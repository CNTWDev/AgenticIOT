"""Outbound Node client with one journal owner and bounded device/service concurrency."""

import argparse
import asyncio
import fcntl
import json
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from agenticiot.adapters import AdapterError, light_change
from agenticiot.edge import EdgeRuntime


class NodeRuntime:
    def __init__(self, directory, *, mqtt_port=None, mqtt_namespace="local-demo", services=None):
        self.directory = directory
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="node-journal")
        self.worker = None
        self.mqtt_port, self.mqtt_namespace = mqtt_port, mqtt_namespace
        self.services = services or {}
        self.control, self.data = asyncio.Queue(64), asyncio.Queue(32)
        self.bindings, self.jobs, self.calls = {}, {}, {}
        self.device_slots, self.read_slots = asyncio.Semaphore(8), asyncio.Semaphore(4)
        self.locks = {}

    async def journal(self, fn, *args):
        return await asyncio.get_running_loop().run_in_executor(self.executor, fn, *args)

    async def start(self):
        def initialize():
            adapters = []
            if self.mqtt_port:
                from agenticiot.mqtt_adapter import MQTTLightAdapter

                adapters.append(MQTTLightAdapter(self.mqtt_port, self.mqtt_namespace))
            return EdgeRuntime(self.directory / "journal.sqlite3", None, adapters=adapters)

        self.worker = await self.journal(initialize)

        def quarantine_table():
            with self.worker.db:
                self.worker.db.execute(
                    "CREATE TABLE IF NOT EXISTS rejected_outbox "
                    "(sequence INTEGER PRIMARY KEY, code TEXT NOT NULL)"
                )

        await self.journal(quarantine_table)

    async def stop(self):
        await asyncio.gather(*self.jobs.values(), return_exceptions=True)
        for task in self.calls.values():
            task.cancel()
        await asyncio.gather(*self.calls.values(), return_exceptions=True)
        await self.journal(self.worker.close)
        self.executor.shutdown(wait=True)

    async def emit(self, message, *, data=False):
        await asyncio.wait_for((self.data if data else self.control).put(message), 2)

    def prepare(self, command):
        previous = self.worker.db.execute(
            "SELECT result FROM jobs WHERE command_id=?", (command["id"],)
        ).fetchone()
        if previous:
            with self.worker.db:
                self.worker.queue(f"/edge/commands/{command['id']}/result", json.loads(previous[0]))
            return False
        if self.worker.db.execute(
            "SELECT 1 FROM intents WHERE command_id=?", (command["id"],)
        ).fetchone():
            with self.worker.db:
                self.worker.record(
                    command["id"],
                    f"/edge/commands/{command['id']}/result",
                    {"outcome": "failed", "error_code": "execution_uncertain"},
                )
            return False
        adapter = self.worker.adapters.get(command["adapter"])
        if adapter is None or command.get("simulated") is not True:
            raise AdapterError("Unsupported binding")
        light_change(command)
        if datetime.fromisoformat(command["deadline"]) <= datetime.now(UTC):
            with self.worker.db:
                self.worker.record(
                    command["id"],
                    f"/edge/commands/{command['id']}/result",
                    {"outcome": "failed", "error_code": "deadline_elapsed"},
                )
            return False
        if adapter.journal_atomic:
            with self.worker.db:
                adapter.read(command["thing_id"])
                result = {"outcome": "succeeded", **adapter.invoke(command)}
                self.worker.record(command["id"], f"/edge/commands/{command['id']}/result", result)
            return False
        with self.worker.db:
            self.worker.db.execute("INSERT INTO intents VALUES (?)", (command["id"],))
        return True

    async def execute(self, command):
        thing_id = command["thing_id"]
        async with self.device_slots, self.locks.setdefault(thing_id, asyncio.Lock()):
            try:
                if not await self.journal(self.prepare, command):
                    return
                adapter = self.worker.adapters[command["adapter"]]
                try:
                    result = {
                        "outcome": "succeeded",
                        **await asyncio.to_thread(adapter.invoke, command),
                    }
                except AdapterError:
                    result = {"outcome": "failed", "error_code": "execution_uncertain"}

                def record():
                    with self.worker.db:
                        self.worker.record(
                            command["id"], f"/edge/commands/{command['id']}/result", result
                        )

                await self.journal(record)
            except AdapterError:
                # Unsupported input is never executed; leave intent evidence intact.
                return

    async def sample(self, binding):
        async with self.read_slots:
            adapter = self.worker.adapters.get(binding["adapter"])
            if adapter is None:
                return
            try:
                if adapter.journal_atomic:

                    def read():
                        with self.worker.db:
                            return adapter.read(binding["thing_id"])

                    observation = await self.journal(read)
                else:
                    observation = await asyncio.to_thread(adapter.read, binding["thing_id"])

                def record():
                    with self.worker.db:
                        self.worker.queue(
                            f"/edge/things/{binding['thing_id']}/observations",
                            observation,
                            observation=True,
                        )

                await self.journal(record)
            except AdapterError:
                pass  # No fabricated observation or freshness timestamp.

    async def infer(self, message):
        call_id = message["invocation_id"]
        try:
            config = self.services[message["local_ref"]]
            body = message["request"]
            from agenticiot.services.api import ChatRequest

            # Validate payload independently; validate model against the local allowlist separately.
            ChatRequest.model_validate(body | {"model": "local"})
            if body["model"] not in config["models"]:
                raise ValueError("Model not approved")
            url = config["url"]
            parsed = urlsplit(url)
            if (
                parsed.scheme != "http"
                or parsed.hostname not in {"127.0.0.1", "::1"}
                or parsed.path != "/v1/chat/completions"
                or parsed.query
                or parsed.fragment
                or parsed.username
                or parsed.password
            ):
                raise ValueError("Only configured numeric-loopback completion endpoints allowed")
            finished, usage, buffer, total_bytes = None, None, b"", 0
            async with (
                asyncio.timeout(60),
                httpx.AsyncClient(
                    trust_env=False, follow_redirects=False, timeout=httpx.Timeout(15, connect=3)
                ) as client,
                client.stream("POST", url, json=body) as response,
            ):
                response.raise_for_status()
                # No fixed-size buffer: a small first token must flush immediately.
                async for part in response.aiter_bytes():
                    total_bytes += len(part)
                    buffer += part
                    if total_bytes > 2 * 1024 * 1024 or len(buffer) > 32768:
                        raise ValueError("Upstream stream limit exceeded")
                    while b"\n" in buffer:
                        line, buffer = buffer.split(b"\n", 1)
                        if not line.startswith(b"data:"):
                            continue
                        payload = line[5:].strip()
                        if payload == b"[DONE]":
                            if finished is None:
                                raise ValueError("Missing completion reason")
                            await self.emit(
                                {
                                    "type": "complete",
                                    "invocation_id": call_id,
                                    "finish_reason": finished,
                                    "usage": usage,
                                },
                                data=True,
                            )
                            return
                        chunk = json.loads(payload)
                        if not isinstance(chunk, dict) or not isinstance(
                            chunk.get("choices", []), list
                        ):
                            raise ValueError("Invalid stream object")
                        if chunk.get("usage") is not None:
                            usage = chunk["usage"]
                        for choice in chunk.get("choices", []):
                            if not isinstance(choice, dict):
                                raise ValueError("Invalid choice")
                            if choice.get("index", 0) != 0:
                                raise ValueError("Multiple choices unsupported")
                            delta = choice.get("delta", {})
                            if not isinstance(delta, dict):
                                raise ValueError("Invalid delta")
                            if delta.get("tool_calls") or delta.get("function_call"):
                                raise ValueError("Tool generation not in this profile")
                            content = delta.get("content")
                            if content:
                                if not isinstance(content, str) or len(content.encode()) > 16384:
                                    raise ValueError("Invalid content")
                                await self.emit(
                                    {"type": "delta", "invocation_id": call_id, "content": content},
                                    data=True,
                                )
                            if choice.get("finish_reason") is not None:
                                finished = choice["finish_reason"]
                                if finished not in {"stop", "length"}:
                                    raise ValueError("Unsupported finish reason")
                raise ValueError("Truncated upstream stream")
        except (KeyError, ValueError, TypeError, httpx.HTTPError, TimeoutError):
            await self.emit({"type": "inference_error", "invocation_id": call_id}, data=True)

    async def run_connection(self, url, token):
        # Prevent replay of stale prompt/delta buffers after a connection loss.
        self.control, self.data = asyncio.Queue(64), asyncio.Queue(32)
        async with connect(
            url,
            additional_headers={"Authorization": f"Bearer {token}"},
            max_size=262144,
            max_queue=32,
            ping_interval=5,
            ping_timeout=10,
            proxy=None,
        ) as ws:
            await ws.send(
                json.dumps(
                    {
                        "type": "hello",
                        "version": 1,
                        "registration": {
                            "title": "Node Runtime",
                            "adapters": list(self.worker.adapters),
                        },
                    }
                )
            )
            welcome = json.loads(await asyncio.wait_for(ws.recv(), 10))
            if welcome.get("type") != "welcome":
                raise ValueError("Node handshake rejected")

            def identity():
                old = self.worker.db.execute("SELECT edge_id FROM identity").fetchone()
                if old and old[0] != welcome["node_id"]:
                    raise ValueError("Journal belongs to another Node")
                with self.worker.db:
                    self.worker.db.execute(
                        "INSERT OR IGNORE INTO identity VALUES (?)", (welcome["node_id"],)
                    )

            await self.journal(identity)

            async def writer():
                while True:
                    try:
                        item = self.control.get_nowait()
                    except asyncio.QueueEmpty:
                        try:
                            item = await asyncio.wait_for(self.data.get(), 0.05)
                        except TimeoutError:
                            continue
                    await asyncio.wait_for(ws.send(json.dumps(item)), 5)

            async def reader():
                async for raw in ws:
                    message = json.loads(raw)
                    kind = message.get("type")
                    if kind == "bindings":
                        self.bindings = {b["thing_id"]: b for b in message["items"]}
                    elif kind == "command":
                        command = message["command"]
                        old = self.jobs.get(command["id"])
                        if old is None or old.done():
                            self.jobs = {k: v for k, v in self.jobs.items() if not v.done()}
                            if len(self.jobs) >= 16:
                                raise ValueError("Node command capacity exceeded")
                            self.jobs[command["id"]] = asyncio.create_task(self.execute(command))
                    elif kind == "ack":

                        def ack(sequence=message["sequence"]):
                            with self.worker.db:
                                self.worker.db.execute(
                                    "DELETE FROM outbox WHERE sequence=?", (sequence,)
                                )

                        await self.journal(ack)
                    elif kind == "reject":

                        def quarantine(seq=message["sequence"], code=message["code"]):
                            with self.worker.db:
                                self.worker.db.execute(
                                    "INSERT OR IGNORE INTO rejected_outbox VALUES (?, ?)",
                                    (seq, code),
                                )

                        # Keep evidence, but allow later records to progress.
                        await self.journal(quarantine)
                    elif kind == "inference":
                        self.calls = {k: v for k, v in self.calls.items() if not v.done()}
                        if len(self.calls) >= 2 or message["invocation_id"] in self.calls:
                            await self.emit(
                                {
                                    "type": "inference_error",
                                    "invocation_id": message["invocation_id"],
                                }
                            )
                        else:
                            self.calls[message["invocation_id"]] = asyncio.create_task(
                                self.infer(message)
                            )
                    elif kind == "cancel":
                        task = self.calls.get(message["invocation_id"])
                        if task:
                            task.cancel()

            async def uploads():
                while True:

                    def pending():
                        return self.worker.db.execute(
                            "SELECT sequence,path,payload FROM outbox WHERE sequence NOT IN "
                            "(SELECT sequence FROM rejected_outbox) ORDER BY sequence LIMIT 64"
                        ).fetchall()

                    for seq, path, payload in await self.journal(pending):
                        parts = path.split("/")
                        result = parts[2] == "commands"
                        await self.emit(
                            {
                                "type": "result" if result else "observation",
                                "sequence": seq,
                                "command_id" if result else "thing_id": parts[3],
                                "data": json.loads(payload),
                            }
                        )
                    await self.emit({"type": "ping"})
                    await asyncio.sleep(0.5)

            async def observations():
                while True:
                    await asyncio.gather(*(self.sample(b) for b in list(self.bindings.values())))
                    await asyncio.sleep(5)

            tasks = [asyncio.create_task(fn()) for fn in (writer, reader, uploads, observations)]
            try:
                done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    task.result()
            finally:
                for task in [*tasks, *self.calls.values()]:
                    task.cancel()
                await asyncio.gather(*tasks, *self.calls.values(), return_exceptions=True)
                self.calls.clear()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="ws://127.0.0.1:8000/v1/nodes/channel")
    parser.add_argument("--data-dir", type=Path, default=Path(".local/node"))
    parser.add_argument("--mqtt-port", type=int)
    parser.add_argument("--mqtt-namespace", default="local-demo")
    parser.add_argument("--services", type=Path, help="Local approved service configuration JSON")
    args = parser.parse_args()
    url = urlsplit(args.url)
    if (
        url.scheme not in {"ws", "wss"}
        or url.username
        or url.password
        or url.query
        or url.fragment
        or url.path != "/v1/nodes/channel"
        or (url.scheme == "ws" and url.hostname not in {"127.0.0.1", "::1", "localhost"})
    ):
        parser.error("Use WSS, or loopback WS for development; credentials belong in environment")
    token = os.environ.get("AGENTICIOT_NODE_TOKEN", "")
    if len(token) < 32:
        parser.error("Set the provisioned AGENTICIOT_NODE_TOKEN")
    services = json.loads(args.services.read_text()) if args.services else {}
    args.data_dir.mkdir(parents=True, exist_ok=True)
    with (args.data_dir / "worker.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)

        async def run():
            node = NodeRuntime(
                args.data_dir,
                mqtt_port=args.mqtt_port,
                mqtt_namespace=args.mqtt_namespace,
                services=services,
            )
            await node.start()
            try:
                while True:
                    try:
                        await node.run_connection(args.url, token)
                    except (OSError, TimeoutError, ConnectionClosed):
                        pass
                    await asyncio.sleep(2)
            finally:
                await node.stop()

        try:
            asyncio.run(run())
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
