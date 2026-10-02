# ruff: noqa: F811
import asyncio
import socket
import threading
import time
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest
import uvicorn
from agenticiot.adapters import AdapterError
from agenticiot.main import create_app
from agenticiot.node import NodeRuntime
from agenticiot.serve import DrainingServer
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from test_access_nodes_services import provisioned, service
from test_registry import TOKEN_A, publish, register, registry_client  # noqa: F401
from test_runtime import virtual_model


@contextmanager
def live_server(app):
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = DrainingServer(uvicorn.Config(app, log_level="error", ws="websockets-sansio"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    try:
        end = time.monotonic() + 5
        while not server.started:
            assert thread.is_alive() and time.monotonic() < end
            time.sleep(0.01)
        yield port
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        sock.close()
        assert not thread.is_alive(), "Test server did not stop"


@pytest.mark.integration
def test_real_websocket_node_burst_events_and_local_stream(registry_client, tmp_path):
    client, _, _ = registry_client
    node_record = provisioned(client)
    service(client, node_record)
    model = publish(client, **virtual_model())
    devices = []
    for index in range(24):
        device = register(client, model, external_ref=f"lamp-{index}").json()
        result = client.put(
            f"/v1/management/devices/{device['id']}/binding", json={"edge_id": node_record["id"]}
        )
        assert result.status_code == 200, result.text
        devices.append(device)

    upstream = FastAPI()

    @upstream.post("/v1/chat/completions")
    async def complete():
        async def chunks():
            yield (
                'data: {"choices":[{"delta":{"content":"local response"},'
                '"finish_reason":null}]}\n\n'
            )
            yield 'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n'
            yield "data: [DONE]\n\n"

        return StreamingResponse(chunks(), media_type="text/event-stream")

    with (
        live_server(upstream) as model_port,
        live_server(create_app(client.app.state.settings)) as port,
    ):

        async def run():
            node = NodeRuntime(
                tmp_path,
                services={
                    "ollama": {
                        "url": f"http://127.0.0.1:{model_port}/v1/chat/completions",
                        "models": ["tiny"],
                    }
                },
            )
            await node.start()
            runner = asyncio.create_task(
                node.run_connection(f"ws://127.0.0.1:{port}/v1/nodes/channel", node_record["token"])
            )
            try:
                async with httpx.AsyncClient(
                    base_url=f"http://127.0.0.1:{port}",
                    headers={"Authorization": f"Bearer {TOKEN_A}"},
                    timeout=10,
                ) as http:
                    async with asyncio.timeout(10):
                        while not (await http.get("/v1/management/nodes")).json()["items"][0][
                            "online"
                        ]:
                            if runner.done():
                                runner.result()
                            await asyncio.sleep(0.05)
                    # Baseline observations are needed to infer sampled changes.
                    async with asyncio.timeout(10):
                        while (await http.get(f"/v1/things/{devices[-1]['id']}/state")).json()[
                            "properties"
                        ].get("power", {}).get("value") is None:
                            if runner.done():
                                runner.result()
                            await asyncio.sleep(0.05)
                    started = time.monotonic()
                    commands = await asyncio.gather(
                        *(
                            http.post(
                                "/v1/tools/actions",
                                json={
                                    "thing_id": device["id"],
                                    "action": "set_power",
                                    "input": {"value": True},
                                    "operation_id": f"intent-{index}",
                                },
                            )
                            for index, device in enumerate(devices)
                        )
                    )
                    assert all(c.status_code == 202 for c in commands)
                    async with asyncio.timeout(10):
                        while True:
                            states = await asyncio.gather(
                                *(http.get(f"/v1/commands/{c.json()['id']}") for c in commands)
                            )
                            if all(s.json()["status"] == "succeeded" for s in states):
                                break
                            if runner.done():
                                runner.result()
                            await asyncio.sleep(0.05)
                    assert time.monotonic() - started < 10  # regression bound, not network SLA
                    events = (await http.get("/v1/events/page")).json()
                    assert len(events["items"]) == 24
                    assert {e["source_kind"] for e in events["items"]} == {"sampled_change"}
                    replay = (
                        await http.get("/v1/events/page", params={"cursor": events["cursor"]})
                    ).json()
                    assert replay["items"] == []
                    async with http.stream(
                        "GET", "/v1/events", headers={"Last-Event-ID": events["items"][0]["id"]}
                    ) as stream:
                        assert stream.status_code == 200
                        async for line in stream.aiter_lines():
                            if line.startswith("id: "):
                                assert line[4:] == events["items"][1]["id"]
                                break
                        else:
                            raise AssertionError("No replayed SSE event")
                    response = await http.post(
                        "/v1/chat/completions",
                        json={
                            "model": "home-model",
                            "messages": [{"role": "user", "content": "hello"}],
                        },
                    )
                    assert response.status_code == 200, response.text
                    assert "local response" in response.text and "[DONE]" in response.text
            finally:
                runner.cancel()
                await asyncio.gather(runner, return_exceptions=True)
                await node.stop()

        asyncio.run(run())


def test_thirteen_blocked_reads_do_not_hold_seven_commands_and_restart_is_deduped(tmp_path):
    class Offline:
        journal_atomic = False

        def read(self, _thing):
            time.sleep(0.3)
            raise AdapterError("device unavailable")

    async def run():
        node = NodeRuntime(tmp_path)
        await node.start()
        node.worker.adapters["offline"] = Offline()
        commands = [
            {
                "id": uuid4().hex,
                "thing_id": uuid4().hex,
                "adapter": "virtual-light-v1",
                "action": "set_power",
                "input": {"value": True},
                "simulated": True,
                "deadline": (datetime.now(UTC) + timedelta(seconds=30)).isoformat(),
            }
            for _ in range(7)
        ]
        reads = [
            asyncio.create_task(node.sample({"thing_id": str(i), "adapter": "offline"}))
            for i in range(13)
        ]
        try:
            await asyncio.sleep(0.02)
            await asyncio.wait_for(asyncio.gather(*(node.execute(c) for c in commands)), 1)
            assert any(not r.done() for r in reads)
            count = await node.journal(
                lambda: node.worker.db.execute("SELECT count(*) FROM jobs").fetchone()[0]
            )
            assert count == 7
        finally:
            await asyncio.gather(*reads)
            del node.worker.adapters["offline"]
            await node.stop()
        restored = NodeRuntime(tmp_path)
        await restored.start()
        try:
            await restored.execute(commands[0])
            assert (
                await restored.journal(
                    lambda: restored.worker.db.execute("SELECT count(*) FROM jobs").fetchone()[0]
                )
                == 7
            )
        finally:
            await restored.stop()

    asyncio.run(run())
