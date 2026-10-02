#!/usr/bin/env bash
# Sourced by deploy.sh. Package changes require an explicit --install-docker flag.

docker_install_help() {
  printf '%s\n' \
    '处理方案：Ubuntu 22.04/24.04 或 Debian 12/13 可运行 sudo bash scripts/deploy.sh install --install-docker。' \
    '其他系统或已有冲突包请按官方文档处理：https://docs.docker.com/engine/install/' \
    'Compose 插件：https://docs.docker.com/compose/install/linux/' >&2
}

package_installed() {
  [[ $(dpkg-query -W -f='${Status}' "$1" 2>/dev/null) == 'install ok installed' ]]
}

install_docker_packages() {
  # Optional paths are function-level test seams; deploy.sh always uses /etc defaults.
  local mode=$1 release_file=${2:-/etc/os-release} apt_root=${3:-/etc/apt}
  local ID='' VERSION_ID='' VERSION_CODENAME='' distro codename arch package sources temporary
  [[ $(id -u) == 0 ]] || die '自动安装需要 root；请用 sudo 重新执行。'
  [[ -r "$release_file" ]] || die '无法识别发行版，请手动安装 Docker。'
  # os-release is an administrator-controlled system file, never the application env file.
  # shellcheck disable=SC1090
  . "$release_file"
  case "$ID:$VERSION_ID" in
    ubuntu:22.04) distro=ubuntu; codename=jammy ;;
    ubuntu:24.04) distro=ubuntu; codename=noble ;;
    debian:12) distro=debian; codename=bookworm ;;
    debian:13) distro=debian; codename=trixie ;;
    *) docker_install_help; die "未纳入自动安装范围：$ID ${VERSION_ID}；不会修改系统。" ;;
  esac
  [[ "$VERSION_CODENAME" == "$codename" ]] || die '发行版代号不匹配，请手动检查软件源。'
  for package in apt-get dpkg dpkg-query systemctl; do
    command -v "$package" >/dev/null || die "缺少 ${package}；请使用官方手动安装方案。"
  done
  systemctl show --property=Version --value >/dev/null 2>&1 || die '自动安装只支持 systemd 主机；容器或其他 init 系统请手动安装。'
  arch=$(dpkg --print-architecture)
  case "$arch" in amd64|arm64) ;; *) die "暂不自动安装 $arch 架构；请按官方文档配置。" ;; esac
  for package in docker.io docker-compose docker-compose-v2 docker-doc docker-buildx podman-docker containerd runc; do
    if package_installed "$package"; then
      die "发现可能冲突的软件包 ${package}；不会自动卸载，请先评估其容器和数据，再按官方文档迁移。"
    fi
  done
  if [[ "$mode" == plugins ]]; then
    package_installed docker-ce || die '现有 Docker 不是受管的 docker-ce 安装；请从原提供方安装 Compose/Buildx，不自动替换 Engine。'
  elif package_installed docker-ce; then
    die '检测到 docker-ce，但 docker 命令不可用；请先修复 PATH 或现有安装。'
  fi
  log '将配置 Docker 官方 apt 源并安装缺失组件；不会卸载现有软件或清理容器数据。'
  # Reuse existing official sources rather than overwriting the administrator's configuration.
  sources=$(grep -rl 'download.docker.com' "$apt_root/sources.list" "$apt_root/sources.list.d" 2>/dev/null || true)
  if [[ -z "$sources" ]]; then
    [[ ! -e "$apt_root/sources.list.d/agenticiot-docker.sources" && ! -e "$apt_root/keyrings/agenticiot-docker.asc" ]] || die '已有同名源/密钥文件；请人工核对，不覆盖。'
    log '安装 apt HTTPS 依赖并获取 Docker 官方签名密钥。'
    apt-get update
    apt-get install -y --no-upgrade ca-certificates curl
    temporary=$(mktemp -d)
    curl --fail --silent --show-error --location --proto '=https' --proto-redir '=https' \
      --connect-timeout 15 --max-time 120 "https://download.docker.com/linux/$distro/gpg" -o "$temporary/docker.asc"
    install -d -m 0755 "$apt_root/keyrings" "$apt_root/sources.list.d"
    install -m 0644 "$temporary/docker.asc" "$apt_root/keyrings/agenticiot-docker.asc"
    printf 'Types: deb\nURIs: https://download.docker.com/linux/%s\nSuites: %s\nComponents: stable\nArchitectures: %s\nSigned-By: %s/keyrings/agenticiot-docker.asc\n' \
      "$distro" "$codename" "$arch" "$apt_root" > "$temporary/docker.sources"
    install -m 0644 "$temporary/docker.sources" "$apt_root/sources.list.d/agenticiot-docker.sources"
    rm -f "$temporary/docker.asc" "$temporary/docker.sources"
    rmdir "$temporary"
  else
    log '检测到已有 Docker 官方源，沿用现有配置。'
  fi
  log '刷新软件包索引并安装 Docker 组件，耗时取决于网络和 apt 锁。'
  apt-get update
  if [[ "$mode" == engine ]]; then
    apt-get install -y --no-upgrade docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
    log '启用并启动本次新装的 Docker 服务。'
    systemctl enable --now docker
  else
    apt-get install -y --no-upgrade docker-buildx-plugin docker-compose-plugin
  fi
}

ensure_docker() {
  if ! command -v docker >/dev/null; then
    log '未检测到 Docker CLI/Engine。'
    if [[ "$INSTALL_DOCKER" == 1 ]]; then install_docker_packages engine
    else docker_install_help; die 'Docker 未安装；本次未修改系统。'; fi
  fi
  if ! docker info >/dev/null 2>&1; then
    docker_install_help
    die 'Docker 守护进程不可达。请检查 sudo docker info、systemctl status docker、DOCKER_HOST 和当前 context；不会因权限/连接问题重装 Docker。'
  fi
  if ! docker compose version >/dev/null 2>&1 || ! docker buildx version >/dev/null 2>&1; then
    log 'Docker 已可用，但缺少 Compose v2 或 Buildx 插件。'
    if [[ "$INSTALL_DOCKER" == 1 ]]; then install_docker_packages plugins
    else docker_install_help; die '请补齐 Compose v2 和 Buildx，或显式启用自动安装。'; fi
  fi
  docker compose version >/dev/null || die 'Compose 安装后仍不可用，请检查插件路径。'
  docker buildx version >/dev/null || die 'Buildx 安装后仍不可用，请检查插件路径。'
  docker compose up --help | grep -- '--wait-timeout' >/dev/null || die 'Compose 太旧：需要 --wait-timeout 支持；请从原提供方显式升级插件。'
  log "Docker 就绪；$(docker compose version --short)。不会自动升级已有 Engine。"
}
