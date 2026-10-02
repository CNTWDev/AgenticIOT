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
    if os.environ.get("DEPLOY_TEST_DOCKER_DOWN") and args == ["info"]:
        sys.exit(1)
    if os.environ.get("DEPLOY_TEST_NO_COMPOSE") and args[:2] == ["compose", "version"]:
        sys.exit(1)
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
    for number in range(1, 10):
        assert f"[{number}/9]" in result.stdout
    documented_stages = [
        line for line in (ROOT / "README.md").read_text().splitlines() if "/9] " in line
    ]
    assert len(documented_stages) == 9
    assert all(stage in result.stdout for stage in documented_stages)
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


def test_installer_help_and_sources_are_english(deployment):
    _, run = deployment
    for relative in ("scripts/deploy.sh", "scripts/lib/deploy-environment.sh"):
        assert (ROOT / relative).read_text().isascii()
    result, calls = run("--help")
    assert result.returncode == 0
    assert "Usage:" in result.stdout and "Buildx" in result.stdout
    assert calls == []
    result, calls = run("install", "--unexpected-option")
    assert result.returncode != 0
    assert "ERROR [Validate arguments]: Unknown argument:" in result.stderr
    assert calls == []


def test_environment_check_is_read_only_and_actionable(deployment):
    root, run = deployment
    before = sorted(root.iterdir())
    result, _ = run("check")
    assert result.returncode == 0, result.stderr
    assert sorted(root.iterdir()) == before
    result, _ = run("check", DEPLOY_TEST_NO_COMPOSE="1")
    assert result.returncode != 0 and "--install-docker" in result.stderr
    result, _ = run("install", "--install-docker", DEPLOY_TEST_DOCKER_DOWN="1")
    assert result.returncode != 0
    assert "will not be reinstalled for permission or connection errors" in result.stderr
    assert sorted(root.iterdir()) == before


def test_auto_install_flag_is_explicit_and_install_only(deployment):
    _, run = deployment
    for action in ("check", "upgrade", "status"):
        result, calls = run(action, "--install-docker")
        assert result.returncode != 0
        assert calls == []


@pytest.mark.parametrize("component", ["engine", "plugins"])
@pytest.mark.parametrize("allowed", ["0", "1"])
def test_missing_components_require_opt_in(component, allowed):
    # Isolate ensure_docker wiring: neither real Docker nor a package manager is called.
    script = r"""
set -Eeuo pipefail
die() { echo "$*" >&2; exit 1; }
log() { echo "$*"; }
source "$1"
INSTALL_DOCKER=$3
DOCKER_PRESENT=0
COMPOSE_PRESENT=0
if [[ "$2" == plugins ]]; then DOCKER_PRESENT=1; fi
command() {
    if [[ "$1" == -v && "$2" == docker ]]; then [[ "$DOCKER_PRESENT" == 1 ]];
    else builtin command "$@"; fi
}
docker() {
    case "$*" in
        info) return 0 ;;
        'compose version'*) [[ "$COMPOSE_PRESENT" == 1 ]] ;;
        'buildx version') return 0 ;;
        'compose up --help') echo '--wait-timeout' ;;
        *) return 1 ;;
    esac
}
install_docker_packages() {
    echo "INSTALL:$1"
    DOCKER_PRESENT=1
    COMPOSE_PRESENT=1
}
ensure_docker
"""
    result = subprocess.run(
        [
            "bash",
            "-c",
            script,
            "test-ensure",
            str(ROOT / "scripts/lib/deploy-environment.sh"),
            component,
            allowed,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if allowed == "1":
        assert result.returncode == 0, result.stderr
        assert f"INSTALL:{component}" in result.stdout
    else:
        assert result.returncode != 0
        assert "INSTALL:" not in result.stdout
        assert "--install-docker" in result.stderr


PACKAGE_FAKE = r"""
import json, os, pathlib, sys
name, args = pathlib.Path(sys.argv[0]).name, sys.argv[1:]
with open(os.environ["DEPLOY_TEST_LOG"], "a") as log:
    log.write(json.dumps([name, *args]) + "\n")
if name == "id":
    print(os.environ.get("DEPLOY_TEST_UID", "0"))
elif name == "dpkg":
    print(os.environ.get("DEPLOY_TEST_ARCH", "amd64"))
elif name == "dpkg-query":
    if args[-1] in os.environ.get("DEPLOY_TEST_PACKAGES", "").split(","):
        print("install ok installed")
    else:
        sys.exit(1)
elif name == "curl":
    pathlib.Path(args[-1]).write_text("test-only signing key")
elif name == "apt-get" and os.environ.get("DEPLOY_TEST_APT_FAIL"):
    sys.exit(42)
elif name == "systemctl" and os.environ.get("DEPLOY_TEST_NO_SYSTEMD"):
    sys.exit(1)
"""


@pytest.fixture
def package_installer(tmp_path):
    commands = tmp_path / "bin"
    commands.mkdir()
    (commands / "python3").symlink_to(sys.executable)
    for name in ("id", "dpkg", "dpkg-query", "curl", "apt-get", "systemctl"):
        executable = commands / name
        executable.write_text("#!/usr/bin/env python3\n" + PACKAGE_FAKE)
        executable.chmod(0o700)
    apt_root = tmp_path / "apt"
    (apt_root / "sources.list.d").mkdir(parents=True)
    release = tmp_path / "os-release"
    log = tmp_path / "commands.jsonl"

    def run(mode="engine", distro="ubuntu", version="24.04", codename="noble", **env):
        release.write_text(f"ID={distro}\nVERSION_ID={version}\nVERSION_CODENAME={codename}\n")
        log.write_text("")
        result = subprocess.run(
            [
                "bash",
                "-c",
                'set -Eeuo pipefail; die() { echo "$*" >&2; exit 1; }; '
                'log() { echo "$*"; }; source "$1"; install_docker_packages "$2" "$3" "$4"',
                "test-installer",
                str(ROOT / "scripts/lib/deploy-environment.sh"),
                mode,
                str(release),
                str(apt_root),
            ],
            env=os.environ
            | {
                "PATH": f"{commands}:{os.environ['PATH']}",
                "DEPLOY_TEST_LOG": str(log),
            }
            | env,
            capture_output=True,
            text=True,
            check=False,
        )
        return result, [json.loads(line) for line in log.read_text().splitlines()]

    return apt_root, run


@pytest.mark.parametrize(
    ("distro", "version", "codename", "arch"),
    [
        ("ubuntu", "22.04", "jammy", "amd64"),
        ("ubuntu", "24.04", "noble", "arm64"),
        ("debian", "12", "bookworm", "amd64"),
        ("debian", "13", "trixie", "arm64"),
    ],
)
def test_official_docker_install_plan(package_installer, distro, version, codename, arch):
    apt_root, run = package_installer
    result, calls = run(distro=distro, version=version, codename=codename, DEPLOY_TEST_ARCH=arch)
    assert result.returncode == 0, result.stderr
    source = (apt_root / "sources.list.d/agenticiot-docker.sources").read_text()
    assert f"https://download.docker.com/linux/{distro}" in source
    assert f"Suites: {codename}" in source and f"Architectures: {arch}" in source
    assert (apt_root / "keyrings/agenticiot-docker.asc").stat().st_mode & 0o777 == 0o644
    assert any(c[0] == "apt-get" and "docker-ce" in c for c in calls)
    assert calls[-1] == ["systemctl", "enable", "--now", "docker"]
    assert not any("remove" in c or "purge" in c for c in calls)


def test_plugin_install_preserves_existing_engine_and_source(package_installer):
    apt_root, run = package_installer
    source = apt_root / "sources.list.d/docker.sources"
    original = "URIs: https://download.docker.com/linux/ubuntu\n"
    source.write_text(original)
    result, calls = run(mode="plugins", DEPLOY_TEST_PACKAGES="docker-ce")
    assert result.returncode == 0, result.stderr
    assert source.read_text() == original
    assert not (apt_root / "sources.list.d/agenticiot-docker.sources").exists()
    installs = [c for c in calls if c[0] == "apt-get" and "install" in c]
    assert installs == [
        [
            "apt-get",
            "install",
            "-y",
            "--no-upgrade",
            "docker-buildx-plugin",
            "docker-compose-plugin",
        ]
    ]
    assert not any("enable" in c or c[0] == "curl" for c in calls)


@pytest.mark.parametrize(
    "env",
    [
        {"DEPLOY_TEST_UID": "1000"},
        {"DEPLOY_TEST_ARCH": "s390x"},
        {"DEPLOY_TEST_PACKAGES": "docker.io"},
        {"DEPLOY_TEST_PACKAGES": "containerd"},
        {"DEPLOY_TEST_NO_SYSTEMD": "1"},
        {"distro": "fedora", "version": "43", "codename": "unsupported"},
    ],
)
def test_unsafe_installations_are_rejected_before_package_changes(package_installer, env):
    apt_root, run = package_installer
    result, calls = run(**env)
    assert result.returncode != 0
    assert not any(c[0] in {"apt-get", "curl"} for c in calls)
    assert not (apt_root / "sources.list.d/agenticiot-docker.sources").exists()


def test_apt_failure_does_not_continue_to_service_start(package_installer):
    _, run = package_installer
    result, calls = run(DEPLOY_TEST_APT_FAIL="1")
    assert result.returncode == 42
    assert not any(c[0] == "curl" or "enable" in c for c in calls)
