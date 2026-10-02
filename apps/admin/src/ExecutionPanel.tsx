import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type FormEvent,
} from "react";
import { registryRequest, RegistryError, type Page } from "./registry-api";

type Binding = {
  id: string;
  edge_id: string;
  adapter: string;
  simulated: true;
};
type Edge = {
  id: string;
  title: string;
  edge_ref: string;
  last_seen_at: string;
  adapters: string[];
};
type Observation = {
  value: unknown;
  source_ref: string;
  observed_at: string;
  freshness: string;
  source_version: string;
};
type State = {
  properties: Record<string, Observation>;
  observation_id?: string;
};
type Receipt = {
  sequence: number;
  stage: string;
  occurred_at: string;
  error_code: string | null;
  evidence: Record<string, unknown>;
};
type Command = {
  id: string;
  action: string;
  status: string;
  trace_id: string;
  receipts: Receipt[];
  blocked?: boolean;
  barrier_at?: string;
};
type Snapshot = {
  binding: Binding | null;
  edges: Edge[];
  state: State;
  commands: Command[];
};
const statusLabels: Record<string, string> = {
  accepted: "已受理",
  running: "执行中",
  succeeded: "观测确认成功",
  failed: "失败",
  unknown: "结果未知，请勿自动重试",
  expired: "已过期",
};
const stageLabels: Record<string, string> = {
  accepted: "持久化受理",
  dispatched: "已交给 Edge",
  acknowledged: "适配器确认",
  confirmed: "观测匹配",
  failed: "失败或结果未确认",
  expired: "未执行即过期",
  timed_out: "确认超时",
};
const freshnessLabels: Record<string, string> = {
  fresh: "新鲜",
  aging: "逐渐陈旧",
  stale: "已陈旧",
};

export default function ExecutionPanel({
  token,
  thingId,
  operator,
  actuator,
  onRefreshDevice,
}: {
  token: string;
  thingId: string;
  operator: boolean;
  actuator: boolean;
  onRefreshDevice: () => Promise<void>;
}) {
  const [snapshot, setSnapshot] = useState<Snapshot>();
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [action, setAction] = useState("set_power");
  const [power, setPower] = useState("true");
  const [brightness, setBrightness] = useState(50);
  const [selectedEdge, setSelectedEdge] = useState("");
  const [pending, setPending] = useState<{
    key: string;
    action: string;
    input: { value: boolean | number };
  }>();
  const epoch = useRef(0);
  const reload = useCallback(() => {
    const current = ++epoch.current;
    return Promise.all([
      registryRequest<Binding | null>(token, `/things/${thingId}/binding`),
      registryRequest<Page<Edge>>(token, "/management/edges"),
      registryRequest<State>(token, `/things/${thingId}/state`),
      registryRequest<Command[]>(token, `/things/${thingId}/commands`),
    ])
      .then(([binding, edges, state, commands]) => {
        if (epoch.current === current)
          setSnapshot({ binding, edges: edges.items, state, commands });
      })
      .catch((failure: Error) => {
        if (epoch.current === current) setError(failure.message);
      });
  }, [thingId, token]);
  useEffect(() => {
    void reload();
    return () => {
      epoch.current += 1;
    };
  }, [reload]);

  async function refresh() {
    setBusy(true);
    setError("");
    setNotice("");
    await reload();
    await onRefreshDevice();
    setBusy(false);
  }

  async function bind(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const fields = new FormData(event.currentTarget);
    setError("");
    setBusy(true);
    try {
      await registryRequest(token, `/management/devices/${thingId}/binding`, {
        method: "PUT",
        body: JSON.stringify({
          edge_id: fields.get("edge"),
          adapter: fields.get("adapter"),
        }),
      });
      setNotice(
        "已绑定模拟设备 Adapter。请运行 Edge 和相应设备进程并刷新观测；绑定本身不会产生设备状态。",
      );
      await reload();
      await onRefreshDevice();
    } catch (failure) {
      setError((failure as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    setError("");
    setNotice("");
    setBusy(true);
    try {
      if (!crypto.randomUUID)
        throw new Error("Use HTTPS or localhost to issue commands securely.");
      const intent = pending ?? {
        key: crypto.randomUUID(),
        action,
        input: {
          value: action === "set_power" ? power === "true" : brightness,
        },
      };
      setPending(intent);
      const result = await registryRequest<Command>(
        token,
        `/things/${thingId}/actions/${intent.action}`,
        {
          method: "POST",
          headers: { "Idempotency-Key": intent.key },
          body: JSON.stringify({ input: intent.input }),
        },
      );
      setPending(undefined);
      setNotice(
        `命令 ${result.id}：${statusLabels[result.status] ?? result.status}。受理不代表执行成功，请查看回执。`,
      );
      await reload();
    } catch (failure) {
      if (
        failure instanceof RegistryError &&
        failure.status >= 400 &&
        failure.status < 500 &&
        failure.status !== 408
      )
        setPending(undefined);
      setError((failure as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="execution-panel" aria-label="虚拟执行">
      <div className="section-heading">
        <h3>Edge 与模拟执行</h3>
        <button
          className="refresh"
          onClick={() => void refresh()}
          disabled={busy}
        >
          刷新状态与回执
        </button>
      </div>
      <p className="simulation-banner">
        SIMULATED · 支持本地虚拟灯具与 MQTT 模拟灯具，不连接真实硬件。
        数据来自独立 Edge 进程；MQTT 发送成功不代表设备执行成功。
      </p>
      {error && (
        <p className="registry-error" role="alert">
          {error}
        </p>
      )}
      {notice && (
        <p className="registry-notice" role="status">
          {notice}
        </p>
      )}
      {!snapshot ? (
        <p>正在读取执行信息…</p>
      ) : (
        <>
          {!snapshot.binding ? (
            <>
              <p className="detail-meta">
                尚未绑定执行终端。仅支持“虚拟灯具模板”，不支持改绑。
              </p>
              {operator && (
                <form
                  className="registry-form"
                  onSubmit={(event) => void bind(event)}
                >
                  <fieldset disabled={busy}>
                    <label>
                      已注册 Edge
                      <select
                        name="edge"
                        required
                        value={selectedEdge}
                        onChange={(event) =>
                          setSelectedEdge(event.target.value)
                        }
                      >
                        <option value="" disabled>
                          选择同域 Edge
                        </option>
                        {snapshot.edges.map((edge) => (
                          <option key={edge.id} value={edge.id}>
                            {edge.title} ({edge.edge_ref})
                          </option>
                        ))}
                      </select>
                    </label>
                    <label>
                      设备 Adapter
                      <select
                        name="adapter"
                        key={selectedEdge}
                        required
                        defaultValue=""
                      >
                        <option value="" disabled>
                          选择 Edge 已声明的 Adapter
                        </option>
                        {snapshot.edges
                          .find((edge) => edge.id === selectedEdge)
                          ?.adapters.map((adapter) => (
                            <option key={adapter} value={adapter}>
                              {adapter}
                            </option>
                          ))}
                      </select>
                    </label>
                    <button
                      className="primary-button"
                      disabled={!snapshot.edges.length}
                    >
                      绑定虚拟 Edge
                    </button>
                  </fieldset>
                  {!snapshot.edges.length && (
                    <p className="detail-meta">
                      当前域没有 Edge。先按开发指南配置独立 Edge
                      凭证并启动虚拟进程，再刷新。
                    </p>
                  )}
                </form>
              )}
            </>
          ) : (
            <p className="detail-meta">
              已绑定 {snapshot.binding.adapter} · Edge{" "}
              {snapshot.binding.edge_id}
            </p>
          )}
          <h4>最新观测（新鲜度按上次查询计算）</h4>
          {!Object.keys(snapshot.state.properties).length ? (
            <p className="detail-meta">暂无设备观测，状态未知。</p>
          ) : (
            <div className="observation-grid">
              {Object.entries(snapshot.state.properties).map(
                ([name, observed]) => (
                  <article className="observation-card" key={name}>
                    <span>{name}</span>
                    <strong>{JSON.stringify(observed.value)}</strong>
                    <small>
                      {freshnessLabels[observed.freshness] ??
                        observed.freshness}{" "}
                      · 序列 {observed.source_version}
                    </small>
                    <small>
                      {new Date(observed.observed_at).toLocaleString("zh-CN")}
                    </small>
                    <small>来源 {observed.source_ref}</small>
                  </article>
                ),
              )}
            </div>
          )}
          {actuator && snapshot.binding && (
            <form
              className="registry-form command-form"
              onSubmit={(event) => void submit(event)}
            >
              <h4>请求虚拟动作</h4>
              <fieldset disabled={busy || !!pending}>
                <div className="form-grid">
                  <label>
                    动作
                    <select
                      value={action}
                      onChange={(event) => setAction(event.target.value)}
                    >
                      <option value="set_power">set_power · 开关</option>
                      <option value="set_brightness">
                        set_brightness · 亮度
                      </option>
                    </select>
                  </label>
                  {action === "set_power" ? (
                    <label>
                      开关目标
                      <select
                        value={power}
                        onChange={(event) => setPower(event.target.value)}
                      >
                        <option value="true">开启</option>
                        <option value="false">关闭</option>
                      </select>
                    </label>
                  ) : (
                    <label>
                      目标亮度
                      <input
                        type="number"
                        min={0}
                        max={100}
                        step={1}
                        required
                        value={brightness}
                        onChange={(event) =>
                          setBrightness(Number(event.target.value))
                        }
                      />
                    </label>
                  )}
                </div>
              </fieldset>
              <button className="primary-button" disabled={busy}>
                {pending ? "用原请求重试" : "发送虚拟命令"}
              </button>
              {pending && (
                <div>
                  <p className="detail-meta">
                    上次提交结果未知，重试保留相同幂等键，不自动创建新命令。停止重试不会取消可能已受理的命令。
                  </p>
                  <button
                    type="button"
                    className="text-button"
                    disabled={busy}
                    onClick={() => setPending(undefined)}
                  >
                    停止此次重试（不取消命令）
                  </button>
                </div>
              )}
            </form>
          )}
          <h4>最近 20 条命令与回执</h4>
          {!snapshot.commands.length && (
            <p className="detail-meta">暂无命令。</p>
          )}
          {snapshot.commands.map((command) => (
            <details className="command-card" key={command.id} open>
              <summary>
                {command.action} ·{" "}
                {statusLabels[command.status] ?? command.status}
              </summary>
              <code>{command.id}</code>
              {command.blocked && (
                <p>
                  Dispatch paused until execution uncertainty is reconciled.
                </p>
              )}
              {operator &&
                command.blocked &&
                command.barrier_at &&
                snapshot.state.observation_id && (
                  <button
                    disabled={busy}
                    onClick={async () => {
                      const reason = window.prompt(
                        "Inspect the current device state, then explain why later commands may proceed (at least 10 characters). This does not mark the previous command successful.",
                      );
                      if (!reason || reason.trim().length < 10) return;
                      setBusy(true);
                      setError("");
                      try {
                        await registryRequest(
                          token,
                          `/commands/${command.id}/reconcile`,
                          {
                            method: "POST",
                            body: JSON.stringify({
                              observation_id: snapshot.state.observation_id,
                              reason: reason.trim(),
                            }),
                          },
                        );
                        await reload();
                      } catch (failure) {
                        setError((failure as Error).message);
                      } finally {
                        setBusy(false);
                      }
                    }}
                  >
                    Reconcile and release dispatch
                  </button>
                )}
              <ol>
                {command.receipts.map((receipt) => (
                  <li key={receipt.sequence}>
                    <span>{stageLabels[receipt.stage] ?? receipt.stage}</span>
                    <small>
                      {new Date(receipt.occurred_at).toLocaleTimeString(
                        "zh-CN",
                      )}
                      {receipt.error_code ? ` · ${receipt.error_code}` : ""}
                    </small>
                    {typeof receipt.evidence.observation_id === "string" && (
                      <code>观测 {receipt.evidence.observation_id}</code>
                    )}
                    {receipt.error_code === "execution_uncertain" && (
                      <small>
                        设备可能已执行。系统未自动重发，请先核查设备状态。
                      </small>
                    )}
                  </li>
                ))}
              </ol>
              <small>追踪 {command.trace_id}</small>
            </details>
          ))}
        </>
      )}
    </section>
  );
}
