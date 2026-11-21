import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { api, type ActivityItem, type PlanOut, type PositionOut } from "../api/client";
import { countdown, pct, usd } from "../lib/time";

const SIZES = [
  { label: "Full", value: 1 },
  { label: "Half", value: 0.5 },
  { label: "Quarter", value: 0.25 },
] as const;

// Per spec A: pending plans void when the price moves >2% from reference.
const VOID_MOVE = 0.02;

function KV({ rows }: { rows: [string, string][] }) {
  return (
    <table className="kv">
      <tbody>
        {rows.map(([k, v]) => (
          <tr key={k}>
            <td className="kv__key">{k}</td>
            <td>{v}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function notional(plan: PlanOut, fraction: number): number {
  return plan.entry_orders.reduce(
    (sum, o) =>
      sum + Number(o.quantity) * Number(o.price ?? plan.reference_price) * fraction,
    0,
  );
}

function PlanCard({ plan, now }: { plan: PlanOut; now: Date }) {
  const queryClient = useQueryClient();
  const status = useQuery({ queryKey: ["status"], queryFn: api.status });
  const [size, setSize] = useState(1);
  const [error, setError] = useState<string | null>(null);
  const onSettled = () => void queryClient.invalidateQueries();
  const approve = useMutation({
    mutationFn: (fraction: number) => api.approvePlan(plan.id, fraction),
    onSuccess: onSettled,
    onError: (e) => setError(e.message),
  });
  const deny = useMutation({
    mutationFn: () => api.denyPlan(plan.id),
    onSuccess: onSettled,
    onError: (e) => setError(e.message),
  });

  const equity = Number(status.data?.equity_usd ?? 0);
  const invested = status.data?.invested_fraction ?? 0;
  const expired = Date.parse(plan.expires_at) <= now.getTime();
  const busy = approve.isPending || deny.isPending;
  const amount = notional(plan, size);
  const worst = Number(plan.worst_case_loss) * size;
  const fees = Number(plan.est_fees) * size;
  const investedAfter =
    equity > 0 ? invested + (plan.side === "buy" ? amount : -amount) / equity : null;
  const order = plan.entry_orders[0];
  const orderPrice = order?.price ?? plan.reference_price;

  const entry =
    plan.side === "buy"
      ? `Buy ${usd(amount)} of ${plan.symbol} with a limit order at the best bid ` +
        `(about ${usd(orderPrice)}), re-priced every 5 minutes. Anything unfilled ` +
        `1 hour after approval is bought at market.`
      : `Sell ${usd(amount)} of ${plan.symbol} with a limit order at ` +
        `${usd(orderPrice)}, converted to market after 1 hour.`;
  const stop =
    plan.side === "buy" && plan.stop_price !== null
      ? `Emergency stop at ${usd(plan.stop_price)} ` +
        `(${pct(Number(plan.stop_price) / Number(plan.reference_price) - 1)}), ` +
        `checked by this application every 30 seconds; it does not run while this ` +
        `machine is offline. Runs without asking you again.`
      : "None; this plan only reduces exposure.";
  const rows: [string, string][] = [
    ["Entry", entry],
    [
      "Exit",
      plan.side === "sell"
        ? "This is the exit; it reduces exposure."
        : "Normal exits come from a later sell plan in the daily check.",
    ],
    ["Stop", stop],
    [
      "Worst case on this plan",
      `-${usd(worst)}${equity > 0 ? ` (${pct(worst / equity)} of account)` : ""}, including fees`,
    ],
    ["Fees and slippage", `About ${usd(fees)} in and out at Bitstamp rates`],
    [
      "Account after",
      investedAfter === null
        ? "—"
        : `Invested ${pct(investedAfter)}, cash ${pct(1 - investedAfter)}`,
    ],
    [
      "Void if",
      `${plan.symbol} moves more than ${(VOID_MOVE * 100).toFixed(0)}% from ` +
        `${usd(plan.reference_price)} before you answer`,
    ],
  ];

  return (
    <div className="card">
      <div className="card__title plan-card__title">
        <span>
          Plan #{plan.id}: {plan.side === "buy" ? "buy" : "sell"} {plan.symbol}
        </span>
        <span className={`badge ${expired ? "badge--red" : "badge--amber"}`}>
          {expired ? "Expired" : `Waiting for you, expires ${countdown(plan.expires_at, now)}`}
        </span>
      </div>
      <p className="plan-card__rationale">{plan.rationale}</p>
      <KV rows={rows} />
      <div className="plan-card__actions">
        <span className="muted">Size</span>
        <div className="size-selector" role="group" aria-label="Plan size">
          {SIZES.map((s) => (
            <button
              key={s.label}
              type="button"
              className={`size-selector__option ${size === s.value ? "size-selector__option--active" : ""}`}
              onClick={() => setSize(s.value)}
            >
              {s.label}
            </button>
          ))}
        </div>
        <button
          type="button"
          className="plan-card__approve"
          disabled={expired || busy}
          onClick={() => approve.mutate(size)}
        >
          Approve
        </button>
        <button
          type="button"
          className="plan-card__deny"
          disabled={expired || busy}
          onClick={() => deny.mutate()}
        >
          Deny
        </button>
      </div>
      {error ? <p className="error">{error}</p> : null}
    </div>
  );
}

// A plan's fills as net USD: sells pay, buys cost, fees cost, units still
// held are marked at the latest price (the same maths reports._veto_scorecard
// uses for shadow orders).
function fillsPnl(
  fills: ActivityItem[],
  prices: Record<string, number>,
): number {
  let cash = 0;
  const held: Record<string, number> = {};
  const lastFillPrice: Record<string, number> = {};
  for (const f of fills) {
    const d = f.details;
    const symbol = String(d.symbol ?? "");
    const asset = symbol.split("-")[0];
    const qty = Number(d.quantity ?? 0);
    const price = Number(d.price ?? 0);
    lastFillPrice[symbol] = price;
    const feeUsd =
      d.fee_currency === "USD" ? Number(d.fee ?? 0) : Number(d.fee ?? 0) * price;
    if (d.side === "buy") {
      cash -= price * qty;
      held[asset] = (held[asset] ?? 0) + qty;
    } else {
      cash += price * qty;
      held[asset] = (held[asset] ?? 0) - qty;
    }
    cash -= feeUsd;
  }
  for (const [asset, qty] of Object.entries(held)) {
    // A shadow-bought asset the account does not hold has no mark in
    // /positions; fall back to its own last fill price.
    const price = prices[`${asset}-USD`] ?? lastFillPrice[`${asset}-USD`];
    if (price !== undefined) cash += qty * price;
  }
  return cash;
}

function planLabel(plan: PlanOut): string {
  return `#${plan.id} ${plan.side === "buy" ? "Buy" : "Sell"} ${plan.symbol}, target ${pct(Number(plan.target_weight))}`;
}

function answerLabel(plan: PlanOut): string {
  if (plan.status === "approved" || plan.status === "completed") {
    const f = Number(plan.size_fraction);
    return `Approved${f === 1 ? ", full" : `, ${pct(f)}`}`;
  }
  if (plan.status === "denied") return "Denied";
  if (plan.status === "expired") return "Expired, no answer";
  return "Voided by price move";
}

function PastDecisions() {
  const plans = useQuery({ queryKey: ["plans", "past"], queryFn: () => api.plans() });
  const fills = useQuery({
    queryKey: ["activity", "fills"],
    queryFn: () => api.activity({ kind: "fill", limit: 500 }),
  });
  const positions = useQuery({ queryKey: ["positions"], queryFn: api.positions });

  const past = (plans.data ?? []).filter((p) => p.status !== "pending");
  const prices: Record<string, number> = {};
  for (const p of positions.data ?? ([] as PositionOut[])) {
    prices[p.symbol] = Number(p.price);
  }

  const byPlan = (items: ActivityItem[], planId: number, shadow: boolean) =>
    items.filter((f) => f.details.plan_id === planId && f.details.shadow === shadow);

  return (
    <div className="card">
      <div className="card__title">Past decisions</div>
      {plans.isError ? (
        <p className="muted">Could not load plans.</p>
      ) : past.length === 0 ? (
        <p className="muted">No past decisions yet.</p>
      ) : (
        <table className="table">
          <thead>
            <tr>
              <th>Plan</th>
              <th>Your answer</th>
              <th>Result if followed</th>
              <th>Your result</th>
            </tr>
          </thead>
          <tbody>
            {past.map((p) => {
              const real = byPlan(fills.data ?? [], p.id, false);
              const shadow = byPlan(fills.data ?? [], p.id, true);
              const followed =
                shadow.length > 0 ? fillsPnl(shadow, prices) : fillsPnl(real, prices);
              const yours = fillsPnl(real, prices);
              const dead =
                p.status === "denied" || p.status === "expired" || p.status === "voided";
              return (
                <tr key={p.id}>
                  <td>{planLabel(p)}</td>
                  <td>{answerLabel(p)}</td>
                  <td>
                    {real.length + shadow.length === 0 ? "No fills" : pnlText(followed)}
                  </td>
                  <td>
                    {dead
                      ? shadow.length === 0
                        ? "—"
                        : `$0 (${followed <= 0 ? "veto helped" : "missed"})`
                      : real.length === 0
                        ? "No fills"
                        : pnlText(yours)}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </div>
  );
}

function pnlText(v: number): string {
  return `${v >= 0 ? "+" : "-"}${usd(Math.abs(v))}`;
}

export function Approvals() {
  const [now, setNow] = useState(() => new Date());
  const pending = useQuery({
    queryKey: ["plans", "pending"],
    queryFn: () => api.plans("pending"),
    refetchInterval: 15_000,
  });
  useEffect(() => {
    const t = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(t);
  }, []);

  if (pending.isLoading) return <p>Loading…</p>;
  if (pending.isError || !pending.data) return <p>Could not load plans.</p>;

  return (
    <div className="page">
      {pending.data.length === 0 ? (
        <div className="card">
          <p className="muted">Nothing waiting for you. Silence means no trade.</p>
        </div>
      ) : (
        pending.data.map((p) => <PlanCard key={p.id} plan={p} now={now} />)
      )}
      <PastDecisions />
      <p className="muted">
        Every request states the worst case in dollars and can be shrunk but never
        enlarged. No answer means no trade.
      </p>
    </div>
  );
}
