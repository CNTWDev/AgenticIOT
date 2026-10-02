import json
from datetime import UTC, datetime, timedelta

import pytest
from agenticiot.virtual_edge import NoRedirect, Transport, VirtualEdge


def test_redirect_cannot_forward_edge_credentials():
    assert (
        NoRedirect().redirect_request(None, None, 302, "redirect", {}, "http://elsewhere.invalid")
        is None
    )


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com",
        "http://user:pass@localhost",
        "http://127.0.0.1/path",
        "http://localhost?redirect=1",
    ],
)
def test_virtual_transport_rejects_nonlocal_or_credential_embedded_origins(url):
    with pytest.raises(ValueError):
        Transport(url, "secret-not-sent")


@pytest.mark.parametrize(
    "action,value,expired,simulated,expected",
    [
        ("set_power", True, True, True, "deadline_elapsed"),
        ("unlock", True, False, True, "adapter_failure"),
        ("set_power", True, False, False, "adapter_failure"),
        ("set_brightness", 12.0, False, True, None),
    ],
)
def test_virtual_adapter_fails_closed_and_commits_result_once(
    tmp_path, action, value, expired, simulated, expected
):
    uploaded = []
    worker = VirtualEdge(
        tmp_path / "journal.sqlite3", lambda method, path, data: uploaded.append(data)
    )
    with worker.db:
        worker.db.execute(
            "INSERT INTO lights VALUES (?, ?)",
            ("light", json.dumps({"power": False, "brightness": 0})),
        )
    command = {
        "id": "command",
        "thing_id": "light",
        "action": action,
        "input": {"value": value},
        "simulated": simulated,
        "deadline": (datetime.now(UTC) + timedelta(seconds=-1 if expired else 60)).isoformat(),
    }
    worker.execute(command)
    worker.flush()
    worker.execute(command)
    worker.flush()
    assert uploaded[0] == uploaded[1]
    assert uploaded[0].get("error_code") == expected
    assert worker.db.execute("SELECT count(*) FROM jobs").fetchone()[0] == 1
    state = json.loads(worker.db.execute("SELECT state FROM lights").fetchone()[0])
    assert state == {"power": False, "brightness": 0 if expected else 12}
    worker.close()
