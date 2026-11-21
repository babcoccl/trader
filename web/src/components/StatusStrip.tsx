import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { api } from "../api/client";
import { countdown, timeAgo } from "../lib/time";

export const STALE_HEARTBEAT_MS = 5 * 60 * 1000;

function staleServiceNames(
  services: { name: string; status: string; last_heartbeat_at: string | null }[],
  now: Date,
) {
  return services
    .filter(
      (s) =>
        s.status !== "ok" ||
        s.last_heartbeat_at === null ||
        now.getTime() - Date.parse(s.last_heartbeat_at) > STALE_HEARTBEAT_MS,
    )
    .map((s) => s.name);
}

export function StatusStrip() {
  const queryClient = useQueryClient();
  const [alarm, setAlarm] = useState<string | null>(null);
  const [now, setNow] = useState(() => new Date());
  const status = useQuery({
    queryKey: ["status"],
    queryFn: api.status,
    refetchInterval: 30_000,
  });

  // Live updates: an alarm event turns the strip red; other events refresh data.
  useEffect(() => {
    const source = new EventSource("/api/stream");
    const refresh = () => {
      void queryClient.invalidateQueries();
    };
    source.addEventListener("alarm", (e) => {
      try {
        const data = JSON.parse((e as MessageEvent).data) as { message?: string; kind?: string };
        setAlarm(data.kind === "alarm_cleared" ? null : (data.message ?? "alarm"));
      } catch {
        setAlarm("alarm");
      }
      refresh();
    });
    for (const ev of ["status", "decision", "plan", "fill"]) {
      source.addEventListener(ev, refresh);
    }
    return () => source.close();
  }, [queryClient]);

  // Tick once a second so "ago" and countdown stay current.
  useEffect(() => {
    const t = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(t);
  }, []);

  if (status.isLoading) {
    return <div className="status-strip">Connecting…</div>;
  }
  if (status.isError || !status.data) {
    return <div className="status-strip status-strip--problem">Problem: cannot reach the API.</div>;
  }

  const s = status.data;
  const stale = staleServiceNames(s.services, now);

  let badge = "Running";
  let problem: string | null = null;
  if (s.halted) {
    badge = "Halted";
    problem = s.halt_reason ?? "halted";
  }
  if (stale.length > 0) {
    badge = "Problem";
    problem = `${stale.join(", ")} heartbeat stale or failing`;
  }
  if (alarm) {
    badge = "Problem";
    problem = alarm;
  }

  return (
    <div className={`status-strip ${problem ? "status-strip--problem" : ""}`}>
      <span className={`badge ${problem ? "badge--red" : "badge--green"}`}>{badge}</span>
      <span className="status-strip__line">
        {problem ? <>{problem}. </> : null}
        {s.last_check_reason ?? "Waiting for the first check."} Last check{" "}
        {timeAgo(s.last_check_at, now)}. Next daily decision{" "}
        {countdown(s.next_daily_decision_at, now)}.
      </span>
      <span className="status-strip__sub">Last trade {timeAgo(s.last_trade_at, now)}.</span>
    </div>
  );
}
