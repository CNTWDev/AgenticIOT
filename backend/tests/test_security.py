import pytest
from agenticiot.config import Settings
from agenticiot.main import create_app
from fastapi.testclient import TestClient
from pydantic import ValidationError

TOKEN = "local-test-only-" + "x" * 32
CLIENT = {
    "token": TOKEN,
    "subject_ref": "operator:test",
    "domain_ref": "home:test",
    "role": "operator",
}


def test_credentials_are_secret_and_invalid_configuration_does_not_echo_inputs():
    settings = Settings(_env_file=None, api_clients=[CLIENT])
    assert TOKEN not in repr(settings)
    assert TOKEN not in repr(settings.api_clients[0])
    with pytest.raises(ValidationError) as error:
        Settings(_env_file=None, api_clients=[CLIENT, CLIENT])
    assert TOKEN not in str(error.value)
    with pytest.raises(ValidationError) as error:
        Settings(_env_file=None, api_clients=[CLIENT | {"role": "superuser"}])
    assert TOKEN not in str(error.value)


def test_authentication_precedes_database_access_and_errors_match_contract():
    settings = Settings(_env_file=None, api_clients=[CLIENT])
    with TestClient(create_app(settings, readiness_probe=lambda: False)) as client:
        denied = client.get("/v1/things", headers={"Authorization": "Bearer incorrect"})
        assert denied.status_code == 401
        assert denied.headers["www-authenticate"] == "Bearer"
        authorized = client.get("/v1/things", headers={"Authorization": f"Bearer {TOKEN}"})
        assert authorized.status_code == 503
        assert authorized.headers["content-type"] == "application/problem+json"
        assert authorized.headers["cache-control"] == "no-store"
        assert authorized.json()["trace_id"] == authorized.headers["x-trace-id"]
        assert TOKEN not in authorized.text
        schema = client.get("/openapi.json").json()
    operation = schema["paths"]["/v1/management/models"]["post"]
    assert operation["security"] == [{"LocalAPICredential": []}]
    assert "application/problem+json" in operation["responses"]["422"]["content"]
