import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../api/client";
import { timeAgo } from "../lib/time";

const PAGE_SIZE = 50;
const KINDS = ["decision", "fill", "heartbeat", "alarm", "alarm_cleared", "heartbeat_summary", "emergency_stop", "deposit"];

export function Activity() {
  const [kind, setKind] = useState("");
  const [q, setQ] = useState("");
  const [limit, setLimit] = useState(PAGE_SIZE);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());

  const activity = useQuery({
    queryKey: ["activity", kind, q, limit],
    queryFn: () =>
      api.activity({ kind: kind || undefined, q: q || undefined, limit }),
    placeholderData: (prev) => prev,
  });

  const items = activity.data ?? [];
  const kinds = Array.from(new Set([...KINDS, ...items.map((i) => i.kind)])).sort();

  const toggle = (key: string) =>
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });

  return (
    <div className="page">
      <div className="card">
        <div className="card__title">Activity</div>
        <div className="activity-filters">
          <select
            aria-label="kind filter"
            value={kind}
            onChange={(e) => {
              setKind(e.target.value);
              setLimit(PAGE_SIZE);
            }}
          >
            <option value="">all kinds</option>
            {kinds.map((k) => (
              <option key={k} value={k}>
                {k}
              </option>
            ))}
          </select>
          <input
            aria-label="search"
            placeholder="search text…"
            value={q}
            onChange={(e) => {
              setQ(e.target.value);
              setLimit(PAGE_SIZE);
            }}
          />
        </div>
        {activity.isLoading ? (
          <p>Loading…</p>
        ) : activity.isError ? (
          <p>Could not load activity.</p>
        ) : items.length === 0 ? (
          <p className="muted">Nothing recorded yet.</p>
        ) : (
          <>
            <ul className="activity-list">
              {items.map((i) => {
                const key = `${i.kind}-${i.ref_id}`;
                return (
                  <li key={key}>
                    <button className="activity-row" onClick={() => toggle(key)}>
                      <span className="muted">{timeAgo(i.time, new Date())}</span>{" "}
                      <span className="badge">{i.kind}</span> {i.text}
                    </button>
                    {expanded.has(key) ? (
                      <pre className="activity-raw">
                        {JSON.stringify(i, null, 2)}
                      </pre>
                    ) : null}
                  </li>
                );
              })}
            </ul>
            {items.length >= limit ? (
              <button
                className="link-button"
                onClick={() => setLimit((l) => l + PAGE_SIZE)}
              >
                load more
              </button>
            ) : null}
          </>
        )}
      </div>
    </div>
  );
}
