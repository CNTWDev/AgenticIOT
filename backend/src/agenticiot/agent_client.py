"""Deterministic device-tool adapter. Caller persists turn/operation references, not the LLM."""

import hashlib
import json
from urllib.parse import urlsplit

import httpx


class DeviceTools:
    def __init__(self, base_url, token, turn_ref):
        url = urlsplit(base_url)
        if (
            url.username
            or url.password
            or url.query
            or url.fragment
            or (
                url.scheme != "https"
                and not (url.scheme == "http" and url.hostname in {"127.0.0.1", "::1", "localhost"})
            )
        ):
            raise ValueError("Use HTTPS, or loopback HTTP for development")
        if not isinstance(turn_ref, str) or not 0 < len(turn_ref) <= 200:
            raise ValueError("A stable entry-owned turn reference is required")
        self.turn_ref = turn_ref
        self.http = httpx.Client(
            base_url=base_url,
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
            follow_redirects=False,
            trust_env=False,
        )

    def close(self):
        self.http.close()

    def invoke(self, operation_ref, *, thing_id, action, input):
        if not isinstance(operation_ref, str) or not 0 < len(operation_ref) <= 200:
            raise ValueError("A stable runtime-owned operation reference is required")
        operation_id = hashlib.sha256(
            json.dumps([self.turn_ref, operation_ref]).encode()
        ).hexdigest()
        response = self.http.post(
            "/v1/tools/actions",
            json={
                "operation_id": operation_id,
                "thing_id": thing_id,
                "action": action,
                "input": input,
            },
        )
        response.raise_for_status()
        return response.json()  # accepted/running/unknown are not physical success.
