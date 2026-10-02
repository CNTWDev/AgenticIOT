# 本地开发

当前为 v0.3 软件试用版：注册和模拟执行基础上已加入内部资源域、Grant、签名入口验证、独立 Node 凭证、WSS、Node 本地文本流和采样事件。完整步骤见 [Node 与本地服务运行指南](api/node-local-services.md)。真实模型兼容、家庭网络、真实硬件和生产安全部署仍待验收。

## 环境

- Python 3.13、uv 0.8.3 或兼容版本。
- Node.js 22.13 以上的 22.x、npm。
- PostgreSQL 18；推荐用 Docker Compose 启动，也可使用现有本地实例。

Python 与 Node 依赖分别锁定在 `uv.lock` 和 `package-lock.json`。运行命令时位于仓库根目录。

## 一键容器启动

```sh
docker compose up --build -d
```

Compose 依次启动数据库、执行迁移、启动 API 和后台。

- 后台：http://127.0.0.1:5173
- API 文档：http://127.0.0.1:8000/docs
- API 存活：http://127.0.0.1:8000/health/live
- 数据库就绪：http://127.0.0.1:8000/health/ready

Compose 的凭证是本地开发默认值，所有对外端口只绑定回环地址。`docker compose down` 停止服务并保留数据库卷。

这里的默认凭证仅指 PostgreSQL。设备 API 默认拒绝所有访问；先按下方说明配置自己的 API 凭证，再启动或重启 API 服务。

## 热更新开发

```sh
cp .env.example .env
make install
make db-up
make migrate
```

两个终端分别运行：

```sh
make api
```

```sh
make admin
```

Vite 将 `/api/*` 转发到本机 API。不要把数据库密码或服务令牌放在 `VITE_*` 变量中。

如果没有 Docker，可以将 `.env` 中的 `AGENTICIOT_DATABASE_URL` 指向已有的 PostgreSQL 数据库，然后执行 `make migrate`。没有数据库也可以打开后台，API 存活检查正常，数据库检查显示未就绪。

## 体验设备注册闭环

1. 首次创建 `.env` 时使用 `.env.example`；已有 `.env` 不要覆盖。
2. 本机生成随机凭证：`uv run python -c 'import secrets; print(secrets.token_urlsafe(32))'`。
3. 将 `.env` 中的 `AGENTICIOT_API_CLIENTS=[]` 改为以下内容，用刚生成的值替换占位符。不要提交 `.env` 或复用测试凭证。

```dotenv
AGENTICIOT_API_CLIENTS='[{"token":"REPLACE_WITH_YOUR_RANDOM_TOKEN","subject_ref":"operator:local","domain_ref":"home:demo","role":"operator"}]'
```

凭证长度至少 32 字符；角色为 `operator` 或 `viewer`。每个凭证只绑定一个域，域与主体是外部引用，不会创建用户、家庭或成员关系。空配置保持默认拒绝。

4. 执行 `make migrate` 和 `uv run python -m agenticiot.access.service`，再启动 `uv run python -m agenticiot.serve`。Compose 用户启动后显式执行 `docker compose exec api python -m agenticiot.access.service`。应用启动不会自动建立或扩展 Grant；只改环境变量不等于获准访问。
5. 后台进入“设备型号”，输入凭证连接工作区，点击“发布型号”，填写标识和名称，使用虚拟灯具模板后发布。
6. 切换“设备注册”，填写设备名称、域内唯一标识、型号版本和可选空间引用，确认注册。
7. 查看属性/动作/事件 Schema；修改名称或空间；进入“注册审计”查看变更主体和追踪 ID。

凭证仅存在页面内存中，不进入 localStorage/sessionStorage。刷新、断开或离开注册模块返回概览后需要重新输入。本地 HTTP 只用于回环地址；不要直接对公网部署此开发认证方案。生产化需要外部身份/授权、TLS、限流、凭证生命周期和更完整的安全审计。

注册设备真实状态为 `commissioning / unknown`；虚拟灯具模板只是能力定义，不会模拟在线、产生设备状态或执行物理动作。

## 继续运行独立 Node

后台进入“节点与本地服务”登记 Node，保存一次性机器凭证并配置 `AGENTICIOT_NODE_TOKEN`，按[运行指南](api/node-local-services.md)启动 `python -m agenticiot.node`。默认应用不挂载旧 HTTP Edge 路由，Compose 不自动创建或启动 Node。

在设备详情中绑定已注册的 Edge；等到独立进程上报后刷新，即可看到模拟灯具的观测。提交虚拟命令后查看受理、调度、确认的回执序列。后台不伪造执行结果，也不把“受理”当成“成功”。

已有安装先备份，再运行 `uv sync --frozen`、`make migrate` 到 `0010_hmac_key_version`。内部域回填保留既有设备及关系；`0005_access` 禁止有损自动降级，回退需恢复备份。不要删除在用 Node 日志、提交到仓库或跨节点共享日志目录。旧静态 Edge 不自动获得机器凭证：域内管理员用原 edge_ref 显式登记，可以为尚无机器凭证的历史节点签发新凭证，保留原 ID、绑定和日志身份；已托管节点必须走独立轮换接口。

接入本机 MQTT 模拟灯具见 [MQTT Adapter 指南](api/mqtt-adapter.md)。Broker 是开发依赖/独立进程，不进入平台服务或平台命令队列；无需 Docker，也不连接公网 Broker。

## 校验

```sh
make check
cd apps/admin && npx playwright install chromium
cd ../..
npm run test:e2e
```

`make check` 包含 Python 格式与 lint、非数据库后端测试、OpenAPI 规范与生成文件一致性、前端 lint、格式及生产构建。MQTT 测试会启动仅本机监听的临时 Broker/设备子进程，结束后关闭。浏览器测试启动独立的 API 8011 和后台 5181，检查桌面/手机布局、真实 API 连接及断线恢复。

数据库集成测试需要专用可丢弃数据库，验证升级、旧数据回填和有损降级拒绝：

```sh
AGENTICIOT_TEST_DATABASE_URL='postgresql+psycopg://USER:PASSWORD@127.0.0.1:PORT/agenticiot_test' uv run pytest -m integration
AGENTICIOT_TEST_DATABASE_URL='postgresql+psycopg://USER:PASSWORD@127.0.0.1:PORT/agenticiot_test' npm run test:e2e
```

不要将该变量指向开发数据或生产数据。未提供变量时数据库和写入型浏览器测试明确跳过。浏览器进程不继承开发凭证，测试启动时显式引导测试域和 Grant。测试保留测试记录，使用专用数据库，后端迁移测试和浏览器测试顺序运行。

## 目录

```text
backend/src/agenticiot/    FastAPI、鉴权、registry 领域和数据库基础设施
backend/migrations/       Alembic；0002_registry 注册表，0003_runtime 运行表，0004_adapters
backend/tests/            后端及数据库迁移测试
apps/admin/              React + TypeScript 管理后台
api/openapi.yaml         Capability API 设计契约；虚拟执行已实现，部分功能仍待实现
api/platform.openapi.json 已实现的接口快照（生成文件）
scripts/                 契约检查、文档生成和架构检查
docs/                    产品定位、架构、ADR 和开发文档
```

能力与设备位于 registry/runtime，Access、Nodes、Services 分模块。独立 WSS 运行器位于 `node.py`，复用 `edge.py` 的日志；旧 HTTP 运行器仅供历史回归。`agent_client.py` 是确定性工具适配器。最小 Adapter 位于 `adapters.py`，MQTT 适配在 `mqtt_adapter.py`，模拟 Broker/设备在 `mqtt_demo.py`；Node 不直接访问平台数据库。

## API 与迁移变更

修改已实现接口后运行 `make contracts`，提交生成的规范与文档；CI 会校验漂移。设计中的 Capability API 必须通过完整 OpenAPI 3.1 验证。

迁移通过 `uv run alembic revision -m 'description'` 创建并审核。添加领域表时将相应 SQLAlchemy 模型导入迁移 metadata，并更新 `database.py` 中期待的迁移 revision。应用启动不自动迁移；迁移作为独立步骤运行。

涉及公共契约、数据库迁移或基础设施的 PR 应同时更新架构/API 文档，重要边界变化新增 ADR；本地 `uv run python scripts/check_architecture.py --base <commit>` 与 CI 使用同一检查。

## 当前身份边界

健康接口和开发文档可匿名访问。资源端点要求 Bearer 凭证，并校验内部域与平台 Grant。只读角色不能写入、调用推理或查看注册审计。支持开发静态凭证及已登记入口的 Ed25519 令牌，不构建平台内建用户/家庭系统。签名验证实现不等于正式身份提供方集成验收完成。

## 当前验证与历史记录

2026-10-02 的软件验收状态见[实施计划](architecture/implementation-plan.md)和 [ADR 0011](adr/0011-async-node-runtime.md)。下列按日期保存的记录描述当时版本，不代表旧 HTTP 通道、旧迁移降级路径或旧身份范围仍是当前运行方式。

## Milestone 0 验证记录

本地验证环境：macOS、Python 3.13、Node.js 22.17、独立 PostgreSQL 18 测试实例。

- `make check` 通过：Python lint/格式、5 项非数据库测试、两份 OpenAPI 完整验证与生成文件一致性、前端 lint/格式和生产构建。
- PostgreSQL 集成验证通过：迁移到 head、降级到 base、再次升级；就绪检查随迁移状态正确改变。合计 6 项后端测试通过。
- 4 项 Playwright 测试通过：桌面和手机布局、真实 API 代理、连接失败反馈与刷新恢复。页面无运行时异常或横向溢出，截图已人工式视觉检查。
- Compose、GitHub Actions YAML 解析通过，Alembic 离线 SQL 可生成。
- 本机没有 Docker，未执行容器镜像构建或整套 Compose 启动；CI 已配置构建检查，但尚未在远程运行。配置解析不能替代容器实测。
- Starlette 1.6 对 AnyIO 4.15 的一个已弃用别名产生第三方警告，测试仅忽略此具体警告，其他警告仍视为错误。

虚拟与 MQTT 模拟执行闭环现已接入；下一步先固定一个真实设备的接入配置、认证/主题权限和故障验收标准，再进入该硬件驱动，不同时铺开所有协议。

本轮接入准备见 [首个真实设备计划](hardware/first-device.md) 和 [ADR 0007](adr/0007-first-physical-device-gates.md)。设备尚未选择，下一实现阶段默认只读，不自动进入真实执行。本次仅更新交付计划与准入文档，没有新增数据库迁移、驱动、认证能力或激活接口。

## 注册切片验证记录（2026-09-14）

- `make check` 通过：17 项非数据库测试、Python/TypeScript 检查、格式、实际/设计 OpenAPI 验证、生成文件一致性与生产构建。
- 专用 PostgreSQL 18 上共 24 项后端测试通过，含 7 项数据库集成测试：迁移往返、型号版本、重复/并发注册、跨域隔离、只读权限、条件编辑、分页、审计和应用重新实例化后的持久化读取。
- `alembic check` 无模型与迁移漂移。
- 8 项 Playwright 测试通过（桌面/手机各 4 项）：真实 API 概览、断线恢复、发布/注册/发现/编辑与冲突恢复、无效凭证/只读权限。测试不使用模拟业务响应；截图已视觉检查，页面无运行时异常或横向溢出（窄屏表格在自身区域滚动）。
- Docker 仍未安装：本轮未执行容器构建或 Compose 全栈启动，也未运行远程 CI。已有 Compose/CI 配置不代表完成部署验收。

## 虚拟执行切片验证记录（2026-09-14）

- `make check` 通过：26 项非数据库测试、lint/格式、OpenAPI 和生成文件一致性、TypeScript 与生产构建。
- 独立 PostgreSQL 18 上共 42 项后端测试通过，含 16 项数据库测试。覆盖并发幂等、按域/Edge 授权、重复结果、过期/晚到证据、观测新鲜度、旧观测拒绝、高风险拒绝、本地日志重启和迁移回滚保留注册资料。
- `alembic check` 无模型漂移；`0003_runtime` 正向迁移只新增运行表。
- 10 项 Playwright 测试通过（桌面/手机各 5 项）。新增流程实际启动独立 Python Edge 子进程，验证界面绑定、初始观测、异步动作、状态变化、四步回执和列表同步。
- 浏览器测试每次运行使用独立域，重试使用独立 Edge 身份，避免旧测试日志序号影响新运行；必须仍使用专用测试数据库。执行面板和完整页面截图已经视觉检查，无页面级横向溢出。
- 曾出现一次开发服务启动超时，带启动日志重跑后 10 项全部通过；未复现，不能据此推断生产稳定性。
- 无真实设备、没有离线自治或生产认证验收；本机无 Docker，容器构建/Compose 启动与远程 CI 尚未实测。测试结束后专用 PostgreSQL 实例已停止。

## Adapter / MQTT 切片验证记录（2026-09-14）

- 最终专用 PostgreSQL 18 上 61 项后端测试通过：44 项非数据库测试、17 项数据库测试。MQTT 测试使用真实本机 aMQTT Broker 和独立设备进程，不是仅 mock 发布函数。
- `make check` 通过：Python/前端 lint、格式、实际/设计 OpenAPI 校验、生成文件一致性、TypeScript 与生产构建；`alembic check` 无模型漂移。
- 12 项 Playwright 测试通过（桌面/手机各 6 项）。新增 MQTT 场景实际启动 Broker、模拟设备、独立 Edge，经 HTTP/API/数据库完成后台绑定、执行、观测及回执闭环。
- 同一浏览器流程随后停止 MQTT 设备：验证 Broker 可用但缺少设备证据时显示 `execution_uncertain`，不把旧状态刷新成新观测，不自动重发；界面明确提示“设备可能已执行”。执行成功和不确定状态截图已视觉检查。
- 覆盖虚拟/MQTT 共用 Adapter 契约、设备日志重启去重、Edge 外部调用崩溃恢复、错误关联 ID/目标/时间/值、真实 Broker 保留快照拒绝、Broker 连接失败、未声明 Adapter 拒绝、跨域/改绑/移除在用 Adapter 拒绝与安全降级保护。
- `0004_adapters` 为增量列迁移，给旧 Edge 回填虚拟 Adapter。已有 MQTT 绑定时拒绝降级至旧运行器；测试使用新的可丢弃数据库验证迁移往返，没有操作用户业务数据库。
- 本机仍无 Docker：未执行容器构建、Compose 全栈、远程 CI 或真实设备验收。默认循环为串行开发演示，不提供规模/吞吐 SLA。测试服务结束后关闭，临时测试数据保留在本次专用目录。
