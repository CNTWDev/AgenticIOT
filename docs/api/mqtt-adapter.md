# 标准 Adapter 与 MQTT 模拟灯具

适用范围更新（2026-10-02）：MQTT 末端协议、模拟器和意图日志规则仍有效；本文 Edge HTTP 启动流程已被 [WSS Node](node-local-services.md) 替代。使用 `python -m agenticiot.node --mqtt-port PORT --mqtt-namespace NAME`，机器凭证改为 `AGENTICIOT_NODE_TOKEN`；不要再按旧 HTTP 轮询命令启动默认平台链路。

状态：已实现本机协议验证，不是生产驱动，不连接真实设备。平台定位和外部 Agent/人与域职责不变。设计依据见 [ADR 0006](../adr/0006-minimal-adapters-and-mqtt-demo.md)。

```text
后台 / 外部调用者
    → 平台：授权、绑定、命令、观测证据、审计
    → 独立 Edge：HTTP 拉取、日志、防重放、Adapter 路由
        → virtual-light-v1：同一 SQLite 事务内的模拟灯
        → mqtt-light-demo-v1 → 本机 MQTT Broker → 独立模拟灯具进程
```

这里只新增协议末端。平台没有新增第二套消息队列，也不负责运行 Broker。

## 最小接口和职责

| 部件 | 做什么 | 不做什么 |
|---|---|---|
| 平台 | 校验 Edge 声明、同域绑定、低风险命令、结果关联 | MQTT 连接、主题拼装、人物记忆 |
| EdgeRuntime | 选择 Adapter、检查动作/截止时间、持久化意图/结果、顺序补传 | 自行生成新意图、自动重试未知物理动作 |
| Adapter | `read(thing_id)`、`invoke(command)` 返回带原始时间的观测；`close()` | 使用操作员凭证、决定域授权、把 PUBACK 当成功 |
| MQTT 模拟设备 | 灯具状态、按 command ID 持久去重、回传观测 | 真实硬件驱动或任意 MQTT 设备兼容 |

`journal_atomic=true` 只适用于设备状态与 Edge 日志共享事务的虚拟实现。MQTT 必须为 false；外部副作用不能与本地日志假装原子提交。

## 本机启动

先按 [开发指南](../development.md) 启动平台/后台，并配置同域操作员与**独立 Edge 凭证**。执行 `uv sync --frozen`、`make migrate` 到 `0004_adapters`，重启 API。

1. 独立终端启动开发 Broker，仅监听本机 18883：

```sh
uv run python -m agenticiot.mqtt_demo broker --port 18883
```

2. 独立终端安全设置 `AGENTICIOT_EDGE_TOKEN` 后启动 Edge（不要将令牌写进命令参数或文档）：

```sh
uv run python -m agenticiot.edge --url http://127.0.0.1:8000 --data-dir .local/mqtt-edge --mqtt-port 18883 --mqtt-namespace local-demo
```

如果升级既有虚拟 Edge，应停止旧进程并继续使用其原 `--data-dir`；不要让新日志共享已使用过的 Edge 身份。新的演示身份使用新的目录。切勿直接变更在用绑定的 namespace/设备映射；本切片尚无安全改绑迁移。

3. 后台发布“虚拟灯具模板”，注册设备，在详情中选择刚注册的 Edge，再选择 `mqtt-light-demo-v1`。复制该设备的 32 位 ID。新绑定不会立即变成在线。
4. 在第三个终端启动该设备模拟器，将 `<thing_id>` 替换为复制的 ID（不保留尖括号）：

```sh
uv run python -m agenticiot.mqtt_demo device --port 18883 --namespace local-demo --thing-id <thing_id> --data-dir .local/mqtt-device
```

5. 等待 Edge 周期并刷新后台，看到 power/brightness 观测；发出开关命令，刷新看到 `accepted → dispatched → acknowledged → confirmed` 及观测 ID。
6. 停止设备进程后再次请求动作：Broker 可能确认收到了消息，但没有设备证据，最终是 `execution_uncertain`。旧观测时间不被刷新，状态随后变陈旧。

每个模拟设备使用自己的目录和 ID，进程锁阻止共用日志。Ctrl-C 停止各进程；平台 Compose 不自动启动 Broker、Edge 或模拟设备。模拟器不自动重连，Broker 重启后需人工重启模拟器；Edge 下一周期重新建立有界连接。

## 已实现 API 增量

- `POST /v1/edge/register` 增加 `adapters`，可选值为两个固定 ID；省略时仅声明虚拟 Adapter，管理列表返回该声明。
- `PUT /v1/management/devices/{thing_id}/binding` 的 `adapter` 支持两者；Edge 未声明返回 `422 unsupported_adapter`，改绑 Edge 或 Adapter 返回 `409 binding_conflict`。
- Edge 不能移除正在使用的 Adapter，返回 `409 adapter_in_use`。
- 命令结果增加 `adapter` 字段；失败报告允许 `execution_uncertain`。所有模拟标记保留为 true。
- 原有能力动作、幂等键、域隔离、只读权限和确认规则不变。协议连接配置不放进北向接口。

## MQTT 消息契约 v1

固定 MQTT 3.1.1、TCP 127.0.0.1、QoS 1、`retain=false`。主题为：

```text
agenticiot/demo/<namespace>/<thing_id>/request
agenticiot/demo/<namespace>/<thing_id>/response
```

namespace 为 1–64 个字母/数字/下划线/连字符；不接受通配符或路径。主题不是安全隔离边界，本地其他进程仍可伪造消息。因此这个无认证演示 Broker **不得开放公网、桥接家庭 Broker 或使用真实设备主题**。

请求公共字段：`version:1`、`simulated:true`、`request_id`、`thing_id`、`operation`、`issued_at`、`deadline`。时间是带时区的 ISO 8601。读取使用随机 request ID；动作使用平台 command ID。

- 读取：`operation:"read"`，`action/input` 为空或省略。
- 动作：`operation:"invoke"`，`action:"set_power" | "set_brightness"`，`input:{"value":...}`。
- 响应：`version:1`、`simulated:true`、相同的 `request_id/thing_id`、`observed_at`、`values:{"power":boolean,"brightness":integer 0..100}`。

成功必须是订阅就绪后收到的相关观测。保留消息、错误目标/关联 ID、请求窗口外的时间、无效值、超过 4096 字节的业务消息均拒绝。MQTT 读取是协议层读取，不等于平台已经实现 `fresh=true` 北向同步读取（仍为 501）。

保留消息检查针对订阅时带 `retain` 标志的历史回放。MQTT 3.1.1 的实时转发不保留发布者的 retain 标志，不能把此检查当成 Broker 安全策略；本实现自身始终发布非保留请求/响应，生产端仍需专用 Broker 策略与设备认证。参见 [OASIS MQTT 3.1.1 §3.3.1.3](https://docs.oasis-open.org/mqtt/mqtt/v3.1.1/mqtt-v3.1.1.html)。

## 故障语义

| 情况 | 行为 |
|---|---|
| API 接受命令后 Edge 尚未领取 | 保留 PostgreSQL 命令；到期不再执行 |
| MQTT 没有设备回包，只有 Broker PUBACK | 不确认成功，记录结果不确定 |
| 外部调用前写入意图后进程崩溃 | 重启不重发，记录结果不确定；即使实际尚未发送也保守处理 |
| Edge 已记录结果但上传中断 | 按原序列、原时间、原结果补传 |
| QoS 重复投递同一请求 | 模拟设备查本地日志返回同一证据，不再次改变状态 |
| 相同 command ID 对应不同请求 | 模拟设备拒绝，不覆盖旧记录 |
| 设备/Broker 不可用 | 不生成新状态或新时间；其他绑定和命令继续按周期处理 |

“结果不确定”不等于“设备未执行”。本切片一旦提交该结果就不会被覆盖；后续普通状态只能说明当前值，不能证明是谁执行了先前命令。平台没有自动补偿、自动恢复未知命令或物理 exactly-once 保证。

## 下一步门槛

已细化为 [首个真实设备接入计划](../hardware/first-device.md)：先确认型号，再实现只读验证，最后单独批准受控动作。目前等待设备选择；接入卡和验收清单不改变现有 API 或运行权限。

只选一个真实设备，固定其协议与能力映射、设备认证、Broker TLS/ACL、凭证更新、超时/重连、去重/读回以及安全停机测试。完成这些前，不移除 `simulated` 限制，也不接入门锁等高风险动作。当前还没有通用插件市场、任意主题映射、BLE/ZigBee/Matter 接入、端侧 Agent 或“一人一域”系统。
