import type { DecisionOut, StatusOut } from "../api/client";

function iso(msAgo: number): string {
  return new Date(Date.now() - msAgo).toISOString();
}

export function makeStatus(overrides: Partial<StatusOut> = {}): StatusOut {
  return {
    mode: "paper",
    halted: false,
    halt_reason: null,
    equity_usd: "10420.500000000000000000",
    invested_fraction: 0.42,
    drawdown: 0.03,
    halt_at: 0.35,
    pending_plans: 1,
    last_check_at: iso(10_000),
    last_check_reason: "Checked prices. Nothing to do.",
    next_daily_decision_at: new Date(Date.now() + 5 * 3600_000).toISOString(),
    last_trade_at: iso(2 * 24 * 3600_000),
    services: [
      { name: "collector", status: "ok", last_heartbeat_at: iso(5_000) },
      { name: "engine", status: "ok", last_heartbeat_at: iso(5_000) },
      { name: "executor", status: "ok", last_heartbeat_at: iso(5_000) },
    ],
    ...overrides,
  };
}

export function makeDecision(overrides: Partial<DecisionOut> = {}): DecisionOut {
  return {
    id: 61,
    cycle: "intraday",
    ran_at: iso(60_000),
    symbol: null,
    target_weight: null,
    current_weight: null,
    action: "none",
    reason: "Checked prices. Nothing to do.",
    details: {},
    ...overrides,
  };
}
