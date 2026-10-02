"""Local-only Adapter host. No real hardware or offline authorization."""

import argparse
import fcntl
import json
import os
import sqlite3
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from agenticiot.adapters import Adapter, AdapterError, VirtualLightAdapter, light_change


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # Never forward an Edge credential to a redirected endpoint.


class Transport:
    def __init__(self, url, token):
        parsed = urlsplit(url)
        if (
            parsed.scheme != "http"
            or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
        ):
            raise ValueError("This virtual runtime only supports a loopback HTTP API origin")
        self.url, self.token = url.rstrip("/"), token
        self.opener = build_opener(ProxyHandler({}), NoRedirect())

    def __call__(self, method, path, data=None):
        body = None if data is None else json.dumps(data, allow_nan=False).encode()
        request = Request(
            self.url + "/v1" + path,
            data=body,
            method=method,
            headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"},
        )
        try:
            with self.opener.open(request, timeout=5) as response:
                return json.load(response)
        except HTTPError as error:
            raise RuntimeError(
                f"Edge channel returned HTTP {error.code}; check configuration"
            ) from None
        except (URLError, TimeoutError):
            raise RuntimeError("Edge channel unavailable; pending records retained") from None


class EdgeRuntime:
    def __init__(self, database, request, *, adapters: list[Adapter] | None = None):
        self.db = sqlite3.connect(database)
        self.request = request
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS identity (edge_id TEXT PRIMARY KEY);
            CREATE TABLE IF NOT EXISTS lights (thing_id TEXT PRIMARY KEY, state TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS jobs (command_id TEXT PRIMARY KEY, result TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS intents (command_id TEXT PRIMARY KEY);
            CREATE TABLE IF NOT EXISTS outbox (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                path TEXT NOT NULL, payload TEXT NOT NULL
            );
        """)
        installed = [VirtualLightAdapter(self.db), *(adapters or [])]
        self.adapters = {adapter.id: adapter for adapter in installed}
        if len(self.adapters) != len(installed):
            raise ValueError("Duplicate adapter ID")

    def close(self):
        for adapter in self.adapters.values():
            adapter.close()
        self.db.close()

    def queue(self, path, payload, *, observation=False):
        cursor = self.db.execute("INSERT INTO outbox(path, payload) VALUES (?, ?)", (path, "{}"))
        if observation:
            payload["source_sequence"] = cursor.lastrowid
        self.db.execute(
            "UPDATE outbox SET payload=? WHERE sequence=?", (json.dumps(payload), cursor.lastrowid)
        )
        return payload

    def flush(self):
        for sequence, path, payload in self.db.execute(
            "SELECT sequence, path, payload FROM outbox ORDER BY sequence"
        ).fetchall():
            self.request("POST", path, json.loads(payload))
            with self.db:
                self.db.execute("DELETE FROM outbox WHERE sequence=?", (sequence,))

    def execute(self, command):
        adapter = self.adapters.get(command.get("adapter", "virtual-light-v1"))
        path = f"/edge/commands/{command['id']}/result"
        previous = self.db.execute(
            "SELECT result FROM jobs WHERE command_id=?", (command["id"],)
        ).fetchone()
        if previous:
            with self.db:
                self.queue(path, json.loads(previous[0]))
            return
        deadline = datetime.fromisoformat(command["deadline"].replace("Z", "+00:00"))
        uncertain = self.db.execute(
            "SELECT 1 FROM intents WHERE command_id=?", (command["id"],)
        ).fetchone()
        if uncertain:
            result = {"outcome": "failed", "error_code": "execution_uncertain"}
        elif deadline <= datetime.now(UTC):
            result = {"outcome": "failed", "error_code": "deadline_elapsed"}
        elif adapter is None:
            result = {"outcome": "failed", "error_code": "adapter_failure"}
        else:
            try:
                light_change(command)
            except AdapterError:
                result = {"outcome": "failed", "error_code": "adapter_failure"}
            else:
                if not adapter.journal_atomic:
                    # Commit BEFORE external I/O. Crash recovery never blindly republishes.
                    with self.db:
                        self.db.execute("INSERT INTO intents VALUES (?)", (command["id"],))
                with self.db:
                    try:
                        result = {"outcome": "succeeded", **adapter.invoke(command)}
                    except AdapterError:
                        result = {
                            "outcome": "failed",
                            "error_code": (
                                "adapter_failure"
                                if adapter.journal_atomic
                                else "execution_uncertain"
                            ),
                        }
                    self.record(command["id"], path, result)
                return
        with self.db:
            self.record(command["id"], path, result)

    def record(self, command_id, path, result):
        result = self.queue(path, result, observation=result["outcome"] == "succeeded")
        self.db.execute("INSERT INTO jobs VALUES (?, ?)", (command_id, json.dumps(result)))

    def tick(self):
        edge = self.request(
            "POST",
            "/edge/register",
            {
                "title": "Local simulated light Edge",
                "version": "0.1.0",
                "adapters": list(self.adapters),
            },
        )
        identity = self.db.execute("SELECT edge_id FROM identity").fetchone()
        if identity and identity[0] != edge["id"]:
            raise RuntimeError("Journal belongs to a different Edge; do not reuse it")
        if not identity:
            with self.db:
                self.db.execute("INSERT INTO identity(edge_id) VALUES (?)", (edge["id"],))
        self.flush()
        bindings = self.request("GET", "/edge/bindings")["items"]
        for binding in bindings:
            adapter = self.adapters.get(binding["adapter"])
            if adapter is None or binding["simulated"] is not True:
                raise RuntimeError("Unsupported binding; execution refused")
            try:
                with self.db:
                    self.queue(
                        f"/edge/things/{binding['thing_id']}/observations",
                        adapter.read(binding["thing_id"]),
                        observation=True,
                    )
            except AdapterError:
                # No synthetic timestamp or state. Continue processing other devices/commands.
                print("Adapter read unavailable; previous observation will age", flush=True)
        self.flush()
        command = self.request("POST", "/edge/commands/claim", {})
        if command is not None:
            self.execute(command)
            self.flush()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--data-dir", type=Path, default=Path(".local/virtual-edge"))
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--mqtt-port", type=int, help="Enable loopback MQTT demo adapter")
    parser.add_argument("--mqtt-namespace", default="local-demo")
    args = parser.parse_args()
    token = os.environ.get("AGENTICIOT_EDGE_TOKEN", "")
    if len(token) < 32:
        parser.error("Set AGENTICIOT_EDGE_TOKEN to an explicitly configured Edge credential")
    transport = Transport(args.url, token)
    args.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (args.data_dir / "worker.lock").open("a") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            parser.error("Another worker is already using this journal")
        adapters = []
        if args.mqtt_port is not None:
            from agenticiot.mqtt_adapter import MQTTLightAdapter

            adapters.append(MQTTLightAdapter(args.mqtt_port, args.mqtt_namespace))
        worker = EdgeRuntime(args.data_dir / "journal.sqlite3", transport, adapters=adapters)
        try:
            while True:
                try:
                    worker.tick()
                except RuntimeError as error:
                    if args.once:
                        raise SystemExit(str(error)) from None
                    print(str(error), flush=True)
                if args.once:
                    break
                time.sleep(5)
        except KeyboardInterrupt:
            pass
        finally:
            worker.close()


if __name__ == "__main__":
    main()
