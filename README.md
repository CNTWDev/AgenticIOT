# AgenticIoT

AgenticIoT is a device management and Node-local service access platform for AI agents. It gives applications auditable interfaces to discover devices, execute actions, subscribe to captured events, and reach approved model services inside home or enterprise networks through outbound Node connections.

The platform handles resource authorization, device connectivity, and execution evidence. Application backends and Agent Runtimes own planning, memory, user relationships, and cloud-model policies. The design follows **Less is More**: a focused device platform, not another general-purpose AI gateway.

**Status: software pilot, not a production-ready release.** Implemented capabilities include simulated devices, a simulated-device MQTT integration, Nodes, local-service streaming, an administration console, and automated tests. Physical hardware, individual model compatibility, household-network latency, and production security still require validation. The licensing strategy is AGPLv3 plus a separate commercial license; see [Licensing and commercial use](#licensing-and-commercial-use) for the status of the formal licensing documents.

## Architecture

```text
Mobile apps / Smart speakers / Web applications
                       |
Trusted application backend + Agent Runtime
  Accounts, intent, confirmation, memory, cloud-model policy
                       | HTTPS / Bearer tokens / Event SSE
                       v
AgenticIoT modular monolith + Admin console
  Access       Internal domains, identity mappings, effective permissions
  Registry     Device models, capability schemas, resource registration
  Nodes        Machine identities, activation, WSS sessions and leases
  Runtime      Asynchronous commands, execution evidence, unknown results
  AI Services  Approved Node-local services, request-scoped streaming
  Tools/Events Typed HTTP tools, sampled events with replay cursors
          |                         ^
     PostgreSQL                     | Outbound WSS connection
                                    |
                          Home / Enterprise Node
                            +-- Adapters --> Sensors and actuators
                            +-- Allowlist -> Local model services
```

A compute-capable Node exposes services such as model inference; it is not a GPU/NPU resource pool. Devices and services belong to an internal `domain_id`. Application backends manage human accounts, while Nodes use independent machine credentials rather than user administration tokens.

### Core boundaries

- **Authorize before activation.** Effective permissions are the intersection of the entry application's claims and the platform's domain grants. Registration does not establish device connectivity, and local services require explicit approval after registration.
- **Inference never directly controls hardware.** Generation and device commands have separate lifecycles. Deterministic code, authorization checks, and execution interfaces mediate physical actions.
- **Unknown does not mean failed.** Unknown outcomes are not automatically retried, and outputs from different models are not stitched together. The Agent Runtime manages business-operation identifiers; the LLM does not invent them.
- **Local execution does not guarantee local data retention.** Relayed inputs pass through the platform server. A service declared local is not proof of network-egress isolation.
- **Events reflect the limits of observation.** Current events describe changes inferred from sampled state, not a complete record of physical activity between samples. The Agent Runtime consumes events and activates agents.

The backend uses Python 3.13, FastAPI, SQLAlchemy, and PostgreSQL 18. The administration console uses React, TypeScript, and Vite. The first deployment model is single-instance, without message middleware, cross-instance streaming, payments, GPU scheduling, or cloud-model fallback. The current tool interface is HTTP; it is not an MCP implementation.

See the [v0.3 technical architecture](docs/architecture/device-node-services-v0.3.md), [account and resource activation decision](docs/adr/0010-account-resource-activation.md), and [asynchronous runtime decision](docs/adr/0011-async-node-runtime.md).

## Linux installation

Use a dedicated Ubuntu 24.04 LTS server as the recommended starting point. Prerequisites are Git, OpenSSL, util-linux, GNU coreutils, tar, Docker Engine, Compose v2, and Buildx. Builds require access to GitHub, container registries, and Python and npm package repositories. The installer does not provision household Nodes, download models, or open firewall ports.

On Ubuntu or Debian, install any missing base tools and clone the repository:

```bash
sudo apt-get update
sudo apt-get install -y git openssl util-linux coreutils tar
git clone https://github.com/CNTWDev/AgenticIOT.git
cd AgenticIOT
```

### Check the environment

```bash
bash scripts/deploy.sh check
# Retry with sudo if your user cannot access the Docker socket.
sudo bash scripts/deploy.sh check
```

`check` does not install packages or create a deployment directory. It distinguishes a missing Docker installation, an unreachable daemon or insufficient permissions, missing Compose/Buildx plugins, and an outdated Compose version, with guidance for each case. It does not reinstall an unreachable Docker daemon or automatically add users to the privileged `docker` group.

### Install the platform

If Docker and its required plugins are already available:

```bash
sudo bash scripts/deploy.sh install
```

To explicitly allow installation of missing Docker components:

```bash
sudo bash scripts/deploy.sh install --install-docker
```

Automatic installation supports **Ubuntu 22.04/24.04 and Debian 12/13 on amd64/arm64 hosts running systemd**. This option authorizes the script to configure or reuse Docker's official apt repository, install missing components, and enable a newly installed Docker service. It does not automatically upgrade an existing Engine. Conflicting distribution Docker, Podman, or containerd/runc packages cause the installer to stop rather than remove them.

For other systems, rootless setups, or custom installations, follow the [Docker installation guide](https://docs.docker.com/engine/install/) and [Compose plugin guide](https://docs.docker.com/compose/install/linux/), then run the normal installer.

Installation and upgrades report timestamps, numbered stages, native Docker/apt output, and the stage that failed, rather than an estimated percentage. Installer messages are in English; output from external tools follows their own locale settings.

```text
[1/9] Check the operating system, base tools, and Docker
[2/9] Validate the deployment directory and acquire the operation lock
[3/9] Fetch main from GitHub and select the release
[4/9] Create or preserve configuration and validate Compose settings
[5/9] Build API and Admin images while the existing release stays online
[6/9] Enter the maintenance window and protect existing data
[7/9] Start PostgreSQL and apply migrations
[8/9] Start the API and Admin console and verify health
[9/9] Record the successful release and finish
```

The default installation directory is `/opt/agenticiot`. Only one Compose project named `agenticiot` is supported per host. Secrets are stored in `config.env` with mode `600`; PostgreSQL data lives in a Docker volume. Release snapshots and backups are stored in `releases/` and `backups/`. The initial installation generates a random administration token, database password, and HMAC key; upgrades preserve them. Do not publish configuration files or backups.

### Access the platform

By default, the Admin console binds to `127.0.0.1:5173` and the API to `127.0.0.1:8000` on the server. PostgreSQL has no published host port. Establish an SSH tunnel from your computer:

```bash
ssh -L 5173:127.0.0.1:5173 -L 8000:127.0.0.1:8000 your-user@your-server
```

Open `http://127.0.0.1:5173`. On the server, an administrator can securely inspect `sudo less /opt/agenticiot/config.env` and use the token in `AGENTICIOT_API_CLIENTS` in the pilot console. Do not upload or capture screenshots of this token. It is neither a shared default password nor a Node credential. Production applications should use short-lived signed tokens and explicit grants; see the [Node and local-service guide](docs/api/node-local-services.md).

## Upgrades and operations

From your original repository checkout:

```bash
git pull --ff-only origin main
sudo bash scripts/deploy.sh upgrade
```

The script fetches the latest `main` from the same GitHub repository, builds images, stops the applications, creates a backup, applies migrations, and checks the new release. It updates `current` only after success. An unchanged commit is not redeployed.

**Upgrades require a maintenance window.** Nodes reconnect, and in-flight inference may become `unknown`; this is not a zero-downtime rolling upgrade. Docker Engine and PostgreSQL major-version upgrades are separate operations.

| Operation | Command |
| --- | --- |
| Service status | `sudo bash scripts/deploy.sh status` |
| Recent logs | `sudo bash scripts/deploy.sh logs` |
| Standalone backup | `sudo bash scripts/deploy.sh backup` |
| Stop while preserving data | `sudo bash scripts/deploy.sh stop` |
| Start | `sudo bash scripts/deploy.sh start` |
| Restart | `sudo bash scripts/deploy.sh restart` |
| Roll back to a release with identical migration history | `sudo bash scripts/deploy.sh rollback <full-40-character-SHA>` |

When using a custom directory, supply it for every operation, for example: `sudo env AGENTICIOT_DEPLOY_DIR=/srv/agenticiot bash scripts/deploy.sh install`. Run `restart` after application configuration changes. Database password rotation also requires updating the database role; editing the configuration alone is insufficient.

A build failure leaves the old release running. A migration or health-check failure stops the applications and retains `pending`, blocking blind restarts and upgrades. Rollback never automatically downgrades the database. Crossing migration versions requires a validated backup-restoration plan. Do not run `docker compose down -v`. See the [Linux operations guide](docs/deployment/linux.md) for recovery procedures, backup protection, and validation boundaries.

## Public HTTPS and WSS

This tutorial runs **Caddy on the same host as the platform**, leaving application ports bound to loopback. You manage public networking and certificates; the deployment script does not configure them. TLS protects transport but does not replace authorization, administration access controls, or security validation.

### Prepare DNS and networking

1. Point your domain's A record to the server's public IPv4 address. The examples use `iot.example.com`. Add an AAAA record only if IPv6 connectivity works.
2. Allow TCP ports 80 and 443 in the cloud security group and host firewall. Restrict SSH to necessary sources. Do not expose ports 8000, 5173, or the database. Review Docker's [firewall limitations](https://docs.docker.com/engine/install/ubuntu/#firewall-limitations).
3. Ensure ports 80 and 443 are available. If Nginx or Caddy already serves other sites, integrate the configuration without overwriting them. Home servers also need a public address and router port forwarding; carrier-grade NAT generally cannot be solved by port forwarding alone.

### Configure the reverse proxy

Install Caddy on the host using its [official installation guide](https://caddyserver.com/docs/install). On a dedicated host without existing sites, edit `/etc/caddy/Caddyfile` using the repository's [example configuration](deploy/Caddyfile.example):

```caddyfile
iot.example.com {
    @backend path /v1/* /health/* /docs /docs/* /openapi.json
    handle @backend {
        reverse_proxy 127.0.0.1:8000 {
            flush_interval -1
        }
    }
    handle_path /api/* {
        reverse_proxy 127.0.0.1:8000 {
            flush_interval -1
        }
    }
    handle {
        reverse_proxy 127.0.0.1:5173
    }
}
```

Replace the example domain with your domain, then validate and load the configuration:

```bash
sudo caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
sudo systemctl enable --now caddy
sudo systemctl reload caddy
sudo journalctl -u caddy -n 100 --no-pager
```

When DNS, port reachability, and certificate-authority requirements are satisfied, Caddy obtains and renews certificates and redirects HTTP to HTTPS. Its reverse proxy supports WebSocket connections natively. See [Automatic HTTPS](https://caddyserver.com/docs/automatic-https) and [reverse proxy documentation](https://caddyserver.com/docs/caddyfile/directives/reverse_proxy). `flush_interval -1` forwards streaming output promptly. Avoid SSE buffering or connection timeouts shorter than your workload requires in an upstream CDN.

### Verify access and connect a Node

```bash
curl --fail https://iot.example.com/health/ready
curl --fail https://iot.example.com/api/health/ready
```

The Admin console is available at `https://iot.example.com`, with API endpoints under `https://iot.example.com/v1/...`. The console's same-origin `/api/` prefix is stripped exactly once, so permissive CORS settings are unnecessary. Nodes use **`wss://iot.example.com/v1/nodes/channel`**, not `/api/v1/nodes/channel`.

Prepare the project's Python environment on the Node host. Register the Node through the Admin console and securely capture its machine credential, which is displayed only once. For an interactive trial, avoid placing the token in shell history:

```bash
read -r -s -p 'Node token: ' AGENTICIOT_NODE_TOKEN
printf '\n'
export AGENTICIOT_NODE_TOKEN
uv run python -m agenticiot.node \
  --url wss://iot.example.com/v1/nodes/channel \
  --data-dir .local/node-home
```

For long-running deployments, use a permission-protected service configuration or a secrets manager. Never put tokens in URLs. Confirm that the Node appears online in the console to verify its authenticated connection; a successful HTTPS health check alone does not validate WSS. Do not disable TLS verification to bypass certificate errors.

Troubleshoot in this order: DNS and security groups, Caddy certificate logs, `deploy.sh status`, API readiness, then Node credentials and endpoint paths. Public deployments should integrate a trusted application backend, restrict administration access, and assess the handling of relayed data against their compliance requirements.

## Development and validation

See the [development guide](docs/development.md) for local development, native execution, and test-database configuration. Local Compose and the server installer are separate workflows; do not mix Compose projects with the same name on one host.

```bash
uv sync --frozen
npm ci
make check
```

CI includes database integration tests, browser end-to-end tests, and Linux Docker smoke tests. Automatic package-installation branches are tested with substitute commands; this does not establish that real apt installation has passed on every supported distribution. Public DNS, ACME certificates, real WSS connections, and backup restoration still require validation in the target environment.

## Documentation

English is the default language for public-facing documentation and new external materials. The README and installer messages are in English; some existing linked guides have not yet been translated.

- Architecture: [platform charter](docs/architecture/platform-charter.md), [current architecture](docs/architecture/device-node-services-v0.3.md), [implementation plan](docs/architecture/implementation-plan.md), and [architecture decisions](docs/adr).
- Interfaces: [implemented OpenAPI](api/platform.openapi.json), [device registry](docs/api/registry.md), [command execution](docs/api/execution.md), [Nodes and local services](docs/api/node-local-services.md), and [MQTT adapter](docs/api/mqtt-adapter.md). The [planned API contract](api/openapi.yaml) is not the implemented API.
- Validation: [software validation record](docs/experiments/software-pilot-validation.md), [network-latency experiment](docs/experiments/node-relay-validation.md), and [first physical device](docs/hardware/first-device.md).
- Historical references: [v0.1](docs/architecture/system-architecture.md) and [v0.2](docs/architecture/device-ai-services-v0.2.md). These are not the current architecture baseline.

## Licensing and commercial use

AgenticIoT has selected a **dual-licensing strategy: AGPLv3 plus a separate commercial license**. Users may choose compliance with the open-source license or a separate agreement with the copyright holders authorized to grant it. This is not a research-only or noncommercial-only model.

### Open-source licensing

Individuals, research institutions, and businesses may use the software, including commercially, without purchasing a commercial license from this project, provided they comply with the GNU Affero General Public License version 3 (AGPLv3).

Distribution of covered software carries applicable license-notice and corresponding-source obligations. If you modify the software and allow users to interact with that modified version over a network, Section 13 requires a prominent offer for those users to obtain its Corresponding Source at no charge. The [full AGPLv3 text](https://opensource.org/license/agpl-3.0) governs the exact requirements.

AGPL does not require a business to publish all of its code, nor does operating a paid cloud service automatically require a commercial license. The scope depends on how the software is modified, distributed, and combined with other works.

### Commercial licensing

Contact us to discuss a separate commercial license if you plan proprietary distribution, integration into a proprietary product, or a modified hosted service for which you do not wish to fulfill applicable AGPL source obligations. Permission for closed-source use, deployment scope, pricing, and support is defined by the written agreement. Commercial use that complies with AGPL does not require purchasing a commercial license.

For now, submit a non-sensitive contact request through [GitHub Issues](https://github.com/CNTWDev/AgenticIOT/issues) to arrange a private conversation. Do not post contracts, customer information, or credentials publicly.

### Licensing documents and contributions

The dual-licensing strategy is decided, but **a formal LICENSE file and commercial agreement have not yet been added**. This section describes the policy; it does not replace the formal licensing documents. Before a licensed release, the project must include the AGPL text, copyright notices, version designation, and commercial contact information, and review third-party dependency obligations.

A commercial license can cover only code that the licensor has the right to license separately; it cannot automatically waive third-party license requirements. Before accepting external contributions, the project should establish a contributor authorization mechanism compatible with dual licensing, such as an appropriate CLA. Submitting code does not automatically transfer copyright. Formal licensing documents should receive professional legal review.
