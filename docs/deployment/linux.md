# Linux 单机部署

此脚本部署 PostgreSQL 18、API 和管理后台，适用于当前软件试点。它不会部署家庭 Node、安装模型、开放防火墙或自动配置公网 TLS。真实设备、真实模型和家庭网络时延仍需按实验计划验证。

## 首次安装

需要 Linux、Git、OpenSSL、util-linux（提供 flock）、GNU coreutils、tar，以及 Docker Engine / Compose v2（支持 `--wait-timeout`）和 Buildx。建议使用 Ubuntu 24.04 LTS。`check` 只检测环境；`install --install-docker` 显式允许在 Ubuntu 22.04/24.04、Debian 12/13 的 amd64/arm64 systemd 主机上配置或复用 Docker 官方 apt 源并安装缺失组件。不会自动卸载冲突包、升级已有 Engine 或因权限问题重装。其他环境按[官方文档](https://docs.docker.com/engine/install/)手动安装。构建需要访问 GitHub、容器镜像仓库、Python 和 npm 包仓库。

```sh
git clone https://github.com/CNTWDev/AgenticIOT.git
cd AgenticIOT
bash scripts/deploy.sh check
sudo bash scripts/deploy.sh install
# 缺少 Docker 且同意安装系统组件时，改用：
# sudo bash scripts/deploy.sh install --install-docker
```

安装默认使用 `/opt/agenticiot`，从指定仓库的 `main` 拉取代码，按提交 SHA 构建镜像、执行数据库迁移、显式初始化一个管理授权，然后等待健康检查。每台服务器只支持一个名为 `agenticiot` 的 Compose 项目；已有同名容器或数据卷时拒绝首次安装，避免误接管开发环境。Docker 权限相当于主机管理员权限。

安装目录包含：`repo.git`（代码缓存）、`releases/<SHA>`（只读使用的版本快照）、`config.env`（密钥配置）、`backups`（数据库和配置备份）、`current`（最近成功版本）、`pending`（未完成部署）。目录权限为 700，配置权限为 600。首次随机生成数据库密码、HMAC 密钥和静态管理令牌，不打印密钥；升级不重新生成、不重置授权。

默认仅监听服务器回环地址，数据库不映射主机端口。可从自己的电脑建立隧道：

```sh
ssh -L 5173:127.0.0.1:5173 -L 8000:127.0.0.1:8000 your-user@your-server
```

浏览器打开 `http://127.0.0.1:5173`。在服务器上由管理员安全读取 `/opt/agenticiot/config.env` 中 `AGENTICIOT_API_CLIENTS` 的 token，填入管理后台。它是试点用静态入口凭证，不是用户注册体系，也不是 Node 凭证。不要把配置、令牌或备份发送到聊天、工单和 Git。

安装和升级均按九个阶段输出时间、步骤编号和原生命令进度，失败时指出所处阶段。公网配置与 WSS 检查请参照 [README 的完整教程](../../README.md#public-https-and-wss)及 [Caddyfile 示例](../../deploy/Caddyfile.example)。API 上游为 `127.0.0.1:8000`，Node 路径为 `/v1/nodes/channel`；不得用明文公网 HTTP 承载凭证。TLS 不取代可信入口签名验证和授权，另见 [Node 接入说明](../api/node-local-services.md)。

## 升级和运维

在最初克隆的目录运行，先更新运维脚本，再执行操作：

```sh
git pull --ff-only origin main
sudo bash scripts/deploy.sh upgrade
sudo bash scripts/deploy.sh status
sudo bash scripts/deploy.sh logs
sudo bash scripts/deploy.sh backup
sudo bash scripts/deploy.sh stop
sudo bash scripts/deploy.sh start
sudo bash scripts/deploy.sh restart
```

也可使用已安装版本的 `/opt/agenticiot/releases/<current文件中的SHA>/scripts/deploy.sh`。不同命令必须使用同一个目录；自定义时每次显式传入 `sudo env AGENTICIOT_DEPLOY_DIR=/srv/agenticiot bash scripts/deploy.sh ...`。不要修改版本快照；修改配置后执行 `restart`。数据库密码不能仅靠修改配置轮换，必须同时协调数据库角色密码。HMAC 轮换应保留前一密钥，参见接口说明。

升级顺序固定为：拉取并构建新镜像 → 停止后台/API，等待最长 25 秒退出 → 备份数据库和配置 → 执行迁移 → 启动 API 并检查就绪 → 启动后台并检查健康 → 更新成功版本。PostgreSQL 容器不随应用升级重建。单实例部署有停机窗口，Node 会断线重连，进行中的推理可能成为 unknown；这不是无损滚动升级。

备份使用 PostgreSQL 自定义格式，并验证归档目录可读；不等于完成恢复演练。独立 `backup` 可在运行中执行，提供数据库一致性快照；升级备份则在应用停止后执行。备份包含敏感对话元数据、资源和配置密钥，应另行加密异地保存、制定保留期并定期恢复演练。脚本不自动删除备份、镜像、版本或数据卷，也不升级 PostgreSQL 主版本。

## 回退和失败处理

仅允许回退到本机保留的版本，且两个版本的完整数据库迁移目录必须一致：

```sh
sudo bash scripts/deploy.sh rollback <完整40位提交SHA>
```

即使迁移一致，也必须审查业务语义是否向后兼容。回退会先停止应用并备份，不执行数据库 downgrade。现有迁移包含不可逆变更，跨迁移版本不能把旧应用直接接到新数据库上。

迁移或新版本健康检查失败时，脚本保留 `pending`，停止应用并拒绝普通 `start/restart/upgrade/rollback`，不会假装已回退。构建失败发生在停机前，旧服务继续工作。不要手工删除数据卷或执行 `docker compose down -v`。

恢复由管理员根据失败阶段决定：

1. 保留 `pending`、日志、原始配置及备份；检查数据库当前 Alembic revision。未成功安装时，版本由 `pending` 指定，不是 `current`。
2. 若继续新版本，修复失败原因，针对 `pending` 版本显式运行迁移并完成就绪验证后，才更新 `current` 并归档 `pending`。不要重新执行初始授权来覆盖已有权限。
3. 若恢复旧版本，把升级前的备份先恢复到隔离的 PostgreSQL 18 实例，验证完整性、授权与旧应用就绪状态，再安排数据库切换和对应配置恢复；这可能丢失备份之后的数据，需明确批准。禁止自动覆盖当前数据库。

人工诊断时使用以下前缀（不打印或 source 配置文件）：

```sh
export RELEASE_SHA="<current或pending中的完整SHA>"
export RELEASE_DIR="/opt/agenticiot/releases/$RELEASE_SHA"
sudo --preserve-env=RELEASE_SHA,RELEASE_DIR docker compose \
  --project-name agenticiot --env-file /opt/agenticiot/config.env \
  -f "$RELEASE_DIR/deploy/compose.linux.yaml" ps -a
```

将最后的 `ps -a` 替换为 `logs --tail 100 api migrate postgres` 可看日志。只有确认版本、迁移与备份后，才执行改变状态的操作。不要把数据库恢复当作一键回退。

## 验证范围

脚本提供 Bash 语法检查和替身 Docker/Git 的流程回归测试，覆盖操作顺序、密钥保留、失败阻断和回退限制。真实 Linux Docker 安装、恢复演练、HTTPS/WSS 和真实网络测试需要在目标服务器完成；在这些验收通过前，仅作为试点部署工具使用。
