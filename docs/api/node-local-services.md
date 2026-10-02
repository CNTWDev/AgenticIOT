# Node 与本地服务运行指南

Current runtime amendment: [pilot recovery guide](../operations/pilot-recovery.md) covers protocol v2, command reconciliation, usage configuration and exact SSE failure semantics. Upgrade to `0011_runtime_recovery`, preserving Node journals. API schemas are generated in `api/platform.openapi.json`.

当前软件实现覆盖可信账号授权、内部域、节点凭证、WSS、模拟设备、请求内文本流和采样事件。平台不是云模型网关，也不创建用户账号。先完成下面的受控引导，再激活资源。

## 一 引导管理主体

配置 `.env` 中的数据库和自己的 `AGENTICIOT_API_CLIENTS`，执行：

```sh
uv sync --frozen
uv run alembic upgrade head
uv run python -m agenticiot.access.service
uv run python -m agenticiot.serve
```

Compose 用户先启动服务，再显式执行 `docker compose exec api python -m agenticiot.access.service`。启动应用不自动创建授权。空配置默认拒绝；重复引导保留已有授权的撤销或收窄状态。

静态凭证仅供本地开发。签名入口使用 `AGENTICIOT_TRUSTED_ISSUERS` 数组，每项包含 `issuer, client_id, key_id, public_key`。公钥为 Ed25519 PEM；JWT 固定 EdDSA，必须含 `iss, aud, iat, exp, sub, client_id, domain_ref, permissions`，最长有效期一小时，默认 audience 为 `agenticiot-platform`。可携带 `turn_ref`。这里的 domain_ref 是入口外部引用，不是内部域 ID。

开发签发器为独立 CLI，无 HTTP 签发接口：

```sh
uv run python -m agenticiot.access.dev_token generate --key /ABSOLUTE/PRIVATE/PATH/entry.pem
uv run python -m agenticiot.access.service --issuer urn:agenticiot:entry-demo --client-id demo-app --external-domain home:demo --subject owner:demo
uv run python -m agenticiot.access.dev_token issue --key /ABSOLUTE/PRIVATE/PATH/entry.pem --turn-ref turn-001
```

先将生成的公钥登记到可信发行方配置，再执行第二条。私钥文件只允许所有者读取，不提交仓库；输出令牌视为秘密。引导命令默认给上述主体开发管理权限，可用 `--permissions` 收窄。正式产品由可信后端签发，用户注册和服务账号生命周期仍在入口系统。

## 二 登记和连接 Node

后台进入“节点与本地服务”，输入已获授权的管理令牌，登记节点。复制只显示一次的机器凭证，通过可信渠道设置 `AGENTICIOT_NODE_TOKEN`，然后运行：

```sh
uv run python -m agenticiot.node --url ws://127.0.0.1:8000/v1/nodes/channel --data-dir .local/node-demo
```

远端必须 WSS 并验证 TLS。CLI 不接受 URL 中的凭据、查询参数或任意路径。一个日志目录只给一个 Node 身份，进程锁避免并行打开；换机器凭证不删除日志。`POST /v1/management/nodes` 创建身份并启用机器凭证，但 `online=false`，直到 WSS 建立。停用通过 activation API；轮换返回新的单次显示凭证，不重新启用被停用节点。

后台“设备型号”发布虚拟灯具模板，“设备注册”创建设备，然后在详情中选择该 Node 绑定。Node 首次观测后才有新鲜状态；绑定不是在线证明。默认命令期限 30 秒。执行未知不会自动重发，并暂停该绑定后续派发。

MQTT 模拟器沿用原有 broker/device 启动方法，Node 使用 `--mqtt-port PORT --mqtt-namespace NAME`。该选项只接本机模拟 MQTT，不授权真实灯具动作。旧 `virtual_edge --once` HTTP 流程不再连接默认服务。

## 三 配置并批准本地模型服务

Node 本机配置文件示例：

```json
{"ollama":{"url":"http://127.0.0.1:11434/v1/chat/completions","models":["YOUR_INSTALLED_MODEL"]}}
```

用 `--services /ABSOLUTE/PATH/services.json` 启动 Node。这里只允许数字回环地址、HTTP、固定 `/v1/chat/completions` 路径；禁用重定向和环境 HTTP 代理。不下载模型，不扫描端口，也不更改宿主机出口策略。示例地址不是本次已经测通某个 Ollama 模型的声明。

后台注册服务：选择本域 Node、服务名称（如 `home-model`）、本机配置标识 `ollama`、获准模型。新记录是待批准，必须再点击“批准启用”。Node 本机白名单与平台记录共同限制调用。

调用 `POST /v1/chat/completions`：

```json
{"model":"home-model","messages":[{"role":"user","content":"你好"}],"stream":true,"max_tokens":512,"temperature":0.7}
```

这是严格文本子集：messages 只允许 system/user/assistant 的字符串 content；只允许上述参数及 `n=1`。不支持工具生成、图像、response_format、任意 URL 或云备用。max_tokens 为 1–4096，temperature 为 0–2。上游需提供 stop/length 结束原因及 `[DONE]`，否则结果未知。每个选定模型版本仍需单独兼容验收。

原 POST 返回 SSE；`X-Invocation-ID` 是调用 ID。`GET /v1/invocations/{id}` 仅返回调用元数据，同域其他主体也不能读取。响应开始后的中断返回流中错误且不发送成功 `[DONE]`。关闭客户端只尽力取消，未确认停止记为 unknown，而不是 cancelled。没有自动重发或恢复生成。

`Idempotency-Key` 可选。配置 `AGENTICIOT_INFERENCE_HMAC_KEY` 后才可使用；同键同输入返回 409 duplicate_invocation 和原调用 ID，不重放正文；不同输入返回冲突。记录保留 30 天，过期不再保证防重。轮换时将旧值保留在 `AGENTICIOT_INFERENCE_HMAC_PREVIOUS_KEYS` JSON 数组，直到该版本最后使用后满 30 天；所有部署配置应一致，不能直接删除有效旧密钥。

界面区分“Node 已连接”“服务已批准”和“服务健康未知”。未实现独立模型健康探针；“声明为本地”不代表已验证数据不出网，云中转本身也会看到请求内容。

## 四 Agent 工具和事件

`GET /v1/tools` 返回少量通用 HTTP 工具，按需查询设备 Schema。它不是 MCP 协议端点；工具直接复用设备服务，不运行 Agent。`operation_id` 从 LLM 参数 Schema 中排除，由 Runtime 注入。

Python 确定性适配器 `agenticiot.agent_client.DeviceTools` 接收入口持久化的 turn_ref，`invoke(operation_ref, thing_id=..., action=..., input=...)` 生成稳定操作 ID。同一意图重试保留两个引用；一轮中有意重复执行用不同 operation_ref。不要从模型内容或工具调用 ID 推断业务意图。首版只执行已验收的低风险模拟绝对设置动作，非幂等、高风险和真实硬件仍拒绝。

`GET /v1/events` 用 Bearer 鉴权订阅 SSE，以 `Last-Event-ID` 恢复；需要能设置头部的流式客户端。`GET /v1/events/page?cursor=...` 提供相同日志的有限分页，便于诊断。事件只为 sampled_change，包含前后值、采样窗口和 `complete_physical_history=false`。两次采样之间开又关可能完全不可见。

每域保留最近 1000 条，过期游标返回 resync_required。入口 Runtime 持久化已消费游标、按事件 ID 去重、应用确定性触发策略后唤醒 Agent；新动作使用稳定触发意图。平台不负责唤醒，MCP 通知也不是持久事件日志。

## 五 升级与验证边界

Migrate to `0011_runtime_recovery`. Existing domain mappings and resource identities are retained. Back up before migration; recovery evidence is forward-only, so cross-schema rollback requires a verified backup restoration plan.

旧 Edge 转 WSS 时，管理者用原 edge_ref 登记尚无机器凭证的节点，平台保留旧 Node ID 与绑定并签发新凭证。停止旧运行器，再以新凭证和原日志目录启动 Node；不要同时运行两个执行进程。对已托管节点重复登记返回冲突，不隐式轮换凭证。

运行测试见开发指南。当前只有模拟设备与模拟模型的自动化证据；真实模型、家庭 NAT/Wi-Fi、云中转时延、真实设备只读、Docker 全栈和生产部署验收分别记录，不能互相替代。
