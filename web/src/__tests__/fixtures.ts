import type {
  DecisionOut,
  OpenOrderOut,
  StatusOut,
  WeeklyReportOut,
} from "../api/client";

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

export function makeOpenOrder(overrides: Partial<OpenOrderOut> = {}): OpenOrderOut {
  return {
    id: 9,
    client_order_id: "5-1",
    symbol: "ETH-USD",
    side: "buy",
    type: "limit",
    price: "2900",
    stop_price: null,
    quantity: "0.1",
    status: "open",
    plan_id: 5,
    created_at: iso(3600_000),
    ...overrides,
  };
}

export function makeWeeklyReport(
  overrides: Partial<WeeklyReportOut> = {},
): WeeklyReportOut {
  return {
    week_start: "2026-11-09",
    week_end: "2026-11-15",
    message: "Weekly paper report 2026-11-09 to 2026-11-15",
    body_md: "# Weekly paper report — 2026-11-09 to 2026-11-15\n",
    tracking_gap_points: 1.5,
    paper_return: 0.02,
    backtest_return: 0.005,
    mean_uptime: 0.99,
    ...overrides,
  };
}
