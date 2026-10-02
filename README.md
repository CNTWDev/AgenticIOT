# AgenticIoT

AgenticIoT 是面向 AI Agent 的设备管理与 Node 本地服务接入平台。入口产品可以通过清晰、可审计的接口发现设备、执行动作、订阅已采集事件，并通过 Node 的主动连接访问家庭或企业内网中获准的模型服务。

平台专注于资源授权、设备连接和执行证据；Agent 的规划、记忆、用户关系和云模型策略留给入口产品。坚持 Less is More，不把设备平台做成通用 AI 网关。

**当前为软件试点，不是生产就绪声明。** 已有模拟设备、MQTT 模拟链路、Node、本地服务流式转发、管理后台与自动化测试。真实设备、具体模型兼容性、家庭网络时延和生产安全验收仍需完成。授权策略为 AGPLv3 与独立商业授权双许可，正式授权文件的落地状态见文末“许可与商业授权”。

## 项目架构

```text
手机 / 音响 / Web 产品
         │
可信入口后端 + Agent Runtime
  用户账号、意图、确认、记忆、云模型策略
         │ HTTPS / Bearer / 设备事件 SSE
         ▼
AgenticIoT 模块化单体 + 管理后台
  Access       内部域、入口映射、授权交集
  Registry     设备型号、能力 Schema、资源登记
  Nodes        机器身份、激活、WSS 会话与租约
  Runtime      异步设备命令、执行证据、未知结果
  AI Services  已批准的 Node 本地服务、请求内流式推理
  Tools/Events 类型化 HTTP 工具、带游标的采样事件
         │                  │
    PostgreSQL              │ Node 主动向平台建立 WSS
                            ▼
                     家庭 / 企业内网 Node
                       ├─ Adapter → 感知与执行设备
                       └─ 白名单 → 本机模型服务
```

算力节点在这里代表“提供模型等服务的节点”，不是 GPU/NPU 资源池。设备、服务资源归属平台内部 `domain_id`；人的账号体系由入口维护，Node 使用独立机器凭证，不能复用用户管理令牌。

### 核心边界

- **授权先于激活**：入口声明的权限与平台登记的域内授权取交集。登记设备不等于设备已在线，本地服务登记后仍需批准启用。
- **推理不直接操作硬件**：模型生成与设备命令各自管理生命周期；设备动作通过确定性代码、权限校验与执行接口完成。
- **未知不是失败**：结果未知时不自动重发、不拼接另一模型输出。业务操作标识由入口 Runtime 管理，不交给 LLM 猜测。
- **本地服务不等于数据不出网**：经平台中转的输入会经过服务器；“声明为本地”不是出口隔离证明。
- **事件有采集边界**：当前提供采样推导的状态变化，不能保证两次采样之间的物理事件都被记录。入口 Runtime 消费事件并唤醒 Agent。

技术栈为 Python 3.13 / FastAPI / SQLAlchemy / PostgreSQL 18，后台采用 React / TypeScript / Vite。首版单实例部署，不引入消息中间件、跨实例流转、支付、GPU 调度和云模型备用路由。当前工具接口是 HTTP，不宣称已实现 MCP。

完整决策见 [v0.3 技术架构](docs/architecture/device-node-services-v0.3.md)、[账号与资源激活](docs/adr/0010-account-resource-activation.md)和[异步运行模型](docs/adr/0011-async-node-runtime.md)。

## Linux 安装

推荐使用专用 Ubuntu 24.04 LTS 服务器。需要 Git、OpenSSL、util-linux、GNU coreutils、tar，以及 Docker Engine、Compose v2 和 Buildx。构建需要连通 GitHub、Docker 镜像仓库、Python 和 npm 包仓库。脚本不安装家庭 Node，不下载大模型，也不开放防火墙。

Ubuntu/Debian 尚无基础工具时，先执行：

```bash
sudo apt-get update
sudo apt-get install -y git openssl util-linux coreutils tar
git clone https://github.com/CNTWDev/AgenticIOT.git
cd AgenticIOT
```

### 检测环境

```bash
bash scripts/deploy.sh check
# 普通用户无 Docker socket 权限时，用 sudo 再检查
sudo bash scripts/deploy.sh check
```

`check` 不安装软件、不创建部署目录。脚本区分“未安装 Docker”“守护进程未启动或无权限”“缺少 Compose/Buildx”“Compose 版本过旧”，打印对应处理方案。已有 Docker 不可达时不会重装，也不会自动把用户加入拥有主机级权限的 docker 组。

### 安装平台

已经安装 Docker 的服务器：

```bash
sudo bash scripts/deploy.sh install
```

允许脚本安装缺失的 Docker 组件：

```bash
sudo bash scripts/deploy.sh install --install-docker
```

自动安装仅支持 **Ubuntu 22.04/24.04、Debian 12/13 的 amd64/arm64 systemd 主机**。此开关授权脚本配置或复用 Docker 官方 apt 源，安装缺失组件，并启用新装的 Docker 服务。已有 Engine 不自动升级；发现发行版 Docker、Podman 或 containerd/runc 冲突包时停止，不替你卸载它们。其他系统、rootless 或自定义安装请按 [Docker 官方指南](https://docs.docker.com/engine/install/)和 [Compose 插件指南](https://docs.docker.com/compose/install/linux/)完成环境准备，再执行普通安装。

安装输出包含时间、步骤编号、Docker/apt 原始进度和失败阶段，不伪造百分比：

```text
[1/9] 检测操作系统、基础工具和 Docker 环境
[2/9] 检查部署目录并获取操作锁
[3/9] 从 GitHub 拉取 main 并确定部署版本
[4/9] 生成或保留配置，验证 Compose 配置
[5/9] 构建 API 和后台镜像，现有服务继续运行
[6/9] 进入维护窗口并保护现有数据
[7/9] 启动数据库并执行迁移
[8/9] 启动 API 和管理后台并验证健康状态
[9/9] 记录成功版本并完成部署
```

默认安装目录 `/opt/agenticiot`，每台主机仅支持一个 `agenticiot` Compose 项目。密钥配置存于 `config.env`（权限 600），数据存于 PostgreSQL Docker 卷；代码快照、备份分别保存在 `releases/`、`backups/`。首次生成随机管理令牌、数据库密码和 HMAC 密钥，升级时保留原值。不要公开配置或备份。

### 首次访问

默认只绑定服务器回环地址：后台 `127.0.0.1:5173`，API `127.0.0.1:8000`；数据库不发布主机端口。从个人电脑建立隧道：

```bash
ssh -L 5173:127.0.0.1:5173 -L 8000:127.0.0.1:8000 your-user@your-server
```

浏览器访问 `http://127.0.0.1:5173`。管理员在服务器本机用 `sudo less /opt/agenticiot/config.env` 安全查看 `AGENTICIOT_API_CLIENTS` 中的 token，填入试点管理后台；不要截图或上传令牌。它不是默认公共密码，也不是 Node 凭证。生产入口应接入短期签名令牌及明确授权，见 [Node 与本地服务指南](docs/api/node-local-services.md)。

## 升级与日常运维

在最初克隆的项目目录执行：

```bash
git pull --ff-only origin main
sudo bash scripts/deploy.sh upgrade
```

脚本从同一 GitHub 仓库拉取最新 `main`，先构建镜像，再停止应用、备份、迁移并检查新版本健康状态；成功后才更新 `current`。相同提交不重复升级。**升级有停机窗口**：Node 重连，进行中的推理可能记为 unknown，不承诺无损滚动升级。Docker Engine 和 PostgreSQL 主版本升级另行安排。

| 操作 | 命令 |
| --- | --- |
| 查看状态 | `sudo bash scripts/deploy.sh status` |
| 最近日志 | `sudo bash scripts/deploy.sh logs` |
| 独立备份 | `sudo bash scripts/deploy.sh backup` |
| 停止并保留数据 | `sudo bash scripts/deploy.sh stop` |
| 启动 | `sudo bash scripts/deploy.sh start` |
| 重启 | `sudo bash scripts/deploy.sh restart` |
| 同迁移历史版本回退 | `sudo bash scripts/deploy.sh rollback <完整40位SHA>` |

自定义目录时每次都显式传入，例如 `sudo env AGENTICIOT_DEPLOY_DIR=/srv/agenticiot bash scripts/deploy.sh install`。修改应用配置后执行 `restart`；数据库密码不能只改配置，必须同步修改数据库角色。

构建失败不影响旧服务；迁移或健康检查失败时停止应用并保留 `pending`，阻止盲目重启和升级。回退不会自动降级数据库；跨迁移版本须先验证备份恢复方案。不要执行 `docker compose down -v`。详细恢复步骤、备份保护与验证边界见 [Linux 运维手册](docs/deployment/linux.md)。

## 公网 HTTPS 和 WSS

以下教程将 **Caddy 安装在同一台宿主机上**，保留应用的回环端口。证书和公网网络由你管理，部署脚本不会自动修改这些配置。TLS 只提供传输安全，不能代替账号授权、管理入口访问控制和安全验收。

### 准备域名与网络

1. 将自己的域名（以下用 `iot.example.com` 举例）A 记录指向服务器公网 IPv4；只有 IPv6 确实可达时才设置 AAAA。
2. 在云安全组和主机防火墙允许 TCP 80/443；SSH 仅允许必要来源。不要开放 8000、5173 或数据库端口。Docker 端口与防火墙的关系参见[官方安全提示](https://docs.docker.com/engine/install/ubuntu/#firewall-limitations)。
3. 确认 80/443 未被其他服务占用；已有 Nginx/Caddy 时整合配置，不覆盖其他站点。家庭服务器还需公网地址及路由端口映射；运营商 CGNAT 通常不能仅靠端口映射提供公网服务。

### 配置反向代理

按 [Caddy 官方安装指南](https://caddyserver.com/docs/install)完成宿主机安装。对于尚未配置站点的专用主机，编辑 `/etc/caddy/Caddyfile`，参考仓库里的 [Caddyfile 示例](deploy/Caddyfile.example)：

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

把示例域名替换成真实域名，然后验证并加载：

```bash
sudo caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
sudo systemctl enable --now caddy
sudo systemctl reload caddy
sudo journalctl -u caddy -n 100 --no-pager
```

满足 DNS、端口和证书机构访问条件时，Caddy 自动申请、续期证书并将 HTTP 重定向至 HTTPS；反向代理原生支持 WebSocket。参见[自动 HTTPS](https://caddyserver.com/docs/automatic-https)和[代理说明](https://caddyserver.com/docs/caddyfile/directives/reverse_proxy)。`flush_interval -1` 用于及时转发流式输出；不要在上层 CDN 再缓冲 SSE，或设置短于业务需要的连接超时。

### 验证访问与连接 Node

```bash
curl --fail https://iot.example.com/health/ready
curl --fail https://iot.example.com/api/health/ready
```

后台地址为 `https://iot.example.com`，API 为 `https://iot.example.com/v1/...`。后台同源 `/api/` 只去掉一次前缀，不需要放宽 CORS。Node 使用 **`wss://iot.example.com/v1/nodes/channel`**，不是 `/api/v1/nodes/channel`。

在安装了项目 Python 运行环境的 Node 主机上，先通过后台注册节点、取得只显示一次的机器凭证。交互试运行可避免令牌出现在命令历史中：

```bash
read -r -s -p 'Node token: ' AGENTICIOT_NODE_TOKEN
printf '\n'
export AGENTICIOT_NODE_TOKEN
uv run python -m agenticiot.node \
  --url wss://iot.example.com/v1/nodes/channel \
  --data-dir .local/node-home
```

长期运行时使用受权限保护的服务配置或秘密管理系统；不要把 token 放进 URL。以后台显示 Node 在线确认鉴权握手完成；HTTPS 健康检查成功并不证明 WSS 已连通。不要通过关闭 TLS 校验绕过证书错误。

排障顺序：DNS 与安全组 → Caddy 证书日志 → `deploy.sh status` → API 就绪 → Node 凭证和路径。公开部署应接入可信入口后端、限制管理访问来源，并按自己的合规要求处理云中转数据。

## 开发与验证

本地开发、原生运行和测试数据库配置见 [开发指南](docs/development.md)。本地 Compose 与服务器脚本是两个工作流，不要在同一主机混用同名 Compose 项目。

```bash
uv sync --frozen
npm ci
make check
```

CI 包含数据库集成测试、浏览器端到端测试和 Linux Docker 容器冒烟测试。自动安装分支使用替身命令验证控制流程，不等于已在所有支持发行版上完成真实 apt 安装；公网 DNS、ACME 证书、真实 WSS 和备份恢复仍需要目标环境验收。

## 文档导航

- 架构：[平台章程](docs/architecture/platform-charter.md)、[当前架构](docs/architecture/device-node-services-v0.3.md)、[实施计划](docs/architecture/implementation-plan.md)、[ADR 目录](docs/adr)。
- 接口：[实际 OpenAPI](api/platform.openapi.json)、[设备注册](docs/api/registry.md)、[命令执行](docs/api/execution.md)、[Node 与本地服务](docs/api/node-local-services.md)、[MQTT 适配器](docs/api/mqtt-adapter.md)。[规划接口](api/openapi.yaml)不等同于已实现接口。
- 验证：[软件验证记录](docs/experiments/software-pilot-validation.md)、[网络时延实验](docs/experiments/node-relay-validation.md)、[首个真实设备](docs/hardware/first-device.md)。
- 历史：[v0.1](docs/architecture/system-architecture.md)、[v0.2](docs/architecture/device-ai-services-v0.2.md)，仅作为历史决策参考。

## 许可与商业授权

AgenticIoT 选择 **AGPLv3 开源许可 + 独立商业授权** 的双许可策略。使用者可选择遵守开源许可，或与有权授权的版权方另行签订商业协议。这不是“仅限研究”或“禁止商用”的许可模式。

### 开源许可

个人、研究机构和企业均可在遵守 GNU Affero General Public License version 3（AGPLv3）的前提下，无需向本项目购买商业许可即可使用，包括商业用途。

分发受许可覆盖的软件时，应履行适用的许可声明和对应源码提供义务。修改本软件并允许用户通过网络与该修改版本交互时，应依第 13 条向这些用户显著提供免费获取该版本对应源码的机会。具体要求以 [AGPLv3 完整条款](https://opensource.org/license/agpl-3.0)为准。

AGPL 并不意味着企业全部代码都必须公开，也不意味着只要运行收费云服务就必须购买商业授权；应根据修改、分发及组合方式判断受许可覆盖的范围。

### 商业授权

如果计划闭源分发、将本项目集成到专有产品，或提供不希望履行适用 AGPL 源码义务的修改版云服务，可联系我们协商独立商业授权。获准的闭源使用、部署范围、费用及支持服务以书面协议为准；符合 AGPL 的商业使用不强制购买商业授权。

目前可通过 [仓库 Issue](https://github.com/CNTWDev/AgenticIOT/issues)提出非敏感的授权联系请求，再协商私下沟通方式。请勿公开合同、客户资料或密钥。

### 授权文件与贡献者

双许可策略已确定，**当前尚未添加正式 LICENSE 文件或商业合同**。本节说明授权策略，不替代正式授权文件；发布前需补齐 AGPL 全文、版权声明、版本选择及商业联系信息，并审查第三方依赖的许可义务。

商业授权仅能覆盖授权方有权另行许可的代码，不能自动豁免第三方组件的许可。接收外部贡献前，应建立支持双许可的贡献者授权机制（如适当的 CLA）；提交代码不自动转让版权。正式授权文件建议由法律专业人士复核。
