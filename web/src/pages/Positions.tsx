import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { api, type PlanOut } from "../api/client";
import { pct, timeAgo, usd } from "../lib/time";

function FundingWatch() {
  const funding = useQuery({ queryKey: ["funding"], queryFn: api.funding });
  if (funding.isLoading) return <p className="muted">Loading funding watch…</p>;
  if (funding.isError || !funding.data)
    return <p className="muted">Could not load funding rates.</p>;
  const { rates, t_bill_rate } = funding.data;
  return (
    <p className="funding-line">
      Funding watch:{" "}
      {rates.length === 0 ? (
        <span className="muted">no funding rates recorded yet.</span>
      ) : (
        rates.map((r) => (
          <span key={`${r.symbol}-${r.venue}`} className="funding-line__rate">
            {r.symbol} {pct(Number(r.annual_rate))} a year ({r.venue},{" "}
            {timeAgo(r.time, new Date())})
          </span>
        ))
      )}{" "}
      vs T-bill {pct(t_bill_rate)}
      {rates.length > 0 && rates.every((r) => Number(r.annual_rate) > t_bill_rate)
        ? " — funding beats T-bills; a carry trade is worth proposing."
        : ""}
    </p>
  );
}

function PlanPanel({ id, onClose }: { id: number; onClose: () => void }) {
  const plan = useQuery({ queryKey: ["plan", id], queryFn: () => api.plan(id) });
  if (plan.isLoading) return <p className="muted">Loading plan…</p>;
  if (plan.isError || !plan.data)
    return <p className="muted">Could not load plan {id}.</p>;
  const p: PlanOut = plan.data;
  return (
    <div className="card" data-testid="plan-panel">
      <div className="card__title">
        Plan {p.id} — {p.side} {p.symbol}
        <button className="link-button" onClick={onClose}>
          close
        </button>
      </div>
      <p>{p.rationale}</p>
      <table className="data-table">
        <tbody>
          <tr>
            <td>Status</td>
            <td>{p.status}</td>
          </tr>
          <tr>
            <td>Stop</td>
            <td>{p.stop_price ? usd(p.stop_price) : "—"}</td>
          </tr>
          <tr>
            <td>Worst case</td>
            <td>{usd(p.worst_case_loss)}</td>
          </tr>
          <tr>
            <td>Est. fees</td>
            <td>{usd(p.est_fees)}</td>
          </tr>
          <tr>
            <td>Expires</td>
            <td>{p.expires_at.slice(0, 16).replace("T", " ")}Z</td>
          </tr>
        </tbody>
      </table>
    </div>
  );
}

export function Positions() {
  const positions = useQuery({ queryKey: ["positions"], queryFn: api.positions });
  const [planId, setPlanId] = useState<number | null>(null);

  if (positions.isLoading) return <p>Loading…</p>;
  if (positions.isError || !positions.data)
    return <p>Could not load positions.</p>;

  return (
    <div className="page">
      <div className="card">
        <div className="card__title">Positions</div>
        {positions.data.length === 0 ? (
          <p className="muted">All cash. No positions.</p>
        ) : (
          <table className="data-table">
            <thead>
              <tr>
                <th>Asset</th>
                <th>Value</th>
                <th>Share</th>
                <th>Avg price</th>
                <th>Gain</th>
                <th>Stop</th>
                <th>Resting orders</th>
                <th>Plan</th>
              </tr>
            </thead>
            <tbody>
              {positions.data.map((p) => (
                <tr key={p.symbol}>
                  <td>
                    {p.asset}
                    <div className="muted">{p.quantity}</div>
                  </td>
                  <td>{usd(p.value_usd)}</td>
                  <td>{pct(p.share)}</td>
                  <td>{p.avg_cost ? usd(p.avg_cost) : "—"}</td>
                  <td>
                    {p.unrealised_gain_usd === null
                      ? "—"
                      : usd(p.unrealised_gain_usd)}
                  </td>
                  <td>{p.stop_price ? usd(p.stop_price) : "—"}</td>
                  <td>
                    {p.resting_orders.length === 0
                      ? "—"
                      : p.resting_orders.map((o) => (
                          <div key={o.id}>
                            {o.side} {o.type} {o.quantity} @{" "}
                            {o.price ? usd(o.price) : "market"} ({o.status})
                          </div>
                        ))}
                  </td>
                  <td>
                    {p.plan_id === null ? (
                      "—"
                    ) : (
                      <button
                        className="link-button"
                        onClick={() => setPlanId(p.plan_id)}
                      >
                        plan {p.plan_id}
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        <FundingWatch />
      </div>
      {planId !== null ? <PlanPanel id={planId} onClose={() => setPlanId(null)} /> : null}
    </div>
  );
}
