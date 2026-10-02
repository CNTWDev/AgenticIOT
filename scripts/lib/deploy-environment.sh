#!/usr/bin/env bash
# Sourced by deploy.sh. Package changes require an explicit --install-docker flag.

docker_install_help() {
  printf '%s\n' \
    'On supported Ubuntu 22.04/24.04 or Debian 12/13 hosts, run: sudo bash scripts/deploy.sh install --install-docker' \
    'For other systems or conflicting packages, follow: https://docs.docker.com/engine/install/' \
    'Compose plugin instructions: https://docs.docker.com/compose/install/linux/' >&2
}

package_installed() {
  [[ $(dpkg-query -W -f='${Status}' "$1" 2>/dev/null) == 'install ok installed' ]]
}

install_docker_packages() {
  # Optional paths are function-level test seams; deploy.sh always uses /etc defaults.
  local mode=$1 release_file=${2:-/etc/os-release} apt_root=${3:-/etc/apt}
  local ID='' VERSION_ID='' VERSION_CODENAME='' distro codename arch package sources temporary
  [[ $(id -u) == 0 ]] || die 'Automatic installation requires root. Run again with sudo.'
  [[ -r "$release_file" ]] || die 'Cannot identify the Linux distribution. Install Docker manually.'
  # os-release is an administrator-controlled system file, never the application env file.
  # shellcheck disable=SC1090
  . "$release_file"
  case "$ID:$VERSION_ID" in
    ubuntu:22.04) distro=ubuntu; codename=jammy ;;
    ubuntu:24.04) distro=ubuntu; codename=noble ;;
    debian:12) distro=debian; codename=bookworm ;;
    debian:13) distro=debian; codename=trixie ;;
    *) docker_install_help; die "Automatic installation is not supported on $ID ${VERSION_ID}. No system changes were made." ;;
  esac
  [[ "$VERSION_CODENAME" == "$codename" ]] || die 'Distribution codename mismatch. Check the package sources manually.'
  for package in apt-get dpkg dpkg-query systemctl; do
    command -v "$package" >/dev/null || die "Missing ${package}. Follow the official manual installation instructions."
  done
  systemctl show --property=Version --value >/dev/null 2>&1 || die 'Automatic installation requires a systemd host. Install manually in containers or on other init systems.'
  arch=$(dpkg --print-architecture)
  case "$arch" in amd64|arm64) ;; *) die "Automatic installation is not supported on $arch. Follow the official instructions." ;; esac
  for package in docker.io docker-compose docker-compose-v2 docker-doc docker-buildx podman-docker containerd runc; do
    if package_installed "$package"; then
      die "Potentially conflicting package: ${package}. It will not be removed automatically. Review existing containers and data before following the official migration instructions."
    fi
  done
  if [[ "$mode" == plugins ]]; then
    package_installed docker-ce || die 'Existing Docker is not a managed docker-ce installation. Install Compose/Buildx through its original provider; the Engine will not be replaced automatically.'
  elif package_installed docker-ce; then
    die 'docker-ce is installed, but the docker command is unavailable. Fix PATH or repair the existing installation first.'
  fi
  log 'Configure the official Docker apt repository and install missing components. Existing packages and container data will not be removed.'
  # Reuse existing official sources rather than overwriting the administrator's configuration.
  sources=$(grep -rl 'download.docker.com' "$apt_root/sources.list" "$apt_root/sources.list.d" 2>/dev/null || true)
  if [[ -z "$sources" ]]; then
    [[ ! -e "$apt_root/sources.list.d/agenticiot-docker.sources" && ! -e "$apt_root/keyrings/agenticiot-docker.asc" ]] || die 'A source or key file with the same name already exists. Review it manually; it will not be overwritten.'
    log 'Install apt HTTPS prerequisites and download the official Docker signing key.'
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
    log 'An official Docker repository is already configured. Reuse the existing settings.'
  fi
  log 'Refresh package indexes and install Docker components. Duration depends on network access and apt locks.'
  apt-get update
  if [[ "$mode" == engine ]]; then
    apt-get install -y --no-upgrade docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
    log 'Enable and start the newly installed Docker service.'
    systemctl enable --now docker
  else
    apt-get install -y --no-upgrade docker-buildx-plugin docker-compose-plugin
  fi
}

ensure_docker() {
  if ! command -v docker >/dev/null; then
    log 'Docker CLI/Engine was not found.'
    if [[ "$INSTALL_DOCKER" == 1 ]]; then install_docker_packages engine
    else docker_install_help; die 'Docker is not installed. No system changes were made.'; fi
  fi
  if ! docker info >/dev/null 2>&1; then
    docker_install_help
    die 'Docker daemon is unreachable. Check sudo docker info, systemctl status docker, DOCKER_HOST, and the current context. Docker will not be reinstalled for permission or connection errors.'
  fi
  if ! docker compose version >/dev/null 2>&1 || ! docker buildx version >/dev/null 2>&1; then
    log 'Docker is available, but the Compose v2 or Buildx plugin is missing.'
    if [[ "$INSTALL_DOCKER" == 1 ]]; then install_docker_packages plugins
    else docker_install_help; die 'Install Compose v2 and Buildx, or explicitly enable automatic installation.'; fi
  fi
  docker compose version >/dev/null || die 'Compose is still unavailable after installation. Check the plugin path.'
  docker buildx version >/dev/null || die 'Buildx is still unavailable after installation. Check the plugin path.'
  docker compose up --help | grep -- '--wait-timeout' >/dev/null || die 'Compose is outdated: --wait-timeout support is required. Explicitly upgrade the plugin through its original provider.'
  log "Docker is ready; Compose $(docker compose version --short). The existing Engine will not be upgraded automatically."
}
