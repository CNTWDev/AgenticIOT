# 虚拟 Edge 与命令执行

适用范围更新（2026-10-02）：本文的能力、证据和设备 API 规则保留；下方 HTTP Edge 启动及领取流程是历史说明，默认应用已移除。使用 [Node 运行指南](node-local-services.md) 的管理凭证引导、机器凭证与 WSS。设备超时或 execution_uncertain 现在明确返回 `unknown`，不能当作确定失败重试。

状态：本地模拟闭环已实现，适用于 macOS/Linux。这里只执行虚拟灯具，不控制任何真实硬件。实际契约见 `api/platform.openapi.json` 和 `/docs`。

## 最小职责划分

- 管理后台：绑定已注册 Edge、请求动作、查看状态和回执；不自己生成模拟执行结果。
- 平台：认证和按域授权、验证能力与低风险策略、持久化命令、去重、维护观测及回执。
- 独立虚拟 Edge：维护本地模拟灯具，拉取已受理命令，检查动作/参数/截止时间，执行并上报结果。
- 外部 Agent / 人与域系统：本轮不构建。云端或端侧调用者都使用相同能力接口，不能借请求体提升权限。

平台的 PostgreSQL 命令记录同时充当持久化拉取队列，无需引入消息中间件或第二份发送 outbox。接受命令和初始回执/审计在一个事务提交后，Edge 才能领取。独立进程通过 HTTP 调用平台，不直接访问平台数据库。

## 启动与演示

先按 [开发指南](../development.md) 启动平台和管理后台，执行 `make migrate` 到 `0004_adapters`。

1. 额外生成一个随机 Edge 令牌，不得复用操作员令牌。令牌至少 32 字符，所有操作员与 Edge 令牌之间必须唯一。
2. 在平台 `.env` 中配置下面的 JSON，替换令牌，并确保 `domain_ref` 与操作员相同。它只是外部主体/域的引用，不会创建家庭或用户。

```dotenv
AGENTICIOT_EDGE_CLIENTS='[{"token":"REPLACE_WITH_A_SEPARATE_RANDOM_EDGE_TOKEN","edge_ref":"edge:local-light","domain_ref":"home:demo"}]'
```

3. 重启 API。启动独立终端进程，令牌只通过进程环境传入，不放 URL 或启动参数中，也不要提交到仓库：

```sh
# 先在当前终端安全地设置 AGENTICIOT_EDGE_TOKEN 为上面配置的 Edge 令牌。
uv run python -m agenticiot.virtual_edge --url http://127.0.0.1:8000 --data-dir .local/virtual-edge
```

正常时进程静默运行，每 5 秒注册/心跳、上报绑定灯具的观测，并至多处理一条命令；Ctrl-C 退出。仅调试一个周期可加 `--once`。此演示 CLI 只接受回环 HTTP origin，禁用环境代理并拒绝 HTTP 重定向，避免把凭证送往其他服务。

4. 后台发布“虚拟灯具模板”，注册设备，打开设备详情。
5. 在“Edge 与模拟执行”选择刚注册的 Edge，再选择 `virtual-light-v1` 并绑定。绑定不可改绑；一个设备只对应一个 Edge/Adapter，一个 Edge 最多 200 个绑定。另一个已实现选项见 [MQTT 模拟灯具](mqtt-adapter.md)。
6. 等待一个周期并点击“刷新状态与回执”，确认收到 `power / brightness` 观测。
7. 点击“发送虚拟命令”。先看到“已受理”，Edge 处理后刷新，看到“观测确认成功”与关联的观测 ID。

注册/绑定不会产生观测。模型必须包含模板中精确的 `power / brightness` 属性及 `set_power / set_brightness` 输入定义；只开放 `low + observed_state` 的这两个动作。额外声明的能力不会因此被自动实现，高风险、`local_only` 或不兼容模型拒绝执行/绑定。

## 已实现的 API

路径统一以 `/v1` 开头。

| 调用方 | 方法与路径 | 职责 |
|---|---|---|
| 操作员/只读 | `GET /management/edges` | 当前域已注册 Edge，至多 200 个；不含凭证 |
| 操作员 | `PUT /management/devices/{thing_id}/binding` | 固定虚拟绑定；同目标重试返回原绑定，改绑返回 409 |
| 操作员/只读 | `GET /things/{thing_id}/binding` | 当前绑定，未绑定返回 JSON null |
| 操作员/只读 | `GET /things/{thing_id}/state` | 最新观测快照与来源、新鲜度 |
| 操作员 | `POST /things/{thing_id}/actions/{action_name}` | 异步受理低风险虚拟动作 |
| 操作员/只读 | `GET /things/{thing_id}/commands` | 最近 20 条命令及回执，不是无限历史查询 |
| 操作员/只读 | `GET /commands/{command_id}` | 命令状态与有序回执 |
| 操作员/只读 | `GET /commands/{command_id}/receipts` | 按序号返回回执 |
| Edge | `POST /edge/register` | 用自身凭证注册身份、版本、支持的 adapters 及心跳 |
| Edge | `GET /edge/bindings` | 仅自己的设备分配 |
| Edge | `POST /edge/commands/claim` | 领取或重取自己的待执行命令；无命令返回 null |
| Edge | `POST /edge/things/{thing_id}/observations` | 上报自身绑定设备的全量灯具快照 |
| Edge | `POST /edge/commands/{command_id}/result` | 提交一次不可变结果；完全相同的重传返回原结果 |

操作员令牌不能访问 Edge 通道，Edge 令牌不能访问北向/管理接口。Edge 的身份、域及命令分配全部由服务端解析，上传者不能自行宣称另一个 Edge 或域。

### 命令受理与重试

```http
POST /v1/things/<thing_id>/actions/set_power
Authorization: Bearer <operator-token>
Idempotency-Key: <unique-intent-id>
Content-Type: application/json

{"input":{"value":true}}
```

返回 202 与 `Location: /v1/commands/<command_id>`。可选 `deadline` 必须带时区、在未来五分钟内；省略时默认 30 秒。当前不接受 `context`，不能传入未经验证的主体、域或授权引用。

同一域、同一主体、同一幂等键的完全相同请求返回原命令；目标、动作、输入或显式截止时间改变则返回 `409 idempotency_conflict`。省略截止时间的重试仍返回原命令，不延长截止时间。请求正常化按 JSON 对象键排序，不进行数值/文本的语义等价推断。

后台在提交结果未知时保留原请求与幂等键；重试不会自动生成新命令。显式停止重试并不取消可能已受理的命令。页面刷新/退出后此临时重试状态不保留，应先查看命令历史。

### 确认与超时

正常回执序列为 `accepted → dispatched → acknowledged → confirmed`。前三者不能冒充物理完成；最后一步必须具有指定 Edge、指定命令、调度后产生且目标值匹配的观测。所有结果都标记 `simulated: true`。

- 普通状态上报即使值恰好匹配，也不会直接确认命令。
- 参数、时间或来源不合法的报告被拒绝；值不匹配产生 `failed / confirmation_mismatch` 回执并保存实际观测。
- 未调度即到期为 `expired`；已调度但未确认到期为 `timed_out`，公开状态为 `failed`。
- 超时在领取/查询/结果处理时惰性结算，没有额外定时调度器。读到的状态会先完成结算。
- 超时不证明未执行。晚到且相关的确认可补充 `confirmed`，原超时回执保留；平台不会因为超时自动新建重试命令。
- 已完成报告不可覆盖。`succeeded` 表示历史动作得到证据，不保证未来设备状态永远相同；本阶段不实现 `DIVERGED` 检测。

### 状态新鲜度

每次上报包含 `source_sequence / observed_at / values`；服务端记录独立 `received_at`。全量快照须有严格布尔 `power` 与 0–100 整数 `brightness`。

序列按 Edge 单调递增，重复序列只有内容完全相同才可重传，且不刷新原接收时间。未来超过 5 秒的观测拒绝，旧时间观测不会覆盖新时间投影。新鲜度按观测时间计算：10 秒内 `fresh`、30 秒内 `aging`、超过 30 秒 `stale`。设备可达性随查询投影为 online/offline；没有观测是 unknown。

网页显示的是上次查询的快照，点击刷新更新新鲜度。`fresh=true` 同步物理读取尚未实现，明确返回 501，不会拿缓存冒充新读取。状态上报不递增设备元数据 ETag；绑定会改变生命周期和资料版本。

## 持久化和故障边界

- PostgreSQL：新增 6 张运行表；记录命令、绑定、不可变观测、最新状态、回执与 Edge 身份。成功受理/终态与注册审计同事务提交。
- 开发迁移降级会删除运行数据，把有虚拟绑定的 active 设备恢复为 commissioning 并递增资料版本；型号和设备记录保留。升级不会自动恢复被删除的执行历史。不要在非可丢弃数据上随意降级。
- `0004_adapters` 降级遇到 MQTT 绑定会拒绝，防止旧运行器把它误当成本地虚拟灯具；需要明确的数据迁移方案，不能直接删除列。
- Edge SQLite：模拟灯具状态、命令去重日志与待上传结果同事务持久化；未获服务端确认前不移除待上传记录。恢复后先按序补传，再生成新观测/领取命令。
- 同一运行目录使用进程锁，仅允许一个本地进程。不要让多个独立日志共享同一 Edge 身份，也不要删除在用日志后重新开始序号。
- 运行目录固定绑定一个服务端 Edge ID。换数据库、重建身份或回滚运行表后需要独立的新演示目录；不能随意重用旧日志。
- 这只证明虚拟状态的事务性去重，不保证任意真实硬件协议的 exactly-once。没有多主 fencing、租约接管或生产级设备身份。
- MQTT Adapter 的外部发送不能加入 Edge SQLite 事务；发送前持久化意图。崩溃或结果丢失记录 `execution_uncertain`，不自动重发、不声称设备没有执行。详见 MQTT 指南。
- 网络断开时不受理新的本地意图，不执行离线授权；已经本地提交的结果留在 outbox 等待补传。声明的离线策略在本切片中不作为放行依据。
- 审计记录成功运行转换，不是防篡改日志，不完整覆盖拒绝访问或输入校验失败。运行审计复用 `/management/audit`，不会记录令牌。

未实现：mTLS/OIDC、外部授权撤销与高风险确认、BLE/WiFi/ZigBee/Matter 协议接入、SSE/Webhook、同步新鲜读取、通用适配器插件、断网自治、数据保留/压缩与生产部署。Compose 仅启动平台，不自动创建 Edge 令牌或启动执行进程。
