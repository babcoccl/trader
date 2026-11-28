import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
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
import type { BacktestParams, BacktestSweepResult } from "../api/client";
import { pct } from "../lib/time";

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

// Fallback preset schema when /api/backtests/presets has not loaded yet.
const FALLBACK_CANDIDATES: Record<
  string,
  { label: string; defaults: Record<string, unknown>; default_fee_tier?: string }
> = {
  funding_downtrend: {
    label: "Funding downtrend carry (spot)",
    defaults: { max_rules: 2, funding_window_days: 7, max_weight: 0.25 },
  },
  regime_composite: {
    label: "Regime composite H/H+ (spot)",
    defaults: { rule_set: "50_200", mixed_mode: "dip", downtrend_mode: "funding" },
  },
  intraday_flush_mr: {
    label: "Intraday flush mean-reversion (spot)",
    defaults: { bar_tf: "4h", entry_atr_mult: 2.5, max_hold_hours: 24 },
    default_fee_tier: "adv2_spot",
  },
  trend_long_short_fut: {
    label: "Trend long/short (perp futures)",
    defaults: { short_max_rules: 2, short_vol_target: 0.25 },
    default_fee_tier: "adv2_fut",
  },
};

const BASE_SLEEVES = [
  "trend_momentum",
  "cross_sectional_momentum",
  "ping_pong_subsidy",
];

function useCopy(): [string | null, (key: string, text: string) => void] {
  const [copied, setCopied] = useState<string | null>(null);
  const copy = (key: string, text: string) => {
    void navigator.clipboard?.writeText(text).then(() => {
      setCopied(key);
      setTimeout(() => setCopied(null), 2000);
    });
  };
  return [copied, copy];
}

function GateBadges({ gates }: { gates: Record<string, string> }) {
  return (
    <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
      {Object.entries(gates).map(([k, v]) => (
        <span
          key={k}
          className={`badge ${v === "PASS" ? "badge--green" : v === "FAIL" ? "badge--red" : "badge--amber"}`}
        >
          {k} {v}
        </span>
      ))}
    </div>
  );
}

function SweepTable({
  sweep,
  copy,
  copied,
}: {
  sweep: BacktestSweepResult;
  copy: (key: string, text: string) => void;
  copied: string | null;
}) {
  const num = (v: unknown) => (typeof v === "number" ? v : 0);
  return (
    <div className="card">
      <div className="card__title">Candidate sweep</div>
      <div className="table-scroll">
        <table className="data-table">
          <thead>
            <tr>
              <th>Strategy</th>
              <th>Tier</th>
              <th>OOS CAGR</th>
              <th>Sharpe</th>
              <th>MaxDD</th>
              <th>NonUp PnL</th>
              <th>Gates</th>
            </tr>
          </thead>
          <tbody>
            {sweep.rows.map((r, i) => {
              const splits = (r.splits ?? {}) as Record<
                string,
                Record<string, number>
              >;
              const oos = splits.oos_2022_plus ?? {};
              const regimes = (r.oos_regimes ?? {}) as Record<
                string,
                Record<string, number>
              >;
              const nonUp =
                num(regimes.mixed?.net_pnl_pct) +
                num(regimes.downtrend?.net_pnl_pct);
              const gates = (r.gates ?? {}) as Record<string, string>;
              return (
                <tr key={i}>
                  <td>{String(r.strategy ?? "")}</td>
                  <td>{String(r.fee_tier ?? "")}</td>
                  <td>{pct(oos.cagr ?? 0)}</td>
                  <td>{(oos.sharpe ?? 0).toFixed(2)}</td>
                  <td>{pct(oos.max_dd ?? 0)}</td>
                  <td>{pct(nonUp)}</td>
                  <td>
                    <GateBadges gates={gates} />
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <button
        style={{ marginTop: 8 }}
        onClick={() => copy("sweep", sweep.combined_markdown)}
      >
        {copied === "sweep" ? "Copied!" : "Copy Full Sweep for LLM"}
      </button>
    </div>
  );
}

function CompactScorecard({ summary }: { summary: Record<string, unknown> }) {
  const splits = (summary.splits ?? {}) as Record<string, Record<string, number>>;
  const regimes = (summary.oos_regimes ?? {}) as Record<
    string,
    Record<string, number>
  >;
  const gates = (summary.gates ?? {}) as Record<string, string>;
  const names = Object.keys(splits);
  if (!names.length) return null;
  return (
    <div className="card">
      <div className="card__title">Compact scorecard</div>
      <div className="table-scroll">
        <table className="data-table">
          <thead>
            <tr>
              <th>Split</th>
              <th>CAGR</th>
              <th>Sharpe</th>
              <th>MaxDD</th>
              <th>Tr/mo</th>
              <th>Win%</th>
            </tr>
          </thead>
          <tbody>
            {names.map((n) => (
              <tr key={n}>
                <td>{n}</td>
                <td>{pct(splits[n].cagr ?? 0)}</td>
                <td>{(splits[n].sharpe ?? 0).toFixed(2)}</td>
                <td>{pct(splits[n].max_dd ?? 0)}</td>
                <td>{(splits[n].trades_per_mo ?? 0).toFixed(1)}</td>
                <td>{pct(splits[n].win_rate ?? 0)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {Object.keys(regimes).length ? (
        <div className="table-scroll" style={{ marginTop: 8 }}>
          <table className="data-table">
            <thead>
              <tr>
                <th>OOS regime</th>
                <th>days%</th>
                <th>contrib</th>
                <th>trades</th>
                <th>net pnl</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(regimes).map(([n, r]) => (
                <tr key={n}>
                  <td>{n}</td>
                  <td>{pct(r.days_pct ?? 0)}</td>
                  <td>{pct(r.contrib_cagr ?? 0)}</td>
                  <td>{r.trades ?? 0}</td>
                  <td>{pct(r.net_pnl_pct ?? 0)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
      <div style={{ marginTop: 8 }}>
        <GateBadges gates={gates} />
      </div>
    </div>
  );
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

export function Backtest() {
  const [params, setParams] = useState(DEFAULT_PARAMS_JSON);
  const [parseError, setParseError] = useState<string | null>(null);
  const [sleeve, setSleeve] = useState("trend_momentum");
  const [feeTier, setFeeTier] = useState("");
  const [execMode, setExecMode] = useState<"maker" | "taker" | "mixed">("maker");
  const [combine, setCombine] = useState(false);
  const [overrides, setOverrides] = useState<Record<string, string>>({});
  const [copied, copy] = useCopy();
  const mutation = useMutation({ mutationFn: api.runBacktest });
  const sweep = useMutation({ mutationFn: api.runBacktestSweep });
  const presets = useQuery({
    queryKey: ["backtest-presets"],
    queryFn: api.backtestPresets,
    retry: false,
  });

  const candidates = presets.data?.candidate_presets ?? FALLBACK_CANDIDATES;
  const feeTiers = presets.data?.fee_tiers;
  const availability = presets.data?.data_availability;
  const isCandidate = sleeve in candidates;
  const candidate = isCandidate ? candidates[sleeve] : null;

  const runBacktest = () => {
    try {
      const parsed = JSON.parse(params) as BacktestParams;
      if (isCandidate) {
        parsed.strategy_sleeve = sleeve;
        parsed.execution_mode = execMode;
        parsed.combine_trend = combine;
        if (feeTier) parsed.fee_tier = feeTier;
        const pv: Record<string, unknown> = {};
        for (const [k, v] of Object.entries(overrides)) {
          if (v === "") continue;
          const n = Number(v);
          pv[k] = Number.isNaN(n) ? v : n;
        }
        if (Object.keys(pv).length) parsed.params = pv;
      } else if (!parsed.strategy_sleeve) {
        parsed.strategy_sleeve = sleeve;
      }
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
          {availability ? (
            <div style={{ display: "flex", gap: 6, marginBottom: 8 }}>
              {(["daily", "funding", "intraday"] as const).map((name) => {
                const a = availability[name];
                const ok = a?.status === "ok";
                return (
                  <span
                    key={name}
                    className={`badge ${ok ? "badge--green" : "badge--amber"}`}
                    title={a ? `${a.start ?? ""}..${a.end ?? ""}` : "missing"}
                  >
                    {name}: {a?.status ?? "missing"}
                  </span>
                );
              })}
            </div>
          ) : null}
          <label>
            Strategy{" "}
            <select value={sleeve} onChange={(e) => setSleeve(e.target.value)}>
              {BASE_SLEEVES.map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
              {Object.entries(candidates).map(([k, c]) => (
                <option key={k} value={k}>
                  {c.label ?? k}
                </option>
              ))}
            </select>
          </label>
          {isCandidate ? (
            <>
              <label>
                Fee tier{" "}
                <select
                  value={feeTier || candidate?.default_fee_tier || "adv2_spot"}
                  onChange={(e) => setFeeTier(e.target.value)}
                >
                  {Object.entries(
                    feeTiers ?? {
                      adv2_spot: { maker_bps: 15, taker_bps: 30 },
                      adv2_fut: { maker_bps: 5.5, taker_bps: 7 },
                    },
                  ).map(([k, t]) => (
                    <option key={k} value={k}>
                      {k} ({t.maker_bps}/{t.taker_bps} bps)
                    </option>
                  ))}
                </select>
              </label>
              <label>
                Execution{" "}
                <select
                  value={execMode}
                  onChange={(e) =>
                    setExecMode(e.target.value as "maker" | "taker" | "mixed")
                  }
                >
                  <option value="maker">maker</option>
                  <option value="taker">taker</option>
                  <option value="mixed">mixed</option>
                </select>
              </label>
              <label>
                <input
                  type="checkbox"
                  checked={combine}
                  onChange={(e) => setCombine(e.target.checked)}
                />{" "}
                Combine w/ Trend
              </label>
              <details>
                <summary>Preset params</summary>
                <div className="stat-grid" style={{ marginTop: 8 }}>
                  {Object.entries(candidate?.defaults ?? {}).map(([k, v]) => (
                    <label key={k} style={{ fontSize: 12 }}>
                      {k}
                      <input
                        value={overrides[k] ?? String(v ?? "")}
                        onChange={(e) =>
                          setOverrides({ ...overrides, [k]: e.target.value })
                        }
                      />
                    </label>
                  ))}
                </div>
              </details>
            </>
          ) : null}
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
          <button
            onClick={() => sweep.mutate({})}
            disabled={sweep.isPending}
            style={{ marginLeft: 8 }}
          >
            {sweep.isPending ? "Sweeping…" : "Run Candidate Sweep"}
          </button>
          {errorText ? <p className="error">{errorText}</p> : null}
          {sweep.isError ? (
            <p className="error">{errorMessage(sweep.error)}</p>
          ) : null}
        </div>

        <div className="backtest-layout__results">
          {!result ? (
            <div>
              <p className="muted">
                Configure parameters and click Run Backtest to simulate.
              </p>
              {sweep.data ? <SweepTable sweep={sweep.data} copy={copy} copied={copied} /> : null}
            </div>
          ) : (
            <>
              {result.compact_summary ? (
                <CompactScorecard summary={result.compact_summary} />
              ) : null}
              {result.compact_report ? (
                <button onClick={() => copy("report", result.compact_report!)}>
                  {copied === "report" ? "Copied!" : "Copy LLM Report"}
                </button>
              ) : null}
              {sweep.data ? (
                <SweepTable sweep={sweep.data} copy={copy} copied={copied} />
              ) : null}
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
                        <th>Cost</th>
                        <th>PnL</th>
                        <th>Fee</th>
                      </tr>
                    </thead>
                    <tbody>
                      {result.trade_log.map((t, i) => {
                        const cost = Number(t.size) * Number(t.price);
                        return (
                          <tr key={i}>
                            <td>{t.date}</td>
                            <td>{t.asset}</td>
                            <td>{t.side}</td>
                            <td>{Number(t.size)}</td>
                            <td>{usdCents(Number(t.price))}</td>
                            <td>{cost > 0 ? usdCents(cost) : "—"}</td>
                            <td>{usdCents(Number(t.pnl))}</td>
                            <td>{usdCents(Number(t.fee))}</td>
                          </tr>
                        );
                      })}
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
