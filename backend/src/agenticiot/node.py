"""Outbound Node client with one journal owner and bounded device/service concurrency."""

import argparse
import asyncio
import fcntl
import json
import logging
import os
import random
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from websockets.asyncio.client import connect
from websockets.exceptions import WebSocketException

from agenticiot.adapters import AdapterError, light_change
from agenticiot.edge import EdgeRuntime

logger = logging.getLogger(__name__)


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
        self.clock_offset = 0.0

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
                self.worker.db.execute(
                    "CREATE TABLE IF NOT EXISTS deliveries (command_id TEXT PRIMARY KEY)"
                )
                self.worker.db.execute(
                    "CREATE TABLE IF NOT EXISTS tombstones (command_id TEXT PRIMARY KEY)"
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
        if self.worker.db.execute(
            "SELECT 1 FROM tombstones WHERE command_id=?", (command["id"],)
        ).fetchone():
            return False
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
        if datetime.fromisoformat(command["deadline"]) <= datetime.now(UTC) + timedelta(
            seconds=self.clock_offset
        ):
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

    def receive_command(self, command_id):
        with self.worker.db:
            self.worker.db.execute("INSERT OR IGNORE INTO deliveries VALUES (?)", (command_id,))

    def reconcile(self, command_id):
        with self.worker.db:
            result = self.worker.db.execute(
                "SELECT result FROM jobs WHERE command_id=?", (command_id,)
            ).fetchone()
            if result:
                self.worker.queue(f"/edge/commands/{command_id}/result", json.loads(result[0]))
                # A saved uncertain result still needs an execution barrier.
                if json.loads(result[0]).get("error_code") != "execution_uncertain":
                    return
            self.worker.db.execute("INSERT OR IGNORE INTO tombstones VALUES (?)", (command_id,))
            intent = self.worker.db.execute(
                "SELECT 1 FROM intents WHERE command_id=?", (command_id,)
            ).fetchone()
            self.worker.queue(
                f"/edge/commands/{command_id}/reconciled",
                {"outcome": "uncertain" if intent or result else "not_executed"},
            )

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
            if config.get("include_usage") is True:
                body = body | {"stream_options": {"include_usage": True}}
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
            handshake_started = asyncio.get_running_loop().time()
            await ws.send(
                json.dumps(
                    {
                        "type": "hello",
                        "version": 2,
                        "capacity": 16,
                        "node_time": datetime.now(UTC).isoformat(),
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
            if "server_time" in welcome:
                self.clock_offset = (
                    datetime.fromisoformat(welcome["server_time"]) - datetime.now(UTC)
                ).total_seconds()
            logger.info(
                "Node handshake: round_trip_ms=%.1f clock_offset_seconds=%.3f",
                (asyncio.get_running_loop().time() - handshake_started) * 1000,
                self.clock_offset,
            )
            inflight = {}
            upload_wake = asyncio.Event()

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
                                await self.emit({"type": "busy", "command_id": command["id"]})
                                continue
                            await self.journal(self.receive_command, command["id"])
                            await self.emit({"type": "received", "command_id": command["id"]})
                            self.jobs[command["id"]] = asyncio.create_task(self.execute(command))
                    elif kind == "reconcile":
                        task = self.jobs.get(message["command_id"])
                        if task is None or task.done():
                            await self.journal(self.reconcile, message["command_id"])
                    elif kind == "ack":
                        inflight.pop(message["sequence"], None)

                        def ack(sequence=message["sequence"]):
                            with self.worker.db:
                                self.worker.db.execute(
                                    "DELETE FROM outbox WHERE sequence=?", (sequence,)
                                )

                        await self.journal(ack)
                        upload_wake.set()
                    elif kind == "reject":
                        if message["code"] not in {
                            "sequence_conflict",
                            "result_conflict",
                            "invalid_message",
                            "resource_not_found",
                            "invalid_evidence_sequence",
                            "invalid_command_stage",
                        }:
                            raise OSError("Retryable evidence rejection: " + message["code"])
                        inflight.pop(message["sequence"], None)
                        logger.warning(
                            "Evidence quarantined: sequence=%s code=%s",
                            message["sequence"],
                            message["code"],
                        )

                        def quarantine(seq=message["sequence"], code=message["code"]):
                            with self.worker.db:
                                self.worker.db.execute(
                                    "INSERT OR IGNORE INTO rejected_outbox VALUES (?, ?)",
                                    (seq, code),
                                )

                        # Keep evidence, but allow later records to progress.
                        await self.journal(quarantine)
                        upload_wake.set()
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
                last_status = -10.0
                while True:
                    upload_wake.clear()

                    def pending():
                        return self.worker.db.execute(
                            "SELECT sequence,path,payload FROM outbox WHERE sequence NOT IN "
                            "(SELECT sequence FROM rejected_outbox) ORDER BY sequence LIMIT 1"
                        ).fetchall()

                    for seq, path, payload in await self.journal(pending):
                        now = asyncio.get_running_loop().time()
                        if now - inflight.get(seq, -10) < 5:
                            continue
                        parts = path.split("/")
                        result = parts[2] == "commands"
                        await self.emit(
                            {
                                "type": parts[4] if result else "observation",
                                "sequence": seq,
                                "command_id" if result else "thing_id": parts[3],
                                "data": json.loads(payload),
                            }
                        )
                        inflight[seq] = now
                    clock = asyncio.get_running_loop().time()
                    if clock - last_status >= 5:

                        def stats():
                            total = self.worker.db.execute(
                                "SELECT count(*) FROM outbox"
                            ).fetchone()[0]
                            quarantined = self.worker.db.execute(
                                "SELECT count(*) FROM rejected_outbox"
                            ).fetchone()[0]
                            return {"pending": total - quarantined, "quarantined": quarantined}

                        await self.emit({"type": "ping", "journal": await self.journal(stats)})
                        last_status = clock
                    try:
                        await asyncio.wait_for(upload_wake.wait(), 0.5)
                    except TimeoutError:
                        pass

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

    async def run_forever(self, url, token):
        delay = 1.0
        while True:
            started = asyncio.get_running_loop().time()
            try:
                await self.run_connection(url, token)
            except (OSError, TimeoutError, WebSocketException) as error:
                # Never log headers, credentials, URLs or inference content.
                logger.warning("Node reconnect: %s; retry in %.1fs", type(error).__name__, delay)
            if asyncio.get_running_loop().time() - started > 30:
                delay = 1.0
            await asyncio.sleep(delay + random.uniform(0, delay / 4))
            delay = min(delay * 2, 30)


def main():
    logging.basicConfig(level=logging.INFO)
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
                await node.run_forever(args.url, token)
            finally:
                await node.stop()

        try:
            asyncio.run(run())
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
