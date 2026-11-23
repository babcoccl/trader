import { useQuery } from "@tanstack/react-query";
import { api, type ShadowSleeveOut, type ShadowTradeOut } from "../api/client";

function money(x: string | null): string {
  if (x === null) return "—";
  const n = Number(x);
  return n.toLocaleString("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 2,
  });
}

function signedPct(value: number | null): string {
  if (value === null) return "—";
  const p = value * 100;
  return `${p >= 0 ? "+" : ""}${p.toFixed(1)}%`;
}

function when(iso: string): string {
  return `${iso.slice(0, 16).replace("T", " ")} UTC`;
}

function TradesTable({
  trades,
  closed,
}: {
  trades: ShadowTradeOut[];
  closed: boolean;
}) {
  return (
    <div className="table-scroll">
      <table className="data-table">
        <thead>
          <tr>
            <th>Asset</th>
            <th>Opened</th>
            {closed ? <th>Closed</th> : null}
            <th>Entry</th>
            {closed ? <th>Exit</th> : null}
            {closed ? <th>Result</th> : <th>Dollars spent</th>}
            {closed ? <th>Sold because</th> : null}
          </tr>
        </thead>
        <tbody>
          {trades.map((t, i) => (
            <tr key={i}>
              <td>{t.symbol}</td>
              <td>{when(t.opened_at)}</td>
              {closed ? <td>{t.closed_at ? when(t.closed_at) : "—"}</td> : null}
              <td>{money(t.entry_price)}</td>
              {closed ? <td>{money(t.exit_price)}</td> : null}
              {closed ? <td>{money(t.pnl_usd)}</td> : <td>{money(t.cost_usd)}</td>}
              {closed ? <td>{t.exit_reason ?? "—"}</td> : null}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function SleeveCard({ sleeve }: { sleeve: ShadowSleeveOut }) {
  return (
    <div className="card">
      <div className="card__title">{sleeve.title}</div>
      <p className="muted">{sleeve.description}</p>
      {sleeve.started_on === null ? (
        <p className="muted">
          No record yet. The first entry appears after the next daily decision.
        </p>
      ) : (
        <>
          {sleeve.last_note ? (
            <p>
              {sleeve.last_day}: {sleeve.last_note}
            </p>
          ) : null}
          <p>
            Since {sleeve.started_on} ({sleeve.days} days):{" "}
            {signedPct(sleeve.return_fraction)} on a $10,000 book after{" "}
            {money(sleeve.fees_usd)} in fees. Worst loss{" "}
            {signedPct(sleeve.worst_drawdown)}. Trading account over the same
            days: {signedPct(sleeve.account_return_fraction)}.
          </p>
          <p>
            {sleeve.closed_trades} closed trades, {sleeve.winning_trades} with a
            gain.
          </p>
          {sleeve.open.length > 0 ? (
            <>
              <h3>Open</h3>
              <TradesTable trades={sleeve.open} closed={false} />
            </>
          ) : null}
          {sleeve.recent_closed.length > 0 ? (
            <>
              <h3>Recently closed</h3>
              <TradesTable trades={sleeve.recent_closed} closed={true} />
            </>
          ) : null}
        </>
      )}
    </div>
  );
}

export function Shadow() {
  const query = useQuery({
    queryKey: ["shadow"],
    queryFn: api.shadow,
    refetchInterval: 60_000,
  });

  if (query.isLoading) return <p>Loading…</p>;
  if (query.isError || !query.data)
    return <p>Could not load the shadow view.</p>;

  return (
    <div className="page">
      <p className="muted">
        Hypothetical results. These sleeves place no orders and do not use the
        account.
      </p>
      {query.data.sleeves.map((s) => (
        <SleeveCard key={s.name} sleeve={s} />
      ))}
    </div>
  );
}
