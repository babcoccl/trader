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
import { fmtQty } from "../lib/format";
import { pct, timeAgo, usd } from "../lib/time";

function usdCents(value: string | number): string {
  return Number(value).toLocaleString("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

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
  const subsidy = useQuery({ queryKey: ["subsidy"], queryFn: api.subsidy, refetchInterval: 15_000 });

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

      <div className="card subsidy-card">
        <div className="card__title subsidy-card__title">
          Fee-Tier Subsidy
          {subsidy.data ? (
            <span className={`badge subsidy-card__badge subsidy-card__badge--${subsidy.data.status}`}>
              {subsidy.data.status[0].toUpperCase() + subsidy.data.status.slice(1)}
            </span>
          ) : null}
        </div>
        {subsidy.isError ? (
          <p className="muted">Could not load subsidy status.</p>
        ) : !subsidy.data ? (
          <p className="muted">Loading subsidy status…</p>
        ) : (
          <>
            <p className="muted subsidy-card__reason">{subsidy.data.reason}</p>
            {subsidy.data.gates.length > 0 ? (
              <div className="subsidy-gates">
                {subsidy.data.gates.map((g) => (
                  <span
                    key={g.name}
                    className={`subsidy-gate ${
                      g.passed ? "subsidy-gate--pass" : "subsidy-gate--fail"
                    }`}
                  >
                    {g.passed ? "✓" : "✕"} {g.name}: {g.detail}
                  </span>
                ))}
              </div>
            ) : null}
            <div className="subsidy-card__tier">
              <span>
                {subsidy.data.tier} ({subsidy.data.maker_bps} / {subsidy.data.taker_bps} bps)
              </span>
              <span>
                BTC ADX {subsidy.data.adx === null ? "—" : subsidy.data.adx.toFixed(1)}
              </span>
            </div>
            <div
              className="progress"
              role="progressbar"
              aria-label="Fee-tier progress"
              aria-valuemin={0}
              aria-valuemax={100}
              aria-valuenow={subsidy.data.progress_pct * 100}
            >
              <div
                className="progress__fill"
                style={{ width: `${subsidy.data.progress_pct * 100}%` }}
              />
            </div>
            <div className="subsidy-card__progress-labels">
              <span>
                {usd(subsidy.data.trailing_30d_volume_usd)} of{" "}
                {usd(subsidy.data.target_volume_usd)} ·{" "}
                {(subsidy.data.progress_pct * 100).toFixed(1)}%
              </span>
              <span>{usd(subsidy.data.remaining_to_target_usd)} to next tier</span>
            </div>
            {Number(subsidy.data.expiring_5d_volume_usd) > 0 ? (
              <p className="muted subsidy-expiry-note">
                Expiring in 5d: {usd(subsidy.data.expiring_5d_volume_usd)} (projected
                30d: {usd(subsidy.data.projected_30d_volume_usd)})
              </p>
            ) : null}
            <div className="subsidy-stats">
              <div>
                <div className="stat-card__title">Booster Trades</div>
                <div className="subsidy-stats__value">{subsidy.data.booster_trades_30d}</div>
              </div>
              <div>
                <div className="stat-card__title">Booster Volume</div>
                <div className="subsidy-stats__value">
                  {usd(subsidy.data.booster_volume_30d_usd)}
                </div>
              </div>
              <div>
                <div className="stat-card__title">Booster Fees Spent</div>
                <div className="subsidy-stats__value">
                  {usdCents(subsidy.data.booster_fees_30d_usd)}
                </div>
              </div>
              <div>
                <div className="stat-card__title">Open Orders</div>
                <div className="subsidy-stats__value">
                  {usd(subsidy.data.open_orders_usd)}
                </div>
              </div>
              <div>
                <div className="stat-card__title">30d Net PnL</div>
                <div
                  className={`subsidy-stats__value ${
                    Number(subsidy.data.booster_net_pnl_30d_usd) >= 0
                      ? "gain"
                      : "loss"
                  }`}
                >
                  {usdCents(subsidy.data.booster_net_pnl_30d_usd)}
                </div>
              </div>
            </div>
            <div className="subsidy-fills">
              <div className="stat-card__title">Recent Ping-Pong Fills</div>
              {subsidy.data.recent_fills.length === 0 ? (
                <p className="muted">No ping-pong fills yet.</p>
              ) : (
                <div className="table-scroll">
                  <table className="data-table">
                    <thead>
                      <tr>
                        <th>Time</th>
                        <th>Side</th>
                        <th>Price</th>
                        <th>Qty</th>
                        <th>Cost</th>
                        <th>Fee</th>
                        <th>PnL</th>
                      </tr>
                    </thead>
                    <tbody>
                      {subsidy.data.recent_fills.map((f, i) => (
                        <tr key={`${f.time}-${i}`}>
                          <td>{f.time.slice(0, 16).replace("T", " ")}Z</td>
                          <td>{f.side}</td>
                          <td>{usdCents(f.price)}</td>
                          <td>{fmtQty(f.quantity)}</td>
                          <td>{usdCents(f.notional_usd)}</td>
                          <td>{usdCents(f.fee_usd)}</td>
                          <td className={Number(f.pnl_usd) >= 0 ? "gain" : "loss"}>
                            {usdCents(f.pnl_usd)}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          </>
        )}
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
