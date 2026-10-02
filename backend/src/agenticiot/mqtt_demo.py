"""Local development broker and simulated device. Never expose this unauthenticated demo."""

import argparse
import asyncio
import fcntl
import json
import signal
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import paho.mqtt.client as mqtt
from pydantic import ValidationError

from agenticiot.adapters import light_change
from agenticiot.mqtt_adapter import LightRequest, LightResponse, topic_root


class DemoLight:
    def __init__(self, database, thing_id):
        self.thing_id = thing_id
        self.db = sqlite3.connect(database)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS light (thing_id TEXT PRIMARY KEY, state TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS applied (
                command_id TEXT PRIMARY KEY, request TEXT NOT NULL, response TEXT NOT NULL
            );
        """)
        existing = self.db.execute("SELECT thing_id FROM light").fetchone()
        if existing and existing[0] != thing_id:
            raise ValueError("Device journal belongs to another thing")
        with self.db:
            self.db.execute(
                "INSERT OR IGNORE INTO light VALUES (?, ?)",
                (thing_id, json.dumps({"power": False, "brightness": 0})),
            )

    def handle(self, payload, retained=False):
        if retained or len(payload) > 4096:
            return None
        try:
            request = LightRequest.model_validate_json(payload)
        except (ValidationError, ValueError):
            return None
        if (
            request.thing_id != self.thing_id
            or not request.issued_at <= datetime.now(UTC) < request.deadline
        ):
            return None
        canonical = request.model_dump_json()
        with self.db:
            if request.operation == "invoke":
                old = self.db.execute(
                    "SELECT request, response FROM applied WHERE command_id=?",
                    (request.request_id,),
                ).fetchone()
                if old:
                    return old[1] if old[0] == canonical else None
            state = json.loads(self.db.execute("SELECT state FROM light").fetchone()[0])
            if request.operation == "invoke":
                prop, value = light_change(request.model_dump())
                state[prop] = value
                self.db.execute("UPDATE light SET state=?", (json.dumps(state),))
            response = LightResponse(
                request_id=request.request_id,
                thing_id=self.thing_id,
                observed_at=datetime.now(UTC),
                values=state,
            ).model_dump_json()
            if request.operation == "invoke":
                # Simulated actuation and dedup evidence are one commit, before publishing.
                self.db.execute(
                    "INSERT INTO applied VALUES (?, ?, ?)",
                    (request.request_id, canonical, response),
                )
            return response

    def close(self):
        self.db.close()


async def broker(port):
    # Dev dependency only: the platform/Edge never embeds or manages a broker.
    from amqtt.broker import Broker

    service = Broker(
        {
            "listeners": {
                "default": {"type": "tcp", "bind": f"127.0.0.1:{port}", "max_connections": 32}
            },
            "plugins": {
                "amqtt.plugins.authentication.AnonymousAuthPlugin": {"allow_anonymous": True}
            },
        }
    )
    stopped = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stopped.set)
    await service.start()
    print(f"READY demo broker 127.0.0.1:{port}", flush=True)
    try:
        await stopped.wait()
    finally:
        await service.shutdown()


def device(args):
    root = topic_root(args.namespace, args.thing_id)
    args.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (args.data_dir / "device.lock").open("a") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        light = DemoLight(args.data_dir / "device.sqlite3", args.thing_id)
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, reconnect_on_failure=False)

        def connected(client, userdata, flags, reason_code, properties):
            if not reason_code.is_failure:
                client.subscribe(root + "/request", qos=1)

        def subscribed(client, userdata, mid, codes, properties):
            if codes and all(not code.is_failure for code in codes):
                print("READY demo device", flush=True)

        def received(client, userdata, message):
            if message.topic == root + "/request":
                response = light.handle(message.payload, message.retain)
                if response:
                    client.publish(root + "/response", response, qos=1, retain=False)

        client.on_connect, client.on_subscribe, client.on_message = connected, subscribed, received
        try:
            client.connect("127.0.0.1", args.port, keepalive=10)
            while client.loop(timeout=0.1) == mqtt.MQTT_ERR_SUCCESS:
                pass
        except KeyboardInterrupt:
            pass
        finally:
            client.disconnect()
            light.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["broker", "device"])
    parser.add_argument("--port", type=int, default=18883)
    parser.add_argument("--namespace", default="local-demo")
    parser.add_argument("--thing-id")
    parser.add_argument("--data-dir", type=Path, default=Path(".local/mqtt-device"))
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("Invalid port")
    if args.mode == "broker":
        asyncio.run(broker(args.port))
    elif not args.thing_id:
        parser.error("--thing-id is required for the device")
    else:
        device(args)


if __name__ == "__main__":
    main()
