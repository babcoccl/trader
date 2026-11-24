import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import {
  Area,
  AreaChart,
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { ApiError, api } from "../api/client";
import type { BacktestParams } from "../api/client";
import { pct, usd } from "../lib/time";

function usdCents(value: number): string {
  return value.toLocaleString("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

function errorMessage(err: unknown): string {
  if (err instanceof ApiError) {
    try {
      const detail = JSON.parse(err.message)?.detail;
      if (typeof detail === "string") return detail;
      if (Array.isArray(detail)) {
        return detail.map((d) => d.msg ?? JSON.stringify(d)).join("; ");
      }
    } catch {
      // not a JSON body; fall through
    }
  }
  return err instanceof Error ? err.message : "Backtest failed";
}

const DEFAULT_PARAMS_JSON = `{
  "strategy_sleeve": "trend_momentum",
  "asset_universe": ["BTC-USD", "ETH-USD"],
  "start_date": "2024-01-01",
  "end_date": "2024-12-31",
  "starting_capital": 10000,
  "assumed_fee_tier": "tier_1_maker",
  "vol_target": 0.40,
  "drawdown_limit": 0.35
}`;

function StatCard({ title, value, sub }: { title: string; value: string; sub?: string }) {
  return (
    <div className="card stat-card">
      <div className="stat-card__title">{title}</div>
      <div className="stat-card__value">{value}</div>
      {sub ? <div className="stat-card__sub">{sub}</div> : null}
    </div>
  );
}

export function Backtest() {
  const [params, setParams] = useState(DEFAULT_PARAMS_JSON);
  const [parseError, setParseError] = useState<string | null>(null);
  const mutation = useMutation({ mutationFn: api.runBacktest });

  const runBacktest = () => {
    try {
      const parsed = JSON.parse(params) as BacktestParams;
      setParseError(null);
      mutation.mutate(parsed);
    } catch (e) {
      setParseError(e instanceof Error ? e.message : "Invalid JSON");
    }
  };

  const result = mutation.data;
  const s = result?.summary_metrics;
  const chartData = (result?.timeseries_data ?? []).map((p) => ({
    timestamp: p.timestamp,
    equity: Number(p.equity),
    benchmark_equity: Number(p.benchmark_equity),
    asset_price: Number(p.asset_price),
    drawdown_pct: p.drawdown * 100,
  }));

  const errorText = parseError ?? (mutation.isError ? errorMessage(mutation.error) : null);

  return (
    <div className="page">
      <div className="backtest-layout">
        <div className="card backtest-layout__params">
          <div className="card__title">Parameters</div>
          <textarea
            className="backtest-params"
            aria-label="Backtest parameters"
            rows={18}
            value={params}
            onChange={(e) => setParams(e.target.value)}
          />
          <button onClick={runBacktest} disabled={mutation.isPending}>
            {mutation.isPending ? "Running…" : "Run Backtest"}
          </button>
          {errorText ? <p className="error">{errorText}</p> : null}
        </div>

        <div className="backtest-layout__results">
          {!result ? (
            <p className="muted">
              Configure parameters and click Run Backtest to simulate.
            </p>
          ) : (
            <>
              <div className="stat-grid">
                <StatCard
                  title="Net CAGR"
                  value={pct(s!.cagr)}
                  sub={`benchmark ${pct(s!.benchmark_cagr)}`}
                />
                <StatCard title="Max Drawdown" value={pct(s!.max_drawdown)} />
                <StatCard
                  title="Total Trades"
                  value={String(s!.total_trades)}
                  sub={`win rate ${pct(s!.win_rate)}`}
                />
                <StatCard
                  title="Total Fees Paid"
                  value={usdCents(Number(s!.total_fees_paid))}
                />
              </div>

              <div className="card">
                <div className="card__title">Equity vs benchmark</div>
                <ResponsiveContainer width="100%" height={280}>
                  <LineChart data={chartData}>
                    <CartesianGrid strokeDasharray="3 3" />
                    <XAxis dataKey="timestamp" fontSize={11} />
                    <YAxis fontSize={11} domain={["auto", "auto"]} />
                    <Tooltip />
                    <Line
                      type="monotone"
                      dataKey="equity"
                      name="Strategy equity"
                      stroke="#191919"
                      dot={false}
                    />
                    <Line
                      type="monotone"
                      dataKey="benchmark_equity"
                      name="Benchmark equity"
                      stroke="#2563eb"
                      strokeDasharray="5 5"
                      dot={false}
                    />
                  </LineChart>
                </ResponsiveContainer>
              </div>

              <div className="card">
                <div className="card__title">Drawdown</div>
                <ResponsiveContainer width="100%" height={180}>
                  <AreaChart data={chartData}>
                    <CartesianGrid strokeDasharray="3 3" />
                    <XAxis dataKey="timestamp" fontSize={11} />
                    <YAxis fontSize={11} unit="%" />
                    <Tooltip />
                    <Area
                      type="monotone"
                      dataKey="drawdown_pct"
                      name="Drawdown"
                      stroke="#f53b3a"
                      fill="rgb(245 59 58 / 0.15)"
                    />
                  </AreaChart>
                </ResponsiveContainer>
              </div>

              <div className="card">
                <div className="card__title">Trade log</div>
                <div className="table-scroll">
                  <table className="data-table">
                    <thead>
                      <tr>
                        <th>Date</th>
                        <th>Asset</th>
                        <th>Side</th>
                        <th>Size</th>
                        <th>Price</th>
                        <th>PnL</th>
                        <th>Fee</th>
                      </tr>
                    </thead>
                    <tbody>
                      {result.trade_log.map((t, i) => (
                        <tr key={i}>
                          <td>{t.date}</td>
                          <td>{t.asset}</td>
                          <td>{t.side}</td>
                          <td>{Number(t.size)}</td>
                          <td>{usd(Number(t.price))}</td>
                          <td>{usdCents(Number(t.pnl))}</td>
                          <td>{usdCents(Number(t.fee))}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  {result.trade_log.length === 0 ? (
                    <p className="muted">No trades in this window.</p>
                  ) : null}
                </div>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
