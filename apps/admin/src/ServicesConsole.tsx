import { useState, type FormEvent } from "react";
import { registryRequest } from "./registry-api";

type Domain = { id: string; title: string; permissions: string[] };
type Node = { id: string; title: string; enabled: boolean; online: boolean };
type Service = {
  id: string;
  name: string;
  node_id: string;
  model: string;
  enabled: boolean;
  locality: string;
  node_connected: boolean;
  service_health: string;
};

export default function ServicesConsole() {
  const [token, setToken] = useState("");
  const [credential, setCredential] = useState("");
  const [domain, setDomain] = useState<Domain>();
  const [nodes, setNodes] = useState<Node[]>([]);
  const [services, setServices] = useState<Service[]>([]);
  const [secret, setSecret] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const manage = domain?.permissions.includes("domain:manage");

  async function load(auth: string) {
    const info = await registryRequest<Domain>(auth, "/management/domain");
    const registered = await registryRequest<{ items: Node[] }>(
      auth,
      "/management/nodes",
    );
    const available = info.permissions.includes("service:invoke")
      ? await registryRequest<{ items: Service[] }>(auth, "/services")
      : { items: [] };
    setDomain(info);
    setNodes(registered.items);
    setServices(available.items);
  }

  async function perform(work: () => Promise<void>) {
    setBusy(true);
    setError("");
    try {
      await work();
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : "请求失败");
    } finally {
      setBusy(false);
    }
  }

  function connect(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    void perform(async () => {
      await load(token);
      setCredential(token);
      setToken("");
    });
  }

  function createNode(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    void perform(async () => {
      const result = await registryRequest<{ token: string }>(
        credential,
        "/management/nodes",
        {
          method: "POST",
          body: JSON.stringify({
            title: data.get("title"),
            edge_ref: data.get("edge_ref"),
          }),
        },
      );
      setSecret(result.token);
      form.reset();
      await load(credential);
    });
  }

  function createService(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    void perform(async () => {
      await registryRequest(credential, "/management/service-connections", {
        method: "POST",
        body: JSON.stringify(Object.fromEntries(data)),
      });
      form.reset();
      await load(credential);
    });
  }

  function toggle(path: string, enabled: boolean) {
    void perform(async () => {
      await registryRequest(credential, path, {
        method: "PATCH",
        body: JSON.stringify({ enabled }),
      });
      await load(credential);
    });
  }

  return (
    <section className="service-console">
      <div className="page-heading">
        <div>
          <p className="eyebrow">DOMAIN AND LOCAL SERVICES</p>
          <h1>节点与本地服务</h1>
          <p className="subtitle">
            账号负责管理，资源归属域；已启用不代表在线，也不证明数据未出网。
          </p>
        </div>
      </div>
      {error && (
        <p role="alert" className="form-error">
          {error}
        </p>
      )}
      {!credential ? (
        <form onSubmit={connect} className="service-card">
          <label>
            管理凭证
            <input
              aria-label="管理凭证"
              type="password"
              value={token}
              onChange={(e) => setToken(e.target.value)}
              required
              autoComplete="off"
            />
          </label>
          <p>使用入口签发的令牌或已引导的开发凭证。仅保存在当前页面内存中。</p>
          <button disabled={busy} type="submit">
            连接资源域
          </button>
        </form>
      ) : (
        <>
          <section className="service-card">
            <h2>当前资源域</h2>
            <p>{domain?.title}</p>
            <code>{domain?.id}</code>
            <p>{domain?.permissions.join(" · ")}</p>
            <button
              disabled={busy}
              onClick={() => void perform(() => load(credential))}
            >
              刷新状态
            </button>{" "}
            <button
              disabled={busy}
              onClick={() => {
                setCredential("");
                setDomain(undefined);
                setNodes([]);
                setServices([]);
                setSecret("");
              }}
            >
              断开并清除凭证
            </button>
          </section>
          {secret && (
            <section className="service-card" aria-label="一次性节点凭证">
              <h2>节点凭证只展示一次</h2>
              <p>
                通过可信渠道配置到 Node 的
                AGENTICIOT_NODE_TOKEN。不要分享或提交到代码库。
              </p>
              <textarea aria-label="节点凭证" readOnly value={secret} />
              <button onClick={() => setSecret("")}>已保存，清除显示</button>
            </section>
          )}
          <section className="service-card">
            <h2>节点</h2>
            {nodes.length === 0 && <p>还没有已登记节点。</p>}
            {nodes.map((node) => (
              <article key={node.id} className="service-row">
                <h3>{node.title}</h3>
                <code>{node.id}</code>
                <p>
                  {node.enabled ? "已启用" : "已停用"} ·{" "}
                  {node.online ? "已连接" : "未连接"}
                </p>
                {manage && (
                  <>
                    <button
                      disabled={busy}
                      onClick={() =>
                        toggle(
                          `/management/nodes/${node.id}/activation`,
                          !node.enabled,
                        )
                      }
                    >
                      {node.enabled ? "停用节点" : "启用节点"}
                    </button>{" "}
                    <button
                      disabled={busy}
                      onClick={() => {
                        if (
                          !window.confirm(
                            "轮换后旧凭证失效，Node 需重新配置。继续？",
                          )
                        )
                          return;
                        void perform(async () => {
                          const result = await registryRequest<{
                            token: string;
                          }>(
                            credential,
                            `/management/nodes/${node.id}/rotate-credential`,
                            { method: "POST" },
                          );
                          setSecret(result.token);
                          await load(credential);
                        });
                      }}
                    >
                      轮换凭证
                    </button>
                  </>
                )}
              </article>
            ))}
            {manage && (
              <form onSubmit={createNode}>
                <h3>登记节点并签发凭证</h3>
                <label>
                  节点名称
                  <input name="title" required maxLength={160} />
                </label>
                <label>
                  节点标识
                  <input name="edge_ref" required maxLength={200} />
                </label>
                <button disabled={busy || !!secret}>登记节点</button>
              </form>
            )}
          </section>
          <section className="service-card">
            <h2>本地服务</h2>
            {services.length === 0 && (
              <p>没有可见服务，或当前账号没有服务调用权限。</p>
            )}
            {services.map((service) => (
              <article key={service.id} className="service-row">
                <h3>{service.name}</h3>
                <p>
                  模型 {service.model} ·{" "}
                  {service.enabled ? "已批准启用" : "待批准"}
                </p>
                <p>
                  声明为本地 · 健康未知 · Node{" "}
                  {service.node_connected ? "已连接" : "未连接"}
                </p>
                {manage && (
                  <button
                    disabled={busy}
                    onClick={() =>
                      toggle(
                        `/management/service-connections/${service.id}/activation`,
                        !service.enabled,
                      )
                    }
                  >
                    {service.enabled ? "停用服务" : "批准启用"}
                  </button>
                )}
              </article>
            ))}
            {manage && nodes.length > 0 && (
              <form onSubmit={createService}>
                <h3>注册服务</h3>
                <label>
                  所属节点
                  <select name="node_id">
                    {nodes.map((n) => (
                      <option value={n.id} key={n.id}>
                        {n.title}
                      </option>
                    ))}
                  </select>
                </label>
                <label>
                  服务名称
                  <input
                    name="name"
                    required
                    pattern="[a-z][a-z0-9_.-]{0,63}"
                  />
                </label>
                <label>
                  Node 本地配置标识
                  <input
                    name="local_ref"
                    required
                    pattern="[a-z][a-z0-9_-]{0,63}"
                  />
                </label>
                <label>
                  获准模型
                  <input name="model" required maxLength={200} />
                </label>
                <p>
                  平台不接收任意模型 URL。地址和模型白名单由 Node
                  本机配置；注册后需另行批准。
                </p>
                <button disabled={busy}>注册为待批准服务</button>
              </form>
            )}
          </section>
        </>
      )}
    </section>
  );
}
