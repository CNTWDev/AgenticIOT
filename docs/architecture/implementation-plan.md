# AgenticIoT 实施计划

Pilot hardening update: recovery/credits, clock-skew handling, independent heartbeat, bounded admission, console permissions and pre-migration recovery are implemented under [ADR 0012](../adr/0012-pilot-recovery-and-transport-bounds.md). The [review disposition](../operations/pilot-recovery.md) distinguishes implemented fixes from pending household-network, model, real-device and sustained-load validation.

版本：0.3。日期：2026-10-02。状态：首版软件链路已实现；真实模型、网络、硬件及生产门禁未完成。

依据[架构 v0.3](device-node-services-v0.3.md)与 [ADR 0009](../adr/0009-node-local-services-and-agent-access.md)。[v0.1 历史计划](implementation-plan-v0.1.md)保留原交付记录，不再决定下一步顺序。

## 已有基础

注册和能力模型、模拟设备、独立 Edge、HTTP 轮询、设备持久化命令、相关回执、SQLite 意图日志和 MQTT 模拟器已实现。这不证明真实设备、生产授权、原生事件或新的流式服务可用。

上述为原切片基础，默认部署现已由 WSS Node 替换 HTTP 轮询。旧 HTTP 端点只装配到历史回归测试，不在默认应用或实际 OpenAPI 中。

## 本次交付状态

| 里程碑 | 软件实现 | 尚未完成的门禁 |
| --- | --- | --- |
| P1 | 内部域迁移、别名/Grant、Ed25519 验证与开发签发器、权限交集、节点与服务管理、撤销 | 产品真实发行方集成；跨入口共享授权流程不开放 |
| P2 | WSS 客户端与服务端、PG 租约、SQLite 单一所有者、跨设备并发、重连证据、限额与排空启动器 | 公网 TLS 部署、容量与停机故障演练 |
| P3 | Node 本地端点白名单、严格文本流、元数据、可选 HMAC 幂等与轮换、unknown | 真实 Ollama/选定模型兼容，云与家庭网络配对时延；服务健康当前明确未知 |
| P4 | 通用 HTTP 工具和确定性 ID 适配器、采样事件分页/SSE、游标与有界保留 | 产品侧真实 Agent 唤醒；MCP 包装、原生事件及非幂等/高风险能力未开放 |
| H1 | 保持 ADR 0007 只读先行的准入约束 | 未选定或接入真实硬件，不能宣称完成 |

以下各节仍为完整验收目标，不能因软件测试通过将待实测门禁勾选完成。当前固定限额与运行方式见 [ADR 0011](../adr/0011-async-node-runtime.md)，账号激活规则见 [ADR 0010](../adr/0010-account-resource-activation.md)。

实测项目与缺口见[软件交付验证记录](../experiments/software-pilot-validation.md)。

## P1 Access 与内部域

- 实现内部 Domain、外部 DomainAlias 和独立 DomainGrant；保留已有资源和历史记录，回填迁移并验证约束。
- 开发用非对称签名发行器模拟可信入口，生产默认禁用该签发能力；现有静态 Token 经显式开发别名映射使用相同 RequestContext。
- 落实令牌、Grant、资源规则取交集；引导首个授权，加入域内管理权限和最小资源管理 API。
- 定义稳定意图与参数指纹契约；非幂等能力在实现严格意图验证前不得启用。

验收：跨域访问、未登记入口、自我提权、无授权别名、撤销后新操作全部被拒；同名外部域不合并，显式关联不搬迁设备；迁移不丢失模型、绑定或命令。只做最小 API，不做入网向导。跨入口自助 UX 可后续实现，但不得留下隐式共享路径。

## P2 Nodes 与设备调度

- 拆除 EdgeService 构造 operator Principal 的复用方式，Node 与用户授权分离。
- WSS 替换 HTTP 领取轮询，迁移模拟器、文档与测试，不保留长期双派发。
- NodeSession 在 PostgreSQL 保存当前代次、实例、期限；连接留在内存。
- 采用异步连接 IO、有界同步事务和 Adapter 执行器；观测与动作解耦，跨设备并发、同设备动作有序。
- 维护 SQLite 单一所有者或经验证的安全访问机制，保留执行意图、去重和证据恢复。
- 实现有限排空、重连、背压、忙碌响应和控制消息优先级。

验收：现有设备行为测试继续通过；13 个不可读绑定不阻塞独立在线设备派发；7 条突发命令不再因每轮只领取一条而积压过期；旧代次不领取新工作，重连补报不重复执行。按[实验计划](../experiments/node-relay-validation.md)测量，不把换成 WSS 等同于实时保证。

## P3 最小本地推理与实验

- 一个获准 Node 服务和模型白名单，先接一个实际本地模型，不先建通用云网关。
- POST 请求内流式、兼容配置白名单、元数据状态、可选幂等键及 HMAC 指纹；不持久化 prompt 或输出。
- 增加拒绝、取消、中断、unknown 和客户端重复请求测试；仅记录可确认用量。
- 按真实云主机和家庭 NAT/Wi-Fi 进行直连/中转配对测量；预算在运行前固定。

验收：选定服务通过兼容契约，正文不落持久化路径，unknown 不重发，重复键不派发，部分输出不重放或拼接；实验如实报告是否达预算。未通过时先评审链路，不扩大到云备用。

## P4 Agent 工具与设备事件

P1 后即可设计契约及工具原型，P2 后可接入设备链路，不必等 P3 完成；P3 的最小风险实验仍保持优先，不将其扩成平台项目。

- 薄工具或 MCP 接入现有设备 Service：发现、Schema、状态、动作和命令查询。
- 可信运行时维护 turn_ref、operation_ref 与稳定 operation_id，同操作变更参数冲突；高风险动作待单独确认能力完成才启用。
- 事件以 sampled_change 或 device_native 标明来源、采样窗口与缺口；保留期内带游标重放，校验权限并防止晚提交漏读。
- 原生订阅是 Adapter 可选能力，以实际支持协议验收；无订阅时不能宣称捕获瞬态事件。
- 用入口侧确定性事件消费者演示去重与 Agent 唤醒；MCP 不承担唤醒逻辑。

验收：外部 Agent 能发现获准设备，读出带新鲜度状态，提交动作并查询真实结果；相同意图不重放、同轮合法重复意图不误合并；采样缺失不被伪造；事件重放不触发重复动作。SSE 为首个传输，webhook 不阻塞此里程碑。

## H1 真实硬件只读验证

目标设备、固件、协议和授权就绪后，可与 P2–P4 并行。继续遵守 [ADR 0007](../adr/0007-first-physical-device-gates.md)与[设备接入计划](../hardware/first-device.md)：先确认目标，再只读，动作另行授权。设备未确定时保持未完成，不自动购买、扫描家庭网络或控制硬件。

验收：真实身份、观测新鲜度和断连行为可证实；模拟器不能替代这一门禁。完整首发发布需明确真实设备验证范围，不能把软件链路通过称为所有 IoT 能力已验证。

## 发布前门禁

记录受信发行方、Grant 撤销时限、事件与幂等保留期、凭据和 HMAC 轮换、并发及内存上限、租约和排空时间、迁移与备份恢复结果。部署缺少关键授权配置时拒绝启用。发布报告分别列出架构接受、接口完成、实现测试、真实网络实验和硬件证据状态。

首版不包含云模型连接、路由与备用、付费网关、多实例、离线授权、模型部署或通用工作流。新增需求须另立 ADR，不能绕过门禁成为临时补丁。
