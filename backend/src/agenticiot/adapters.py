"""Minimal Edge-local contract. No platform database, user identity or Agent planning."""

import json
import sqlite3
from datetime import UTC, datetime
from typing import Protocol


class AdapterError(RuntimeError):
    """No trustworthy observation is available; never manufacture fresh state."""


class Adapter(Protocol):
    id: str
    # True ONLY when device state participates in the Edge journal's SQLite transaction.
    journal_atomic: bool

    def read(self, thing_id: str) -> dict: ...

    def invoke(self, command: dict) -> dict: ...

    def close(self) -> None: ...


def light_change(command):
    """Validate the local light allowlist even after platform authorization."""
    value = command["input"].get("value")
    prop = {"set_power": "power", "set_brightness": "brightness"}.get(command["action"])
    valid = (prop == "power" and type(value) is bool) or (
        prop == "brightness"
        and type(value) in (int, float)
        and 0 <= value <= 100
        and value == int(value)
    )
    if not valid or set(command["input"]) != {"value"} or command.get("simulated") is not True:
        raise AdapterError("Unsupported simulated light command")
    return prop, int(value) if prop == "brightness" else value


class VirtualLightAdapter:
    id = "virtual-light-v1"
    journal_atomic = True

    def __init__(self, journal: sqlite3.Connection):
        self.db = journal

    def read(self, thing_id):
        self.db.execute(
            "INSERT OR IGNORE INTO lights VALUES (?, ?)",
            (thing_id, json.dumps({"power": False, "brightness": 0})),
        )
        state = json.loads(
            self.db.execute("SELECT state FROM lights WHERE thing_id=?", (thing_id,)).fetchone()[0]
        )
        return {"values": state, "observed_at": datetime.now(UTC).isoformat()}

    def invoke(self, command):
        prop, value = light_change(command)
        row = self.db.execute(
            "SELECT state FROM lights WHERE thing_id=?", (command["thing_id"],)
        ).fetchone()
        if row is None:
            raise AdapterError("Light has not been read/bound")
        state = json.loads(row[0])
        state[prop] = value
        self.db.execute(
            "UPDATE lights SET state=? WHERE thing_id=?", (json.dumps(state), command["thing_id"])
        )
        return {"values": state, "observed_at": datetime.now(UTC).isoformat()}

    def close(self):
        pass  # Journal ownership belongs to EdgeRuntime.
