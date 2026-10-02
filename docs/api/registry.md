# 设备注册与能力发现 API

状态：第一条注册切片已实现。实际字段与错误契约以 `api/platform.openapi.json` 和运行时 `/docs` 为准。`api/openapi.yaml` 仍包含未实现的执行设计，不能作为所有端点均可用的证明。

后续实现：Node WSS、绑定、状态、命令和采样事件流已接入，范围见 [Node 与本地服务](node-local-services.md)。本文注册职责不变；真实硬件仍未开放。旧 `domain_ref` 响应字段现在承载内部域 ID；外部字符串通过显式别名映射。静态凭证也必须先建立平台 Grant，不能仅配置环境变量即自动获得授权。

## 职责边界

平台保存设备型号、类型化能力、设备归属引用和注册变更。外部系统拥有主体、域、空间及其成员关系；本模块不建立“一人一域”模型、不管理自然语言意图、不包含 Agent Runtime。Agent 可部署云端或端侧，均通过获授权的设备发现接口访问同样的资源边界。

注册不是连接：设备最初为 `commissioning`，可达性为 `unknown`。绑定虚拟 Edge 后进入 `active`，但直到观测上报前可达性仍未知。发现接口按最新观测时间投影在线/离线；不会在注册时触发设备行为。

## 授权

请求头：`Authorization: Bearer <local-api-token>`。

- `AGENTICIOT_API_CLIENTS` 是本地开发配置，空列表默认拒绝；没有内置超级管理员。
- 服务端把凭证映射到 `subject_ref / domain_ref / role`。请求体不能自行设置域；跨域资源返回 404，显式查询未获授权域返回 403。
- `viewer`：会话、型号和设备/能力的只读访问。
- `operator`：上述读取、发布型号、注册设备、编辑资料和读取注册审计。
- 凭证不返回给客户端；所有响应 `Cache-Control: no-store`。后台只在页面内存持有凭证。
- 这不是生产身份体系。外部 OIDC / grant 集成将在后续替换认证来源，资源侧授权仍保留。

## 已实现端点

所有路径均以 `/v1` 开头；管理后台使用同源 `/api` 代理。

| 方法 | 路径 | 职责 |
|---|---|---|
| GET | `/management/session` | 返回当前主体引用、授权域与角色 |
| POST / GET | `/management/models` | 发布不可变型号版本 / 分页读取 |
| GET | `/management/models/{model_id}` | 查看版本与能力定义 |
| POST / GET | `/management/devices` | 注册设备 / 分页读取 |
| GET / PATCH | `/management/devices/{device_id}` | 查看注册信息 / 条件更新名称与空间 |
| GET | `/management/audit` | 读取当前域成功变更的审计记录 |
| GET | `/things` | 发现设备；支持 `space_ref`、`capability` 过滤 |
| GET | `/things/{thing_id}` | 返回设备信息和完整类型化能力 |

ID 是平台生成的 32 位小写十六进制字符串；`external_ref` 是调用方提供的域内唯一设备标识，不承诺跨协议或跨域全局唯一。

### 型号发布

必填 `key / version / title`，可选 `description`；至少声明一个 property、action 或 event。

- 型号唯一键：`(domain_ref, key, version)`；版本仅接受 `major.minor.patch` 数字格式。
- 发布即冻结；不提供编辑或删除型号接口。修改能力需发布新版本，旧设备继续绑定旧版本。
- 属性声明 `schema / readable / writable / unit`；动作声明 `input_schema / output_schema / risk / confirmation / offline_policy`；事件声明 `data_schema`。
- JSON Schema 使用 2020-12 内联子集：根必须指定单一 `type`，不支持 `$ref / $dynamicRef / $recursiveRef / $id`，不会进行网络解析。
- 每个 Schema 至多 32 KiB、嵌套深度至多 16；每类最多 64 个能力，总能力目录至多 256 KiB（按规范化 JSON 计算）。
- 高风险或关键风险动作的 `confirmation` 不能为 `none`。这些字段是声明，不表示动作执行、安全确认或离线授权已实现。

### 设备注册与编辑

注册必填 `model_id / external_ref / title`，可选 `space_ref`；型号必须属于当前授权域。成功返回 201、`Location` 和 `ETag: "1"`。型号版本被固定，不随新版本自动升级。

PATCH 只接受 `title` 与 `space_ref`，至少一个字段；`space_ref: null` 表示清空，`title: null` 不合法。

```http
PATCH /v1/management/devices/<device_id>
Authorization: Bearer <local-api-token>
If-Match: "1"
Content-Type: application/json

{"title":"客厅主灯","space_ref":"room:living"}
```

成功返回更新对象和新 ETag。缺少 `If-Match` 返回 428，版本不匹配返回 412，数据库同时提供乐观锁保护。后台提示重新加载，不自动覆盖他人的修改。GET 管理设备接口返回当前 ETag。

### 分页与发现

列表响应为 `{items: [...], next_cursor: string | null}`。`limit` 默认 50、范围 1–200；按稳定资源 ID 排序，游标绑定资源类型、域和过滤条件。不得跨查询复用游标。并发新增不会提供快照一致性；需要完整当前集合时重新开始分页。审计列表目前同样按 ID 分页，不保证时间倒序。

`GET /things?capability=set_power&space_ref=room:living` 按能力名称和空间过滤；同名属性、动作或事件均可匹配。详情包含 Schema；观测与动作通过独立的 `/state` 与 `/actions/{action_name}` 端点访问。详情中未设置的可选字段可省略。

## 错误与审计

错误使用 `application/problem+json`：`type / title / status / code / trace_id`。不回显请求体、API 凭证、数据库连接串或 SQL。响应 `X-Trace-ID` 可关联成功变更记录。

| 状态码 | 典型 code | 含义 |
|---|---|---|
| 400 | `invalid_cursor` | 游标格式或查询范围不匹配 |
| 401 / 403 | `unauthorized / forbidden` | 凭证无效或授权不足 |
| 404 | `resource_not_found` | 当前域内不存在该资源 |
| 409 | `resource_conflict` | 型号版本、设备标识或引用约束冲突 |
| 412 / 428 | `revision_conflict / precondition_required` | 编辑冲突或缺少条件头 |
| 422 | `invalid_request` | 输入或能力 Schema 不合法 |
| 503 | `database_not_ready` | 数据库或所需业务表不可用 |

成功变更与 `model.published / device.registered / device.updated` 审计在同一事务提交。失败或冲突不会写入成功审计。审计只保存主体、域、资源、操作、时间及追踪 ID，不保存凭证或完整能力输入。当前不是防篡改日志，也不覆盖拒绝访问、登录失败或设备执行；这些能力需要后续独立设计。

## 下一条闭环

虚拟 Adapter 与 Edge 的上述执行闭环现已实现；下一步应验证更完整的故障恢复，再接入一种真实协议，而不是同时扩展协议大全或 Agent 编排。
