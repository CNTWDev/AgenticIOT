import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type FormEvent,
} from "react";
import {
  ArrowUpRight,
  Box,
  KeyRound,
  LogOut,
  Plus,
  RefreshCw,
  ShieldCheck,
  X,
} from "lucide-react";
import {
  registryRequest,
  virtualLightCapabilities,
  type Audit,
  type Device,
  type Model,
  type Page,
  type SessionInfo,
  type Thing,
} from "./registry-api";
import ExecutionPanel from "./ExecutionPanel";

export type RegistryView = "models" | "devices" | "audit";
type Connection = SessionInfo & { token: string };
const titles = { models: "设备型号", devices: "设备注册", audit: "注册审计" };
const lifecycleLabels: Record<string, string> = {
  commissioning: "待接入",
  active: "已启用",
  suspended: "已暂停",
  retiring: "退役中",
  retired: "已退役",
};
const reachabilityLabels: Record<string, string> = {
  online: "在线",
  sleeping: "休眠",
  offline: "离线",
  unknown: "未观测",
};

export default function RegistryConsole({ view }: { view: RegistryView }) {
  const [connection, setConnection] = useState<Connection>();
  return (
    <RegistryWorkspace
      key={`${view}:${connection?.domain_ref ?? ""}:${connection?.subject_ref ?? ""}:${connection?.role ?? ""}`}
      view={view}
      connection={connection}
      setConnection={setConnection}
    />
  );
}

function RegistryWorkspace({
  view,
  connection,
  setConnection,
}: {
  view: RegistryView;
  connection: Connection | undefined;
  setConnection: (value: Connection | undefined) => void;
}) {
  const [tokenInput, setTokenInput] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(!!connection);
  const [rows, setRows] = useState<(Model | Device | Audit)[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [form, setForm] = useState(false);
  const [selected, setSelected] = useState<Model | Thing>();
  const requestEpoch = useRef(0);

  const reload = useCallback(
    (next?: string) => {
      if (!connection) return Promise.resolve();
      const epoch = ++requestEpoch.current;
      return registryRequest<Page<Model | Device | Audit>>(
        connection.token,
        `/management/${view}?limit=30${next ? `&cursor=${encodeURIComponent(next)}` : ""}`,
      )
        .then((result) => {
          if (epoch !== requestEpoch.current) return;
          setRows((old) => (next ? [...old, ...result.items] : result.items));
          setCursor(result.next_cursor);
        })
        .catch((failure: Error) => {
          if (epoch === requestEpoch.current)
            setError((failure as Error).message);
        })
        .finally(() => {
          if (epoch === requestEpoch.current) setLoading(false);
        });
    },
    [connection, view],
  );

  useEffect(() => {
    void reload();
    return () => {
      requestEpoch.current += 1;
    };
  }, [reload]);

  function refresh(next?: string) {
    setLoading(true);
    setError("");
    void reload(next);
  }

  async function connect(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      const info = await registryRequest<SessionInfo>(
        tokenInput.trim(),
        "/management/session",
      );
      setConnection({ ...info, token: tokenInput.trim() });
      setTokenInput("");
    } catch (failure) {
      setError((failure as Error).message);
    } finally {
      setBusy(false);
    }
  }

  function disconnect() {
    requestEpoch.current += 1;
    setConnection(undefined);
    setRows([]);
    setSelected(undefined);
    setForm(false);
    setError("");
    setNotice("");
    setLoading(false);
    setTokenInput("");
  }

  async function inspect(row: Model | Device) {
    if (!connection) return;
    setBusy(true);
    setError("");
    try {
      const detail = await registryRequest<Model | Thing>(
        connection.token,
        "key" in row ? `/management/models/${row.id}` : `/things/${row.id}`,
      );
      setSelected(detail);
      setForm(false);
    } catch (failure) {
      setError((failure as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function created(resource: Model | Device, edited = false) {
    setForm(false);
    setNotice(
      "key" in resource
        ? "型号版本已发布，能力定义已锁定。"
        : edited
          ? "设备资料已更新。"
          : "设备已注册，等待 Edge 接入。",
    );
    setLoading(true);
    await reload();
    await inspect(resource);
  }

  return (
    <section className="registry-page">
      <div className="page-heading">
        <div>
          <p className="eyebrow">DEVICE REGISTRY</p>
          <h1>{titles[view]}</h1>
          <p className="subtitle">
            {view === "models"
              ? "发布明确、可复用的设备能力，每个版本独立保存。"
              : view === "devices"
                ? "将设备关联到已发布型号，并在当前授权域内管理。"
                : "查看型号发布和设备变更的操作记录。"}
          </p>
        </div>
        {connection && (
          <div className="registry-actions">
            <button
              className="refresh"
              onClick={() => refresh()}
              disabled={loading || busy}
            >
              <RefreshCw size={15} />
              刷新列表
            </button>
            {view !== "audit" && connection.role === "operator" && (
              <button
                className="primary-button"
                onClick={() => {
                  setForm(true);
                  setSelected(undefined);
                  setError("");
                }}
                disabled={busy}
              >
                <Plus size={16} />
                {view === "models" ? "发布型号" : "注册设备"}
              </button>
            )}
          </div>
        )}
      </div>
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
      {!connection ? (
        <form
          className="panel connection-form"
          onSubmit={(event) => void connect(event)}
        >
          <KeyRound size={25} />
          <h2>连接管理工作区</h2>
          <p>使用服务端配置的本地 API 凭证，访问获授权的设备域。</p>
          <label>
            API 凭证
            <input
              type="password"
              autoComplete="off"
              required
              value={tokenInput}
              onChange={(event) => setTokenInput(event.target.value)}
            />
          </label>
          <button className="primary-button" disabled={busy}>
            {busy ? "验证中…" : "连接工作区"}
          </button>
          <small>凭证仅保存在本次页面内存中，刷新或断开后需要重新输入。</small>
        </form>
      ) : (
        <>
          <div className="connection-strip">
            <ShieldCheck size={16} />
            <span>{connection.domain_ref}</span>
            <small>
              {connection.subject_ref} ·{" "}
              {connection.role === "operator" ? "操作员" : "只读"}
            </small>
            <button
              onClick={disconnect}
              className="text-button"
              disabled={busy}
            >
              <LogOut size={15} />
              断开
            </button>
          </div>
          {form && (
            <div className="panel editor-panel">
              <div className="section-heading">
                <h2>{view === "models" ? "发布新型号版本" : "注册新设备"}</h2>
                <button
                  aria-label="关闭表单"
                  className="icon-button"
                  disabled={busy}
                  onClick={() => setForm(false)}
                >
                  <X size={18} />
                </button>
              </div>
              {view === "models" ? (
                <ModelForm
                  token={connection.token}
                  onCreated={created}
                  setError={setError}
                  busy={busy}
                  setBusy={setBusy}
                />
              ) : (
                <DeviceForm
                  token={connection.token}
                  onCreated={created}
                  setError={setError}
                  busy={busy}
                  setBusy={setBusy}
                />
              )}
            </div>
          )}
          <div className="panel registry-list" aria-busy={loading}>
            <div className="section-heading">
              <h2>
                {view === "models"
                  ? "已发布型号版本"
                  : view === "devices"
                    ? "已注册设备"
                    : "变更记录"}
              </h2>
              <span>{rows.length} 条已加载</span>
            </div>
            {loading && rows.length === 0 ? (
              <p className="empty-state">正在读取…</p>
            ) : rows.length === 0 ? (
              <div className="empty-state">
                <Box size={28} />
                <p>
                  {error
                    ? "列表暂不可用，请处理上方提示后刷新。"
                    : view === "models"
                      ? "尚无型号，请先发布一个能力定义。"
                      : view === "devices"
                        ? "尚无注册设备，请选择已发布型号开始。"
                        : "尚无注册操作记录。"}
                </p>
              </div>
            ) : (
              <div className="registry-table-wrap">
                <table className="registry-table">
                  <thead>
                    <tr>
                      {view === "audit" ? (
                        <>
                          <th>操作</th>
                          <th>资源</th>
                          <th>主体 / 时间</th>
                          <th>追踪</th>
                        </>
                      ) : (
                        <>
                          <th>{view === "models" ? "型号" : "设备"}</th>
                          <th>版本 / 标识</th>
                          <th>{view === "models" ? "能力" : "连接状态"}</th>
                          <th>详情</th>
                        </>
                      )}
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((row) =>
                      "operation" in row ? (
                        <tr key={row.id}>
                          <td>{operationLabel(row.operation)}</td>
                          <td>
                            <code>{row.resource_id}</code>
                          </td>
                          <td>
                            {row.subject_ref}
                            <small>
                              {new Date(row.occurred_at).toLocaleString(
                                "zh-CN",
                              )}
                            </small>
                          </td>
                          <td>
                            <code>{row.trace_id}</code>
                          </td>
                        </tr>
                      ) : (
                        <tr key={row.id}>
                          <td>
                            <strong>{row.title}</strong>
                            <small>
                              {"key" in row
                                ? row.key
                                : (row.space_ref ?? "未分配空间")}
                            </small>
                          </td>
                          <td>
                            {"key" in row ? row.version : row.model_version}
                            {"external_ref" in row && (
                              <small>{row.external_ref}</small>
                            )}
                          </td>
                          <td>
                            {"key" in row ? (
                              `${Object.keys(row.properties).length} 属性 · ${Object.keys(row.actions).length} 动作 · ${Object.keys(row.events).length} 事件`
                            ) : (
                              <>
                                <span className="outline-badge">
                                  {lifecycleLabels[row.lifecycle_status] ??
                                    row.lifecycle_status}
                                </span>
                                <small>
                                  连接：
                                  {reachabilityLabels[row.reachability] ??
                                    row.reachability}
                                </small>
                              </>
                            )}
                          </td>
                          <td>
                            <button
                              className="text-button"
                              aria-label={`查看 ${row.title}`}
                              onClick={() => void inspect(row)}
                              disabled={busy}
                            >
                              查看
                              <ArrowUpRight size={14} />
                            </button>
                          </td>
                        </tr>
                      ),
                    )}
                  </tbody>
                </table>
              </div>
            )}
            {cursor && (
              <button
                className="refresh load-more"
                onClick={() => refresh(cursor)}
                disabled={loading}
              >
                加载更多
              </button>
            )}
          </div>
          {selected && (
            <section className="panel detail-panel" aria-label="资源详情">
              <div className="section-heading">
                <h2>{selected.title}</h2>
                <button
                  className="icon-button"
                  aria-label="关闭详情"
                  onClick={() => setSelected(undefined)}
                >
                  <X size={18} />
                </button>
              </div>
              <p className="detail-meta">
                {"key" in selected
                  ? `已发布 · ${selected.key} / ${selected.version} · 不可修改`
                  : `设备 ${selected.external_ref} · 型号版本 ${selected.model_version}`}
              </p>
              <code className="resource-id">{selected.id}</code>
              {!("key" in selected) && connection.role === "operator" && (
                <EditDevice
                  key={`${selected.id}:${selected.revision}`}
                  device={selected}
                  token={connection.token}
                  onSaved={(resource) => created(resource, true)}
                  onReload={() => void inspect(selected)}
                  setError={setError}
                  busy={busy}
                  setBusy={setBusy}
                />
              )}
              <h3>能力定义</h3>
              <p className="detail-meta">
                以下是设备声明的能力；仅绑定虚拟灯具适配器后可请求模拟动作。
              </p>
              <div className="capability-groups">
                {(["properties", "actions", "events"] as const).map((kind) => (
                  <div key={kind}>
                    <h4>
                      {
                        { properties: "属性", actions: "动作", events: "事件" }[
                          kind
                        ]
                      }{" "}
                      <span>{Object.keys(selected[kind]).length}</span>
                    </h4>
                    {Object.entries(selected[kind]).map(([name, schema]) => (
                      <details key={name}>
                        <summary>{name}</summary>
                        <pre>{JSON.stringify(schema, null, 2)}</pre>
                      </details>
                    ))}
                  </div>
                ))}
              </div>
              {!("key" in selected) && (
                <ExecutionPanel
                  key={selected.id}
                  token={connection.token}
                  thingId={selected.id}
                  operator={connection.role === "operator"}
                  onRefreshDevice={async () => {
                    await reload();
                    await inspect(selected);
                  }}
                />
              )}
            </section>
          )}
        </>
      )}
    </section>
  );
}

type FormProps = {
  token: string;
  onCreated: (resource: Model | Device) => Promise<void>;
  setError: (message: string) => void;
  busy: boolean;
  setBusy: (value: boolean) => void;
};

function ModelForm({ token, onCreated, setError, busy, setBusy }: FormProps) {
  const [definition, setDefinition] = useState(
    '{"properties": {}, "actions": {}, "events": {}}',
  );
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    setBusy(true);
    const fields = new FormData(event.currentTarget);
    try {
      const capabilities = JSON.parse(definition);
      if (
        !capabilities ||
        Array.isArray(capabilities) ||
        typeof capabilities !== "object"
      )
        throw new Error("能力定义必须是 JSON 对象。");
      const result = await registryRequest<Model>(token, "/management/models", {
        method: "POST",
        body: JSON.stringify({
          ...capabilities,
          key: fields.get("key"),
          title: fields.get("title"),
          version: fields.get("version"),
          description: fields.get("description"),
        }),
      });
      await onCreated(result);
    } catch (failure) {
      setError(
        failure instanceof SyntaxError
          ? "能力定义不是有效的 JSON。"
          : (failure as Error).message,
      );
    } finally {
      setBusy(false);
    }
  }
  return (
    <form onSubmit={(event) => void submit(event)} className="registry-form">
      <fieldset disabled={busy}>
        <div className="form-grid">
          <label>
            型号标识
            <input
              name="key"
              required
              pattern="[a-z][a-z0-9_-]{0,63}"
              maxLength={64}
              placeholder="virtual_light"
            />
          </label>
          <label>
            型号名称
            <input
              name="title"
              required
              maxLength={160}
              placeholder="可调光灯具"
            />
          </label>
          <label>
            版本
            <input
              name="version"
              required
              defaultValue="1.0.0"
              pattern="(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
            />
          </label>
          <label>
            描述
            <input name="description" maxLength={2000} />
          </label>
        </div>
        <div className="schema-label">
          <label htmlFor="capability-json">能力定义（JSON）</label>
          <button
            type="button"
            className="text-button"
            onClick={() =>
              setDefinition(JSON.stringify(virtualLightCapabilities, null, 2))
            }
          >
            使用虚拟灯具模板
          </button>
        </div>
        <textarea
          id="capability-json"
          className="schema-editor"
          rows={12}
          spellCheck={false}
          value={definition}
          onChange={(event) => setDefinition(event.target.value)}
          required
        />
        <p className="detail-meta">
          支持属性、动作、事件及内联 JSON Schema。发布后修改须创建新版本。
        </p>
        <button className="primary-button">
          {busy ? "发布中…" : "确认发布"}
        </button>
      </fieldset>
    </form>
  );
}

function DeviceForm({ token, onCreated, setError, busy, setBusy }: FormProps) {
  const [models, setModels] = useState<Model[]>([]);
  const [next, setNext] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const loadModels = useCallback(
    (cursor?: string) => {
      return registryRequest<Page<Model>>(
        token,
        `/management/models?limit=200${cursor ? `&cursor=${encodeURIComponent(cursor)}` : ""}`,
      )
        .then((data) => {
          setModels((old) => (cursor ? [...old, ...data.items] : data.items));
          setNext(data.next_cursor);
        })
        .catch((failure: Error) => {
          setError((failure as Error).message);
        })
        .finally(() => {
          setLoading(false);
        });
    },
    [token, setError],
  );
  useEffect(() => {
    void loadModels();
  }, [loadModels]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError("");
    const fields = new FormData(event.currentTarget);
    try {
      const result = await registryRequest<Device>(
        token,
        "/management/devices",
        {
          method: "POST",
          body: JSON.stringify({
            title: fields.get("title"),
            external_ref: fields.get("external_ref"),
            model_id: fields.get("model_id"),
            space_ref: fields.get("space_ref") || null,
          }),
        },
      );
      await onCreated(result);
    } catch (failure) {
      setError((failure as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <form className="registry-form" onSubmit={(event) => void submit(event)}>
      <fieldset disabled={busy || loading}>
        <div className="form-grid">
          <label>
            设备名称
            <input name="title" required maxLength={160} placeholder="客厅灯" />
          </label>
          <label>
            设备标识
            <input
              name="external_ref"
              required
              maxLength={200}
              placeholder="virtual:living-room-light"
            />
          </label>
          <label>
            型号版本
            <select name="model_id" required defaultValue="">
              <option value="" disabled>
                {loading ? "正在加载型号…" : "选择已发布型号"}
              </option>
              {models.map((model) => (
                <option key={model.id} value={model.id}>
                  {model.title} / {model.version} ({model.key})
                </option>
              ))}
            </select>
          </label>
          <label>
            空间引用（可选）
            <input
              name="space_ref"
              maxLength={200}
              placeholder="room:living-room"
            />
          </label>
        </div>
        {next && (
          <button
            type="button"
            className="text-button"
            onClick={() => {
              setLoading(true);
              void loadModels(next);
            }}
          >
            加载更多型号
          </button>
        )}
        {!loading && models.length === 0 && (
          <p>请先到“设备型号”发布一个版本。</p>
        )}
        <p className="detail-meta">
          新设备登记为待接入，连接状态未知。注册不会触发任何物理动作。
        </p>
        <button className="primary-button" disabled={models.length === 0}>
          {busy ? "注册中…" : "确认注册"}
        </button>
      </fieldset>
    </form>
  );
}

function EditDevice({
  device,
  token,
  onSaved,
  onReload,
  setError,
  busy,
  setBusy,
}: {
  device: Thing;
  token: string;
  onSaved: FormProps["onCreated"];
  onReload: () => void;
  setError: FormProps["setError"];
  busy: boolean;
  setBusy: FormProps["setBusy"];
}) {
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    setBusy(true);
    const fields = new FormData(event.currentTarget);
    try {
      const result = await registryRequest<Device>(
        token,
        `/management/devices/${device.id}`,
        {
          method: "PATCH",
          headers: { "If-Match": `"${device.revision}"` },
          body: JSON.stringify({
            title: fields.get("title"),
            space_ref: fields.get("space_ref") || null,
          }),
        },
      );
      await onSaved(result);
    } catch (failure) {
      setError((failure as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <form
      className="registry-form edit-device"
      onSubmit={(event) => void submit(event)}
    >
      <fieldset disabled={busy}>
        <div className="form-grid">
          <label>
            编辑名称
            <input
              name="title"
              required
              maxLength={160}
              defaultValue={device.title}
            />
          </label>
          <label>
            编辑空间
            <input
              name="space_ref"
              maxLength={200}
              defaultValue={device.space_ref ?? ""}
            />
          </label>
        </div>
        <div className="registry-actions">
          <button className="refresh">保存修改</button>
          <button className="text-button" type="button" onClick={onReload}>
            重新加载详情
          </button>
          <small>修订 {device.revision}</small>
        </div>
      </fieldset>
    </form>
  );
}

function operationLabel(operation: string) {
  return (
    (
      {
        "model.published": "发布型号",
        "device.registered": "注册设备",
        "device.updated": "更新设备",
      } as Record<string, string>
    )[operation] ?? operation
  );
}
