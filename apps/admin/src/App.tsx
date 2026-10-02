import { useCallback, useEffect, useRef, useState } from "react";
import {
  Activity,
  ArrowDown,
  ArrowUpRight,
  Box,
  Braces,
  Check,
  Circle,
  Database,
  Layers3,
  Radio,
  RefreshCw,
  Server,
  ShieldCheck,
} from "lucide-react";
import { probe, type Probe } from "./api";
import RegistryConsole, { type RegistryView } from "./RegistryConsole";
import ServicesConsole from "./ServicesConsole";

type Snapshot = { api: Probe; database: Probe; checkedAt: string };

export default function App() {
  const [route, setRoute] = useState(
    window.location.hash.slice(1) || "overview",
  );
  useEffect(() => {
    const update = () => setRoute(window.location.hash.slice(1) || "overview");
    window.addEventListener("hashchange", update);
    return () => window.removeEventListener("hashchange", update);
  }, []);
  const registryRoute = ["models", "devices", "audit"].includes(route);
  const [snapshot, setSnapshot] = useState<Snapshot>();
  const [loading, setLoading] = useState(false);
  const inFlight = useRef(false);
  const refresh = useCallback(async () => {
    if (inFlight.current) return;
    inFlight.current = true;
    setLoading(true);
    try {
      const [api, database] = await Promise.all([
        probe("/health/live", "ok"),
        probe("/health/ready", "ready"),
      ]);
      setSnapshot({
        api,
        database,
        checkedAt: new Date().toLocaleTimeString("zh-CN"),
      });
    } finally {
      inFlight.current = false;
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  return (
    <div className="shell">
      <aside className="sidebar" aria-label="主导航">
        <a className="brand" href="#overview">
          <span className="brand-icon">
            <Layers3 size={22} />
          </span>
          AgenticIoT
        </a>
        <span className="product-label">DEVICE OPERATIONS</span>
        <nav>
          <a
            className={route === "overview" ? "nav-active" : ""}
            href="#overview"
          >
            <Activity size={18} />
            运行概览
          </a>
          <a className={route === "models" ? "nav-active" : ""} href="#models">
            <Box size={18} />
            设备型号
          </a>
          <a
            className={route === "devices" ? "nav-active" : ""}
            href="#devices"
          >
            <Braces size={18} />
            设备注册
          </a>
          <a className={route === "audit" ? "nav-active" : ""} href="#audit">
            <ShieldCheck size={18} />
            注册审计
          </a>
          <a
            className={route === "services" ? "nav-active" : ""}
            href="#services"
          >
            <Server size={18} />
            节点与本地服务
          </a>
        </nav>
        <div className="sidebar-footer">
          <span className="phase-dot" />
          本地开发环境
          <br />
          <small>Device Registry · v0.1</small>
        </div>
      </aside>

      <div className="workspace">
        <header className="topbar">
          <span>
            控制平面 <span className="slash">/</span>{" "}
            {route === "services"
              ? "节点与本地服务"
              : registryRoute
                ? "设备管理"
                : "运行概览"}
          </span>
          <span className="environment">LOCAL</span>
        </header>
        <main id="overview">
          {route === "services" ? (
            <ServicesConsole />
          ) : registryRoute ? (
            <RegistryConsole view={route as RegistryView} />
          ) : (
            <>
              <div className="page-heading">
                <div>
                  <p className="eyebrow">PLATFORM FOUNDATION</p>
                  <h1>运行概览</h1>
                  <p className="subtitle">
                    连接物理世界的第一步，从可靠的运行基础开始。
                  </p>
                </div>
                <button
                  className="refresh"
                  onClick={() => void refresh()}
                  disabled={loading}
                >
                  <RefreshCw size={16} className={loading ? "spinning" : ""} />
                  {loading ? "检查中…" : "刷新状态"}
                </button>
              </div>

              <div
                className="status-grid"
                aria-live="polite"
                aria-busy={loading}
              >
                <StatusCard
                  icon={<Server size={19} />}
                  title="平台 API"
                  data={snapshot?.api}
                />
                <StatusCard
                  icon={<Database size={19} />}
                  title="PostgreSQL"
                  data={snapshot?.database}
                />
                <article className="status-card">
                  <div className="card-label">
                    <Radio size={19} />
                    <h2>真实硬件接入</h2>
                  </div>
                  <p className="status-value muted">待接入</p>
                  <p className="status-detail">
                    虚拟 Edge 已支持；真实硬件待接入
                  </p>
                </article>
              </div>
              <p className="checked-time">
                {snapshot
                  ? `最近检查 ${snapshot.checkedAt} · 状态来自实时健康接口`
                  : "正在读取平台状态…"}
              </p>

              <section
                className="first-device"
                aria-labelledby="first-device-title"
              >
                <div className="device-illustration">
                  <Box size={40} strokeWidth={1.25} />
                </div>
                <div>
                  <span className="section-kicker">设备注册已开放</span>
                  <h2 id="first-device-title">
                    <a href="#models">定义第一个虚拟灯具 →</a>
                  </h2>
                  <p>
                    发布型号、注册设备、绑定虚拟 Edge，再验证动作与观测回执。
                  </p>
                  <div className="steps">
                    <span>注册设备</span>
                    <span>→</span>
                    <span>调用能力</span>
                    <span>→</span>
                    <span>确认状态</span>
                    <span>→</span>
                    <span>审计记录</span>
                  </div>
                </div>
                <span className="outline-badge">Milestone 1–2</span>
              </section>

              <div className="content-grid">
                <section className="panel" id="architecture">
                  <div className="section-heading">
                    <h2>清晰的执行边界</h2>
                    <span>ARCHITECTURE</span>
                  </div>
                  <div className="architecture-node">
                    <Braces size={18} />
                    <div>
                      <strong>AI Agent / 应用</strong>
                      <small>发现能力，提出结构化请求</small>
                    </div>
                    <span className="node-tag">外部</span>
                  </div>
                  <ArrowDown className="flow-arrow" size={17} />
                  <div className="architecture-node highlighted">
                    <ShieldCheck size={18} />
                    <div>
                      <strong>AgenticIoT 平台</strong>
                      <small>授权校验、命令执行与结果追踪</small>
                    </div>
                    <span className="node-tag">核心</span>
                  </div>
                  <ArrowDown className="flow-arrow" size={17} />
                  <div className="architecture-node">
                    <Radio size={18} />
                    <div>
                      <strong>Edge / 物理设备</strong>
                      <small>协议接入，本地执行并回报真实状态</small>
                    </div>
                    <span className="node-tag">现场</span>
                  </div>
                </section>

                <section className="panel" id="interfaces">
                  <div className="section-heading">
                    <h2>接口与契约</h2>
                    <span>DEVELOPER ACCESS</span>
                  </div>
                  <a
                    className="resource-link"
                    href="/api/openapi.json"
                    target="_blank"
                    rel="noreferrer"
                  >
                    <span className="resource-icon">
                      <Braces size={20} />
                    </span>
                    <div>
                      <strong>已实现的 OpenAPI</strong>
                      <p>查看当前服务端接口定义</p>
                    </div>
                    <ArrowUpRight size={18} />
                  </a>
                  <div className="endpoint">
                    <code>GET /health/live</code>
                    <span>进程存活</span>
                  </div>
                  <div className="endpoint">
                    <code>GET /health/ready</code>
                    <span>数据库与迁移</span>
                  </div>
                  <p className="contract-note">
                    注册、状态与命令接口已开放。当前只支持虚拟灯具模拟执行，事件流与真实硬件待接入。
                  </p>
                </section>
              </div>
              <footer className="page-footer">
                <span>AgenticIoT · Physical Device Fabric</span>
                <span>Capability first. Verifiable execution.</span>
              </footer>
            </>
          )}
        </main>
      </div>
    </div>
  );
}

function StatusCard({
  icon,
  title,
  data,
}: {
  icon: React.ReactNode;
  title: string;
  data?: Probe;
}) {
  return (
    <article className="status-card">
      <div className="card-label">
        {icon}
        <h2>{title}</h2>
      </div>
      <p
        className={`status-value ${!data ? "muted" : data.status === "ok" ? "healthy" : "unavailable"}`}
      >
        {data?.status === "ok" ? <Check size={20} /> : <Circle size={13} />}
        {!data ? "检查中" : data.status === "ok" ? "运行正常" : "未就绪"}
      </p>
      <p className="status-detail">{data?.message ?? "正在连接服务…"}</p>
      {data?.traceId && <small className="trace">Trace {data.traceId}</small>}
    </article>
  );
}
