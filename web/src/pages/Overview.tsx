import { useQuery } from "@tanstack/react-query";
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { api } from "../api/client";
import { pct, timeAgo, usd } from "../lib/time";

function StatCard({ title, value, sub }: { title: string; value: string; sub?: string }) {
  return (
    <div className="card stat-card">
      <div className="stat-card__title">{title}</div>
      <div className="stat-card__value">{value}</div>
      {sub ? <div className="stat-card__sub">{sub}</div> : null}
    </div>
  );
}

export function Overview() {
  const status = useQuery({ queryKey: ["status"], queryFn: api.status, refetchInterval: 30_000 });
  const equity = useQuery({ queryKey: ["equity"], queryFn: () => api.equityReport() });
  const daily = useQuery({
    queryKey: ["decisions", "daily"],
    queryFn: () => api.decisions({ cycle: "daily", limit: 1 }),
  });
  const intraday = useQuery({
    queryKey: ["decisions", "intraday"],
    queryFn: () => api.decisions({ cycle: "intraday", limit: 1 }),
  });
  const recent = useQuery({
    queryKey: ["decisions", "recent"],
    queryFn: () => api.decisions({ limit: 10 }),
  });

  if (status.isLoading) return <p>Loading…</p>;
  if (status.isError || !status.data) return <p>Could not load status.</p>;
  const s = status.data;

  const latestDaily = daily.data?.[0];
  const latestIntraday = intraday.data?.[0];
  const last10 = recent.data ?? [];

  const chartData = (equity.data ?? []).map((p) => ({
    time: p.time.slice(0, 10),
    account: Number(p.account),
    "Buy and hold BTC": p.btc === null ? null : Number(p.btc),
    "Buy and hold BTC+ETH": p.btc_eth === null ? null : Number(p.btc_eth),
  }));

  return (
    <div className="page">
      <div className="stat-grid">
        <StatCard title="Account value" value={usd(s.equity_usd)} sub={`mode: ${s.mode}`} />
        <StatCard title="Invested now" value={pct(s.invested_fraction)} />
        <StatCard
          title="Drop from peak"
          value={pct(s.drawdown)}
          sub={`halts at ${pct(s.halt_at)}`}
        />
        <StatCard
          title="Needs your decision"
          value={String(s.pending_plans)}
          sub={s.pending_plans === 1 ? "plan waiting" : "plans waiting"}
        />
      </div>

      <div className="card">
        <div className="card__title">Account vs buy and hold</div>
        {equity.isError ? (
          <p className="muted">Could not load equity history.</p>
        ) : chartData.length === 0 ? (
          <p className="muted">No equity history yet.</p>
        ) : (
          <ResponsiveContainer width="100%" height={280}>
            <LineChart data={chartData} margin={{ top: 8, right: 16, bottom: 0, left: 8 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#eee" />
              <XAxis dataKey="time" tick={{ fontSize: 12 }} />
              <YAxis
                tick={{ fontSize: 12 }}
                tickFormatter={(v: number) => `$${Math.round(v).toLocaleString()}`}
                width={80}
                domain={["auto", "auto"]}
              />
              <Tooltip formatter={(v) => usd(v as number)} />
              <Legend />
              <Line
                type="monotone"
                dataKey="account"
                name="Account"
                stroke="#2563eb"
                dot={false}
                strokeWidth={2}
              />
              <Line
                type="monotone"
                dataKey="Buy and hold BTC"
                stroke="#f59e0b"
                dot={false}
                strokeWidth={1.5}
              />
              <Line
                type="monotone"
                dataKey="Buy and hold BTC+ETH"
                stroke="#10b981"
                dot={false}
                strokeWidth={1.5}
              />
            </LineChart>
          </ResponsiveContainer>
        )}
      </div>

      <div className="card">
        <div className="card__title">What the system thinks right now</div>
        {daily.isError || intraday.isError ? (
          <p className="muted">Could not load the latest decisions.</p>
        ) : (
          <>
            {latestDaily ? (
              <p>
                <span className="muted">Daily, {timeAgo(latestDaily.ran_at, new Date())}:</span>{" "}
                {latestDaily.reason}
              </p>
            ) : (
              <p className="muted">No daily decision yet.</p>
            )}
            {latestIntraday ? (
              <p>
                <span className="muted">
                  Intraday, {timeAgo(latestIntraday.ran_at, new Date())}:
                </span>{" "}
                {latestIntraday.reason}
              </p>
            ) : (
              <p className="muted">No intraday check yet.</p>
            )}
          </>
        )}
      </div>

      <div className="card">
        <div className="card__title">Why nothing has happened lately</div>
        {recent.isError ? (
          <p className="muted">Could not load decisions.</p>
        ) : last10.length === 0 ? (
          <p className="muted">No decisions recorded yet.</p>
        ) : (
          <ul className="reason-list">
            {last10.map((d) => (
              <li key={d.id}>
                <span className="muted">
                  {timeAgo(d.ran_at, new Date())} · {d.cycle}
                </span>{" "}
                {d.reason}
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
