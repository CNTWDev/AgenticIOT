"""Deployment control-flow tests; substitutes do not validate Docker or Linux itself."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
OLD = "a" * 40
NEW = "b" * 40
REPOSITORY = "https://github.com/CNTWDev/AgenticIOT.git"

FAKE = r"""
import json, os, pathlib, sys
name = pathlib.Path(sys.argv[0]).name
args = sys.argv[1:]
with open(os.environ["DEPLOY_TEST_LOG"], "a") as log:
    log.write(json.dumps([name, *args]) + "\n")
if name == "uname":
    print("Linux")
elif name == "git":
    if "get-url" in args:
        print("https://github.com/CNTWDev/AgenticIOT.git")
    elif "rev-parse" in args:
        ref = args[-1]
        if ref == "FETCH_HEAD":
            print("b" * 40)
        elif os.environ.get("DEPLOY_TEST_SCHEMA_CHANGE"):
            print(ref[:40])
        else:
            print("same-migration-tree")
elif name == "docker":
    if "--help" in args:
        print("--wait-timeout")
    elif "pg_dump" in args:
        print("fake archive")
    elif "pg_restore" in args:
        assert sys.stdin.read() == "fake archive\n"
    fail = os.environ.get("DEPLOY_TEST_FAIL")
    if fail == "build" and "build" in args:
        sys.exit(42)
    if fail == "migration" and args[-4:] == ["run", "--rm", "--no-deps", "migrate"]:
        sys.exit(42)
    if fail == "health" and "up" in args and args[-1] == "api":
        sys.exit(42)
"""


@pytest.fixture
def deployment(tmp_path):
    root = tmp_path.resolve() / "installation"
    root.mkdir()
    (root / ".agenticiot").write_text(REPOSITORY + "\n")
    (root / "repo.git").mkdir()
    for sha in (OLD, NEW):
        release = root / "releases" / sha / "deploy"
        release.mkdir(parents=True)
        shutil.copy(ROOT / "deploy/compose.linux.yaml", release)
    commands = tmp_path / "bin"
    commands.mkdir()
    (commands / "python3").symlink_to(sys.executable)
    for name in ("uname", "git", "docker", "flock"):
        executable = commands / name
        executable.write_text("#!/usr/bin/env python3\n" + FAKE)
        executable.chmod(0o700)
    log = tmp_path / "commands.jsonl"
    env = os.environ | {
        "PATH": f"{commands}:{os.environ['PATH']}",
        "AGENTICIOT_DEPLOY_DIR": str(root),
        "DEPLOY_TEST_LOG": str(log),
    }

    def run(*args, **overrides):
        log.write_text("")
        result = subprocess.run(
            ["bash", str(ROOT / "scripts/deploy.sh"), *args],
            env=env | overrides,
            capture_output=True,
            text=True,
            check=False,
        )
        calls = [json.loads(line) for line in log.read_text().splitlines()]
        return result, calls

    return root, run


def existing(root):
    (root / "current").write_text(OLD + "\n")
    (root / "config.env").write_text("unchanged-secrets\n")


def test_install_generates_secrets_and_bootstraps_once(deployment):
    root, run = deployment
    result, calls = run("install")
    assert result.returncode == 0, result.stderr
    config = (root / "config.env").read_text()
    assert "POSTGRES_PASSWORD=" in config
    token = json.loads(config.split("AGENTICIOT_API_CLIENTS=", 1)[1])[0]["token"]
    assert len(token) == 64 and token not in result.stdout + result.stderr
    assert (root / "config.env").stat().st_mode & 0o777 == 0o600
    assert any("agenticiot.access.service" in call for call in calls)
    assert (root / "current").read_text().strip() == NEW
    assert not (root / "pending").exists()
    result, calls = run("upgrade")
    assert result.returncode == 0
    assert not any("agenticiot.access.service" in call for call in calls)
    assert (root / "config.env").read_text() == config


def test_upgrade_orders_build_stop_backup_migrate_start(deployment):
    root, run = deployment
    existing(root)
    result, calls = run("upgrade")
    assert result.returncode == 0, result.stderr
    build = next(i for i, c in enumerate(calls) if "build" in c)
    stop = next(i for i, c in enumerate(calls) if "stop" in c)
    backup = next(i for i, c in enumerate(calls) if "pg_dump" in c)
    migrate = next(i for i, c in enumerate(calls) if c[-1] == "migrate")
    start = next(i for i, c in enumerate(calls) if "up" in c and c[-1] == "api")
    assert build < stop < backup < migrate < start
    assert (root / "config.env").read_text() == "unchanged-secrets\n"
    archive = next((root / "backups").glob("*/database.dump"))
    assert archive.read_text() == "fake archive\n"
    assert (archive.parent / "release").read_text().strip() == OLD
    assert not any("agenticiot.access.service" in call for call in calls)


@pytest.mark.parametrize("phase", ["build", "migration", "health"])
def test_upgrade_failure_preserves_current_and_fails_closed(deployment, phase):
    root, run = deployment
    existing(root)
    result, calls = run("upgrade", DEPLOY_TEST_FAIL=phase)
    assert result.returncode != 0
    assert (root / "current").read_text().strip() == OLD
    assert (root / "config.env").read_text() == "unchanged-secrets\n"
    if phase == "build":
        assert not (root / "pending").exists()
        assert not any("stop" in call for call in calls)
    else:
        assert (root / "pending").exists()
        assert "stop" in calls[-1]
        result, calls = run("start")
        assert result.returncode != 0
        assert not any("up" in call and "--help" not in call for call in calls)


def test_rollback_refuses_schema_change_and_accepts_same_schema(deployment):
    root, run = deployment
    existing(root)
    result, calls = run("rollback", NEW, DEPLOY_TEST_SCHEMA_CHANGE="1")
    assert result.returncode != 0
    assert not any("stop" in call for call in calls)
    result, calls = run("rollback", NEW)
    assert result.returncode == 0, result.stderr
    assert (root / "current").read_text().strip() == NEW
    assert not any(call[-1] == "migrate" for call in calls)


def test_refuses_unknown_operation_and_broad_path(deployment):
    _, run = deployment
    result, _ = run("uninstall")
    assert result.returncode != 0
    result, calls = run("install", AGENTICIOT_DEPLOY_DIR="/opt")
    assert result.returncode != 0
    assert not any(c[0] == "docker" for c in calls)
