import { useEffect, useState } from "react";

type Overview = {
  total_requests: number;
  completed_requests: number;
  avg_score: number;
  recommend_share: number;
  avg_latency_ms: number;
  total_cost_usd: number;
  active_providers: number;
};

type Provider = {
  provider: string;
  healthy: boolean;
  mode: string;
  detail: string;
};

type AgentMetric = {
  agent: string;
  completed: number;
  error_count: number;
  avg_latency_ms: number;
  success_rate: number;
};

type Trace = {
  request_id: string;
  city: string;
  business_type: string;
  verdict: string | null;
  duration_ms: number | null;
  confidence: number | null;
  reasoning: string | null;
};

const API_URL = (import.meta.env.VITE_API_URL ?? "http://localhost:8000").replace(/\/$/, "");
const OPS_TOKEN = import.meta.env.VITE_OPS_TOKEN ?? "geoverdict-ops-secret";
const NAV = ["Overview", "Агенты", "Провайдеры", "Трейсы"] as const;

async function fetchOps<T>(path: string) {
  const response = await fetch(`${API_URL}${path}`, {
    headers: { "X-Ops-Token": OPS_TOKEN }
  });
  if (!response.ok) throw new Error(`Failed to fetch ${path}`);
  return response.json() as Promise<T>;
}

export default function App() {
  const [tab, setTab] = useState<(typeof NAV)[number]>("Overview");
  const [overview, setOverview] = useState<Overview | null>(null);
  const [providers, setProviders] = useState<Provider[]>([]);
  const [agents, setAgents] = useState<AgentMetric[]>([]);
  const [traces, setTraces] = useState<Trace[]>([]);

  useEffect(() => {
    Promise.all([
      fetchOps<Overview>("/api/v1/ops/overview"),
      fetchOps<Provider[]>("/api/v1/ops/providers/status"),
      fetchOps<AgentMetric[]>("/api/v1/ops/agents/metrics"),
      fetchOps<Trace[]>("/api/v1/ops/traces")
    ]).then(([overviewData, providerData, agentData, traceData]) => {
      setOverview(overviewData);
      setProviders(providerData);
      setAgents(agentData);
      setTraces(traceData);
    });
  }, []);

  return (
    <div className="ops-layout">
      <aside className="ops-sidebar">
        <div className="ops-logo">
          <span className="logo-dot" />
          <div>
            <strong>GeoVerdict</strong>
            <p>LLMOps dashboard</p>
          </div>
        </div>
        {NAV.map((item) => (
          <button
            className={item === tab ? "nav-item active" : "nav-item"}
            key={item}
            onClick={() => setTab(item)}
            type="button"
          >
            {item}
          </button>
        ))}
      </aside>

      <main className="ops-main">
        <header className="ops-topbar">
          <div>
            <p className="ops-eyebrow">GeoVerdict runtime</p>
            <h1>{tab}</h1>
          </div>
          <div className="status-pill">providers: {overview?.active_providers ?? 0}</div>
        </header>

        {tab === "Overview" && overview ? (
          <>
            <section className="metric-grid">
              <article className="metric-card">
                <span>Запросов</span>
                <strong>{overview.total_requests}</strong>
              </article>
              <article className="metric-card">
                <span>Completed</span>
                <strong>{overview.completed_requests}</strong>
              </article>
              <article className="metric-card">
                <span>Avg score</span>
                <strong>{overview.avg_score}</strong>
              </article>
              <article className="metric-card">
                <span>Recommend share</span>
                <strong>{Math.round(overview.recommend_share * 100)}%</strong>
              </article>
              <article className="metric-card">
                <span>Latency</span>
                <strong>{Math.round(overview.avg_latency_ms)} ms</strong>
              </article>
              <article className="metric-card">
                <span>Total cost</span>
                <strong>${overview.total_cost_usd.toFixed(3)}</strong>
              </article>
            </section>

            <section className="ops-card">
              <h2>Статус провайдеров</h2>
              <div className="provider-grid">
                {providers.map((provider) => (
                  <article className="provider-card" key={provider.provider}>
                    <div className="provider-title">
                      <strong>{provider.provider}</strong>
                      <span className={provider.healthy ? "health ok" : "health bad"}>
                        {provider.healthy ? "healthy" : "down"}
                      </span>
                    </div>
                    <p>{provider.detail}</p>
                  </article>
                ))}
              </div>
            </section>
          </>
        ) : null}

        {tab === "Агенты" ? (
          <section className="ops-card">
            <h2>Метрики агентов</h2>
            <table className="ops-table">
              <thead>
                <tr>
                  <th>Агент</th>
                  <th>Success rate</th>
                  <th>Latency</th>
                  <th>Completed</th>
                  <th>Errors</th>
                </tr>
              </thead>
              <tbody>
                {agents.map((item) => (
                  <tr key={item.agent}>
                    <td>{item.agent}</td>
                    <td>{Math.round(item.success_rate * 100)}%</td>
                    <td>{Math.round(item.avg_latency_ms)} ms</td>
                    <td>{item.completed}</td>
                    <td>{item.error_count}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>
        ) : null}

        {tab === "Провайдеры" ? (
          <section className="ops-card">
            <h2>Provider routing</h2>
            <div className="provider-grid">
              {providers.map((provider) => (
                <article className="provider-card" key={provider.provider}>
                  <div className="provider-title">
                    <strong>{provider.provider}</strong>
                    <span className={provider.healthy ? "health ok" : "health bad"}>
                      {provider.mode}
                    </span>
                  </div>
                  <p>{provider.detail}</p>
                </article>
              ))}
            </div>
          </section>
        ) : null}

        {tab === "Трейсы" ? (
          <section className="ops-card">
            <h2>Последние reasoning traces</h2>
            <div className="trace-list">
              {traces.map((trace) => (
                <article className="trace-card" key={trace.request_id}>
                  <div className="trace-head">
                    <strong>{trace.request_id}</strong>
                    <span>{trace.city}</span>
                    <span>{trace.verdict ?? "pending"}</span>
                  </div>
                  <p>{trace.reasoning ?? "Reasoning not available yet."}</p>
                </article>
              ))}
            </div>
          </section>
        ) : null}
      </main>
    </div>
  );
}
