from agenticiot.main import create_app
from fastapi.testclient import TestClient


def test_liveness_survives_database_failure_and_does_not_leak_secrets():
    def broken_database():
        raise RuntimeError("postgresql://secret-user:secret-password@internal-host")

    with TestClient(create_app(readiness_probe=broken_database)) as client:
        assert client.get("/health/live").status_code == 200
        response = client.get("/health/ready")
    assert response.status_code == 503
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json()["code"] == "database_not_ready"
    assert response.json()["trace_id"] == response.headers["x-trace-id"]
    assert "secret" not in response.text
    assert "internal-host" not in response.text


def test_missing_migration_is_not_ready():
    with TestClient(create_app(readiness_probe=lambda: False)) as client:
        assert client.get("/health/ready").status_code == 503


def test_ready_and_trace_propagation():
    trace = "a" * 32
    with TestClient(create_app(readiness_probe=lambda: True)) as client:
        response = client.get("/health/ready", headers={"traceparent": f"00-{trace}-{'b' * 16}-01"})
    assert response.json()["status"] == "ready"
    assert response.headers["x-trace-id"] == trace


def test_unimplemented_capabilities_are_not_exposed_as_working_endpoints():
    with TestClient(create_app(readiness_probe=lambda: True)) as client:
        response = client.get("/v1/cloud-models")
        schema = client.get("/openapi.json").json()
    assert response.status_code == 404
    assert response.json()["code"] == "http_404"
    assert "/v1/things" in schema["paths"]
    assert "/v1/things/{thing_id}/actions/{action_name}" in schema["paths"]
    assert "/v1/events" in schema["paths"]
    assert "/v1/cloud-models" not in schema["paths"]
    assert "/v1/edge/commands/claim" not in schema["paths"]


def test_invalid_trace_header_is_replaced():
    with TestClient(create_app(readiness_probe=lambda: True)) as client:
        response = client.get("/health/live", headers={"traceparent": "untrusted-input"})
    assert len(response.headers["x-trace-id"]) == 32
    assert response.headers["x-trace-id"] != "untrusted-input"
