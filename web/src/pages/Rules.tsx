import { useQuery } from "@tanstack/react-query";
import { api, type AssetRulesOut, type RulesOut } from "../api/client";
import { countdown, pct, timeAgo } from "../lib/time";

function formatPrice(x: number | null | undefined): string {
  if (x === null || x === undefined || Number.isNaN(x)) return "—";
  if (x >= 1000)
    return x.toLocaleString("en-US", { maximumFractionDigits: 0 });
  if (x >= 1) return x.toFixed(2);
  return x.toFixed(4);
}

function signedPct(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  const p = value * 100;
  return `${p >= 0 ? "+" : ""}${p.toFixed(1)}%`;
}

function weightPct(value: string | null): string {
  return value === null ? "—" : pct(Number(value));
}

function decidedAt(iso: string): string {
  return `${iso.slice(0, 16).replace("T", " ")} UTC`;
}

function AssetBlock({ asset }: { asset: AssetRulesOut }) {
  if (asset.decided_at === null) {
    return (
      <div key={asset.symbol}>
        <h3>{asset.symbol}</h3>
        <p className="muted">No daily decision recorded yet.</p>
      </div>
    );
  }
  const missingLevel = asset.rules.some((r) => r.level === null);
  return (
    <div className="rules-asset">
      <h3>
        {asset.symbol}: {asset.rules_positive ?? "—"} of 9 rules on
      </h3>
      <p>{asset.reason}</p>
      <p className="muted">Decided {decidedAt(asset.decided_at)}</p>
      <p>
        Volatility {pct(asset.annual_vol)} a year. Target{" "}
        {weightPct(asset.target_weight)}, holding{" "}
        {weightPct(asset.current_weight)}.
      </p>
      <div className="table-scroll">
        <table className="data-table">
          <thead>
            <tr>
              <th>Rule</th>
              <th>Status</th>
              <th>Compared with</th>
              <th>Last close against it</th>
            </tr>
          </thead>
          <tbody>
            {asset.rules.map((r) => (
              <tr key={r.name}>
                <td>{r.label}</td>
                <td>
                  {r.positive === null ? "No data" : r.positive ? "On" : "Off"}
                </td>
                <td>{formatPrice(r.level)}</td>
                <td>{signedPct(r.distance)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {missingLevel ? (
        <p className="muted">Levels appear after the next daily decision.</p>
      ) : null}
    </div>
  );
}

function TrendCard({ data }: { data: RulesOut }) {
  return (
    <div className="card">
      <div className="card__title">Trend rules (decided daily at 00:05 UTC)</div>
      <p>Next daily decision {countdown(data.next_daily_decision_at, new Date())}.</p>
      {data.latest_daily &&
      (data.latest_daily.action === "blocked" ||
        data.latest_daily.action === "error") ? (
        <p className="error">
          Latest daily run {data.latest_daily.action}:{" "}
          {data.latest_daily.reason}
        </p>
      ) : null}
      {data.assets.map((a) => (
        <AssetBlock key={a.symbol} asset={a} />
      ))}
    </div>
  );
}

function IntradayCard({ data }: { data: RulesOut }) {
  const intraday = data.intraday;
  return (
    <div className="card">
      <div className="card__title">Five-minute check</div>
      <p className="muted">
        Observation only. The sudden-drop sleeve is parked and places no orders.
      </p>
      {intraday === null ? (
        <p className="muted">No five-minute check recorded yet.</p>
      ) : (
        <>
          <p>{intraday.reason}</p>
          <p className="muted">
            Ran {timeAgo(intraday.ran_at, new Date())} (
            {decidedAt(intraday.ran_at)})
          </p>
          {intraday.assets.map((a) => (
            <p key={a.symbol}>
              {a.symbol} {formatPrice(a.last_price)}. Nearest stop{" "}
              {a.nearest_stop_distance === null
                ? "none"
                : `${pct(a.nearest_stop_distance)} under the price`}
              .{" "}
              {a.drop_bid === null ? (
                <span className="muted">No sudden-drop level.</span>
              ) : (
                `Sudden-drop level ${formatPrice(a.drop_bid)} (${pct(
                  a.drop_bid_distance,
                )} under the price).`
              )}
            </p>
          ))}
        </>
      )}
    </div>
  );
}

export function Rules() {
  const query = useQuery({
    queryKey: ["rules"],
    queryFn: api.rules,
    refetchInterval: 60_000,
  });

  if (query.isLoading) return <p>Loading…</p>;
  if (query.isError || !query.data)
    return <p>Could not load the rules view.</p>;
  const data = query.data;

  return (
    <div className="page">
      <TrendCard data={data} />
      <IntradayCard data={data} />
      <div className="card">
        <div className="card__title">Fixed settings</div>
        <table className="kv-table">
          <tbody>
            {data.parameters.map((p) => (
              <tr key={p.name}>
                <td className="muted">{p.name}</td>
                <td>{p.value}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
