import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import { pct, usd } from "../lib/time";

function StatCard({ title, value, sub }: { title: string; value: string; sub?: string }) {
  return (
    <div className="card stat-card">
      <div className="stat-card__title">{title}</div>
      <div className="stat-card__value">{value}</div>
      {sub ? <div className="stat-card__sub">{sub}</div> : null}
    </div>
  );
}

function signedPct(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  const p = value * 100;
  return `${p >= 0 ? "+" : ""}${p.toFixed(1)}%`;
}

export function Reports() {
  const summary = useQuery({ queryKey: ["summary"], queryFn: api.summary });
  const digests = useQuery({ queryKey: ["digests"], queryFn: () => api.digests(30) });

  if (summary.isLoading) return <p>Loading…</p>;
  if (summary.isError || !summary.data) return <p>Could not load the summary.</p>;
  const s = summary.data;

  const gap =
    s.paper_vs_backtest_gap_pp === null
      ? "—"
      : `${s.paper_vs_backtest_gap_pp >= 0 ? "+" : ""}${s.paper_vs_backtest_gap_pp.toFixed(1)} points`;

  return (
    <div className="page">
      <div className="stat-grid">
        <StatCard
          title="Return since start"
          value={signedPct(s.return_pct)}
          sub={
            s.from_at && s.to_at
              ? `${s.from_at.slice(0, 10)} to ${s.to_at.slice(0, 10)}`
              : "no equity history yet"
          }
        />
        <StatCard
          title="Worst drop so far"
          value={s.worst_drop_pct === null ? "—" : `-${pct(s.worst_drop_pct)}`}
          sub="halts at 35.0%"
        />
        <StatCard title="Fees paid" value={usd(s.fees_usd)} />
        <StatCard
          title="Live vs backtest"
          value={gap}
          sub="gap between real fills and the model"
        />
      </div>

      <div className="card">
        <div className="card__title">Scorecards</div>
        <table className="kv-table">
          <tbody>
            <tr>
              <td className="muted">Against baselines</td>
              <td>
                Account vs cash, buy and hold BTC, buy and hold BTC+ETH — see the chart on
                Overview.
              </td>
            </tr>
            <tr>
              <td className="muted">Your vetoes</td>
              <td>
                {s.vetoes.plans === 0
                  ? "No denied, expired or voided plans yet."
                  : `${s.vetoes.plans} denied, expired or voided; ${
                      s.vetoes.shadow_fills
                    } shadow fills${
                      s.vetoes.result_usd === null
                        ? "."
                        : `; would-have-been result ${usd(s.vetoes.result_usd)}.`
                    }`}
              </td>
            </tr>
            <tr>
              <td className="muted">News view</td>
              <td>Shadow mode: nothing scored yet. Not allowed to change sizes.</td>
            </tr>
            <tr>
              <td className="muted">Promotion gate</td>
              <td>Paper trading. Next step: live with $1,000 after your sign-off.</td>
            </tr>
          </tbody>
        </table>
      </div>

      <div className="card">
        <div className="card__title">Daily digests</div>
        {digests.isError ? (
          <p className="muted">Could not load digests.</p>
        ) : (digests.data ?? []).length === 0 ? (
          <p className="muted">No digests yet; the first one is written after 00:30 UTC.</p>
        ) : (
          <ul className="digest-list">
            {(digests.data ?? []).map((d) => (
              <li key={d.id}>
                <details>
                  <summary>{d.day}</summary>
                  <pre className="digest-body">{d.body_md}</pre>
                </details>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
