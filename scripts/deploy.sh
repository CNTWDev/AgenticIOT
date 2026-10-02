#!/usr/bin/env bash
# Single-host deployment. Never source secrets as shell code; never downgrade a database.
set -Eeuo pipefail
umask 077

REPOSITORY=https://github.com/CNTWDev/AgenticIOT.git
DEPLOY_ROOT=${AGENTICIOT_DEPLOY_DIR:-/opt/agenticiot}
ACTION=${1:-help}

die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
usage() {
  printf '%s\n' \
    'Usage: bash scripts/deploy.sh install|upgrade|start|stop|restart|status|logs|backup|rollback SHA' \
    'Requires Linux, Git, OpenSSL, flock and Docker Engine with Compose v2 (--wait support).' \
    'Default directory: /opt/agenticiot; override with AGENTICIOT_DEPLOY_DIR.' \
    'One installation per host. Services bind only to 127.0.0.1. No automatic Docker installation.'
}
case "$ACTION" in
  help|-h|--help) usage; exit 0 ;;
  install|upgrade|start|stop|restart|status|logs|backup|rollback) ;;
  *) usage; die 'Unknown operation' ;;
esac
[[ $(uname -s) == Linux ]] || die 'Run deployment on a Linux server.'
[[ "$DEPLOY_ROOT" == /* && "$DEPLOY_ROOT" != *'/../'* && "$DEPLOY_ROOT" != *'/./'* ]] || die 'Use an absolute dedicated directory.'
case "${DEPLOY_ROOT%/}" in
  ''|/|/opt|/usr|/var|/srv|/tmp|/root|/home|"${HOME:-/root}") die 'Refusing a broad installation directory.' ;;
esac
[[ "$DEPLOY_ROOT" != */ && "$DEPLOY_ROOT" != */.. && "$DEPLOY_ROOT" != */. ]] || die 'Use a normalized path without trailing slash.'
for program in git openssl flock docker tar realpath; do
  command -v "$program" >/dev/null || die "Install prerequisite: $program (see docs/deployment/linux.md)."
done
docker info >/dev/null 2>&1 || die 'Docker daemon unavailable or current user lacks access.'
docker compose version >/dev/null || die 'Docker Compose v2 required.'
docker compose up --help | grep -- '--wait-timeout' >/dev/null || die 'Update Docker Compose: --wait-timeout required.'
[[ ! -L "$DEPLOY_ROOT" ]] || die 'Installation root must not be a symlink.'
if [[ ! -d "$DEPLOY_ROOT" ]]; then
  [[ "$ACTION" == install ]] || die 'Not installed.'
  mkdir -p "$DEPLOY_ROOT"
fi
[[ $(realpath "$DEPLOY_ROOT") == "$DEPLOY_ROOT" ]] || die 'Symlinked parent paths are not supported.'
if [[ ! -f "$DEPLOY_ROOT/.agenticiot" ]]; then
  [[ "$ACTION" == install ]] || die 'Missing installation marker.'
  [[ -z $(ls -A "$DEPLOY_ROOT") ]] || die 'Installation directory is not empty.'
  printf '%s\n' "$REPOSITORY" > "$DEPLOY_ROOT/.agenticiot"
fi
[[ $(<"$DEPLOY_ROOT/.agenticiot") == "$REPOSITORY" ]] || die 'Installation repository mismatch.'
chmod 700 "$DEPLOY_ROOT"
exec 9>"$DEPLOY_ROOT/deploy.lock"
flock -n 9 || die 'Another deployment operation is running.'
mkdir -p "$DEPLOY_ROOT/releases" "$DEPLOY_ROOT/backups"
CONFIG="$DEPLOY_ROOT/config.env"

select_release() {
  RELEASE_SHA=$1
  [[ "$RELEASE_SHA" =~ ^[0-9a-f]{40}$ ]] || die 'Expected a full 40-character commit SHA.'
  RELEASE_DIR="$DEPLOY_ROOT/releases/$RELEASE_SHA"
  [[ -f "$RELEASE_DIR/deploy/compose.linux.yaml" ]] || die 'Release unavailable.'
  export RELEASE_SHA RELEASE_DIR
}
compose() {
  docker compose --project-name agenticiot --env-file "$CONFIG" \
    -f "$RELEASE_DIR/deploy/compose.linux.yaml" "$@"
}
current() {
  [[ -f "$DEPLOY_ROOT/current" ]] || die 'No successful installation yet.'
  select_release "$(<"$DEPLOY_ROOT/current")"
}
safe_state() {
  [[ ! -f "$DEPLOY_ROOT/pending" ]] || die 'Previous deployment interrupted. See recovery instructions before starting or rolling back.'
}
backup() {
  local destination
  destination=$(mktemp -d "$DEPLOY_ROOT/backups/$(date -u +%Y%m%dT%H%M%SZ).XXXXXX")
  cp "$CONFIG" "$destination/config.env"
  printf '%s\n' "$RELEASE_SHA" > "$destination/release"
  compose exec -T postgres pg_dump -U agenticiot -d agenticiot -Fc > "$destination/database.dump.partial"
  # Validate the archive directory without exposing database contents in logs.
  compose exec -T postgres pg_restore --list < "$destination/database.dump.partial" >/dev/null
  mv "$destination/database.dump.partial" "$destination/database.dump"
  printf 'Backup (contains secrets): %s\n' "$destination"
}
start_apps() {
  compose up -d --no-deps --no-build --pull never --wait --wait-timeout 120 api
  compose up -d --no-deps --no-build --pull never --wait --wait-timeout 120 admin
}
finish_release() {
  printf '%s\n' "$RELEASE_SHA" > "$DEPLOY_ROOT/current.new"
  mv "$DEPLOY_ROOT/current.new" "$DEPLOY_ROOT/current"
  mv "$DEPLOY_ROOT/pending" "$DEPLOY_ROOT/last-success"
  printf 'Ready: %s\nAdmin: http://127.0.0.1:5173  API: http://127.0.0.1:8000\n' "$RELEASE_SHA"
}
failure() {
  local status=${1:-$?}
  trap - ERR
  if [[ -f "$DEPLOY_ROOT/pending" ]]; then
    compose stop -t 25 admin api || true
    printf 'Deployment interrupted; apps stopped. Data/configuration retained. See docs/deployment/linux.md recovery.\n' >&2
  fi
  exit "$status"
}
trap failure ERR
trap 'failure 130' INT
trap 'failure 143' TERM

case "$ACTION" in
  install|upgrade)
    safe_state
    if [[ "$ACTION" == install ]]; then
      [[ ! -f "$DEPLOY_ROOT/current" ]] || die 'Already installed; use upgrade.'
      # Do not attach silently to another Compose installation with the same name.
      [[ -z $(docker ps -aq --filter label=com.docker.compose.project=agenticiot) ]] || die 'An agenticiot Compose project already exists.'
      [[ -z $(docker volume ls -q --filter label=com.docker.compose.project=agenticiot) ]] || die 'An agenticiot data volume already exists; recover it explicitly.'
    else
      current
    fi
    if [[ ! -d "$DEPLOY_ROOT/repo.git" ]]; then
      git init --bare "$DEPLOY_ROOT/repo.git"
      git --git-dir="$DEPLOY_ROOT/repo.git" remote add origin "$REPOSITORY"
    fi
    [[ $(git --git-dir="$DEPLOY_ROOT/repo.git" remote get-url origin) == "$REPOSITORY" ]] || die 'Remote mismatch.'
    git --git-dir="$DEPLOY_ROOT/repo.git" fetch origin refs/heads/main
    candidate=$(git --git-dir="$DEPLOY_ROOT/repo.git" rev-parse FETCH_HEAD)
    if [[ -f "$DEPLOY_ROOT/current" && "$candidate" == "$(<"$DEPLOY_ROOT/current")" ]]; then
      printf 'Already at latest main: %s\n' "$candidate"
      exit 0
    fi
    if [[ ! -d "$DEPLOY_ROOT/releases/$candidate" ]]; then
      stage=$(mktemp -d "$DEPLOY_ROOT/releases/.stage.XXXXXX")
      git --git-dir="$DEPLOY_ROOT/repo.git" archive "$candidate" | tar -x -C "$stage"
      mv "$stage" "$DEPLOY_ROOT/releases/$candidate"
    fi
    select_release "$candidate"
    if [[ ! -f "$CONFIG" ]]; then
      [[ "$ACTION" == install ]] || die 'Missing configuration; never regenerate secrets on upgrade.'
      password=$(openssl rand -hex 32)
      credential=$(openssl rand -hex 32)
      hmac_key=$(openssl rand -hex 32)
      printf 'POSTGRES_PASSWORD=%s\nAGENTICIOT_INFERENCE_HMAC_KEY=%s\nAGENTICIOT_TRUSTED_ISSUERS=[]\nAGENTICIOT_API_CLIENTS=[{"token":"%s","subject_ref":"server-admin","domain_ref":"initial-domain","role":"operator"}]\n' \
        "$password" "$hmac_key" "$credential" > "$CONFIG.new"
      mv "$CONFIG.new" "$CONFIG"
      unset password credential hmac_key
    fi
    chmod 600 "$CONFIG"
    compose config --quiet
    # Build before interrupting the running release; retain versioned images for rollback.
    compose build api admin
    if [[ "$ACTION" == upgrade ]]; then
      current
      printf '%s\n' "$candidate" > "$DEPLOY_ROOT/pending"
      compose stop -t 25 admin api
      backup
      select_release "$candidate"
    else
      printf '%s\n' "$candidate" > "$DEPLOY_ROOT/pending"
    fi
    # Never recreate PostgreSQL as part of an application upgrade.
    compose up -d --no-recreate --wait --wait-timeout 120 postgres
    compose run --rm --no-deps migrate
    if [[ "$ACTION" == install ]]; then
      compose run --rm --no-deps api python -m agenticiot.access.service
    fi
    start_apps
    finish_release
    ;;
  rollback)
    safe_state
    current
    old=$RELEASE_SHA
    target=${2:-}
    select_release "$target"
    old_schema=$(git --git-dir="$DEPLOY_ROOT/repo.git" rev-parse "$old:backend/migrations")
    target_schema=$(git --git-dir="$DEPLOY_ROOT/repo.git" rev-parse "$target:backend/migrations")
    [[ "$old_schema" == "$target_schema" ]] || die 'Migration history differs. Automatic database downgrade is forbidden; see recovery guide.'
    compose config --quiet
    docker image inspect "agenticiot-api:$target" "agenticiot-admin:$target" >/dev/null
    select_release "$old"
    printf '%s\n' "$target" > "$DEPLOY_ROOT/pending"
    compose stop -t 25 admin api
    backup
    select_release "$target"
    start_apps
    finish_release
    ;;
  backup) current; backup ;;
  start|restart)
    safe_state
    current
    if [[ "$ACTION" == restart ]]; then compose stop -t 25 admin api; fi
    compose up -d --no-recreate --wait --wait-timeout 120 postgres
    start_apps
    ;;
  stop) current; compose stop -t 25 admin api postgres ;;
  status) current; compose ps ;;
  logs) current; compose logs --tail 100 api admin postgres ;;
esac
