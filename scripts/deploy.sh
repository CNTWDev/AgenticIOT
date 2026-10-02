#!/usr/bin/env bash
# Single-host deployment. Never source secrets as shell code; never downgrade a database.
set -Eeuo pipefail
umask 077

REPOSITORY=https://github.com/CNTWDev/AgenticIOT.git
DEPLOY_ROOT=${AGENTICIOT_DEPLOY_DIR:-/opt/agenticiot}
ACTION=${1:-help}
INSTALL_DOCKER=0
TARGET=''
STEP_NUMBER=0
STEP_TOTAL=9
CURRENT_STEP='参数检查'
STARTED_AT=$SECONDS

log() { printf '[%s] %s\n' "$(date '+%H:%M:%S')" "$*"; }
step() { STEP_NUMBER=$((STEP_NUMBER + 1)); CURRENT_STEP=$*; log "[$STEP_NUMBER/$STEP_TOTAL] $CURRENT_STEP"; }
die() { printf 'ERROR [%s]: %s\n' "$CURRENT_STEP" "$*" >&2; exit 1; }
usage() {
  printf '%s\n' \
    'Usage: bash scripts/deploy.sh check|install|upgrade|start|stop|restart|status|logs|backup|rollback SHA' \
    'Optional: install --install-docker (explicitly install missing Docker components on supported apt/systemd hosts).' \
    'Requires Linux, Git, OpenSSL, flock and Docker Engine with Compose v2 (--wait support).' \
    'Default directory: /opt/agenticiot; override with AGENTICIOT_DEPLOY_DIR.' \
    'One installation per host. Services bind only to 127.0.0.1. No package changes without --install-docker.'
}
case "$ACTION" in
  help|-h|--help) usage; exit 0 ;;
  check|install|upgrade|start|stop|restart|status|logs|backup|rollback) ;;
  *) usage; die 'Unknown operation' ;;
esac
shift
if [[ "$ACTION" == rollback ]]; then TARGET=${1:-}; [[ $# == 0 ]] || shift; fi
while [[ $# -gt 0 ]]; do
  case "$1" in
    --install-docker) [[ "$ACTION" == install ]] || die '--install-docker 只允许用于 install。'; INSTALL_DOCKER=1 ;;
    *) die "未知参数：$1" ;;
  esac
  shift
done
case "$ACTION" in
  check) STEP_TOTAL=1 ;;
  rollback) STEP_TOTAL=6 ;;
  start|restart) STEP_TOTAL=4 ;;
  stop|status|logs|backup) STEP_TOTAL=3 ;;
esac
step '检测操作系统、基础工具和 Docker 环境'
trap 'printf "ERROR [%s]: 环境准备失败，请检查上方错误；不会继续部署。\n" "$CURRENT_STEP" >&2' ERR
[[ $(uname -s) == Linux ]] || die 'Run deployment on a Linux server.'
[[ "$DEPLOY_ROOT" == /* && "$DEPLOY_ROOT" != *'/../'* && "$DEPLOY_ROOT" != *'/./'* ]] || die 'Use an absolute dedicated directory.'
case "${DEPLOY_ROOT%/}" in
  ''|/|/opt|/usr|/var|/srv|/tmp|/root|/home|"${HOME:-/root}") die 'Refusing a broad installation directory.' ;;
esac
[[ "$DEPLOY_ROOT" != */ && "$DEPLOY_ROOT" != */.. && "$DEPLOY_ROOT" != */. ]] || die 'Use a normalized path without trailing slash.'
for program in git openssl flock tar realpath; do
  command -v "$program" >/dev/null || die "Install prerequisite: $program (see docs/deployment/linux.md)."
done
SCRIPT_DIRECTORY=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=lib/deploy-environment.sh
source "$SCRIPT_DIRECTORY/lib/deploy-environment.sh"
ensure_docker
if [[ "$ACTION" == check ]]; then log '环境检查通过，未创建部署目录、未安装软件。'; exit 0; fi
step '检查部署目录并获取操作锁'
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
  log '备份数据库及配置，并检查归档目录；大数据库需要更长时间。'
  cp "$CONFIG" "$destination/config.env"
  printf '%s\n' "$RELEASE_SHA" > "$destination/release"
  compose exec -T postgres pg_dump -U agenticiot -d agenticiot -Fc > "$destination/database.dump.partial"
  # Validate the archive directory without exposing database contents in logs.
  compose exec -T postgres pg_restore --list < "$destination/database.dump.partial" >/dev/null
  mv "$destination/database.dump.partial" "$destination/database.dump"
  printf 'Backup (contains secrets): %s\n' "$destination"
}
start_apps() {
  log '启动 API，等待就绪检查（最多 120 秒）。'
  compose up -d --no-deps --no-build --pull never --wait --wait-timeout 120 api
  log '启动管理后台，等待健康检查（最多 120 秒）。'
  compose up -d --no-deps --no-build --pull never --wait --wait-timeout 120 admin
}
finish_release() {
  printf '%s\n' "$RELEASE_SHA" > "$DEPLOY_ROOT/current.new"
  mv "$DEPLOY_ROOT/current.new" "$DEPLOY_ROOT/current"
  mv "$DEPLOY_ROOT/pending" "$DEPLOY_ROOT/last-success"
  printf 'Ready: %s\nAdmin: http://127.0.0.1:5173  API: http://127.0.0.1:8000\n' "$RELEASE_SHA"
  log "操作成功，总耗时 $((SECONDS - STARTED_AT)) 秒；配置位于 ${CONFIG}（不要公开）。"
}
failure() {
  local status=${1:-$?}
  trap - ERR
  printf 'ERROR [%s]: 操作失败，已耗时 %s 秒。\n' "$CURRENT_STEP" "$((SECONDS - STARTED_AT))" >&2
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
    step '从 GitHub 拉取 main 并确定部署版本'
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
    step '生成或保留配置，验证 Compose 配置'
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
    step '构建 API 和后台镜像，现有服务继续运行'
    log '以下为 Docker 构建输出；首次下载和构建可能较慢，不显示虚假的时间百分比。'
    compose build api admin
    step '进入维护窗口并保护现有数据'
    if [[ "$ACTION" == upgrade ]]; then
      current
      printf '%s\n' "$candidate" > "$DEPLOY_ROOT/pending"
      compose stop -t 25 admin api
      backup
      select_release "$candidate"
    else
      log '首次安装，无已有业务数据库需要升级前备份。'
      printf '%s\n' "$candidate" > "$DEPLOY_ROOT/pending"
    fi
    # Never recreate PostgreSQL as part of an application upgrade.
    step '启动数据库并执行迁移'
    compose up -d --no-recreate --wait --wait-timeout 120 postgres
    compose run --rm --no-deps migrate
    if [[ "$ACTION" == install ]]; then
      log '显式初始化试点管理授权，不输出管理令牌。'
      compose run --rm --no-deps api python -m agenticiot.access.service
    fi
    step '启动 API 和管理后台并验证健康状态'
    start_apps
    step '记录成功版本并完成部署'
    finish_release
    ;;
  rollback)
    safe_state
    current
    old=$RELEASE_SHA
    step '验证目标版本、迁移一致性和保留镜像'
    target=$TARGET
    select_release "$target"
    old_schema=$(git --git-dir="$DEPLOY_ROOT/repo.git" rev-parse "$old:backend/migrations")
    target_schema=$(git --git-dir="$DEPLOY_ROOT/repo.git" rev-parse "$target:backend/migrations")
    [[ "$old_schema" == "$target_schema" ]] || die 'Migration history differs. Automatic database downgrade is forbidden; see recovery guide.'
    compose config --quiet
    docker image inspect "agenticiot-api:$target" "agenticiot-admin:$target" >/dev/null
    select_release "$old"
    step '停止应用并备份当前数据'
    printf '%s\n' "$target" > "$DEPLOY_ROOT/pending"
    compose stop -t 25 admin api
    backup
    select_release "$target"
    step '启动回退版本并检查健康状态'
    start_apps
    step '记录回退结果'
    finish_release
    ;;
  backup) step '创建备份'; current; backup ;;
  start|restart)
    safe_state
    current
    step '启动服务并检查健康状态'
    if [[ "$ACTION" == restart ]]; then compose stop -t 25 admin api; fi
    compose up -d --no-recreate --wait --wait-timeout 120 postgres
    start_apps
    step '服务已就绪'
    ;;
  stop) step '停止服务，保留所有数据'; current; compose stop -t 25 admin api postgres ;;
  status) step '显示服务状态'; current; compose ps ;;
  logs) step '显示最近 100 行服务日志'; current; compose logs --tail 100 api admin postgres ;;
esac
