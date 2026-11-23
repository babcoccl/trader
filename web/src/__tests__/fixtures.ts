import type {
  DecisionOut,
  OpenOrderOut,
  RuleOut,
  RulesOut,
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

const RULE_FIXTURE: [string, string, boolean | null][] = [
  ["sma50", "Price above its 50-day average", true],
  ["sma100", "Price above its 100-day average", false],
  ["sma200", "Price above its 200-day average", false],
  ["momentum30", "Price above its level 30 days ago", true],
  ["momentum90", "Price above its level 90 days ago", false],
  ["momentum180", "Price above its level 180 days ago", false],
  [
    "breakout20",
    "20-day breakout (on at a 20-day high, off at a 10-day low)",
    false,
  ],
  [
    "breakout55",
    "55-day breakout (on at a 55-day high, off at a 27-day low)",
    false,
  ],
  [
    "breakout100",
    "100-day breakout (on at a 100-day high, off at a 50-day low)",
    null,
  ],
];

function makeRuleOut(
  [name, label, positive]: [string, string, boolean | null],
  i: number,
): RuleOut {
  const level = 50000 + i * 100;
  return {
    name,
    label,
    positive,
    level,
    distance: 60000 / level - 1,
  };
}

export function makeRulesOut(overrides: Partial<RulesOut> = {}): RulesOut {
  return {
    next_daily_decision_at: new Date(Date.now() + 5 * 3600_000).toISOString(),
    latest_daily: {
      ran_at: iso(3600_000),
      action: "none",
      reason: "Checked. Nothing to do.",
    },
    assets: ["BTC-USD", "ETH-USD"].map((symbol) => ({
      symbol,
      decided_at: iso(3600_000),
      action: "none",
      reason: `Checked ${symbol}. 2 of 9 rules positive. Target 0%, holding 0%.`,
      last_close: 60000,
      rules_positive: 2,
      rules: RULE_FIXTURE.map(makeRuleOut),
      annual_vol: 0.55,
      raw_weight: 0.11,
      target_weight: "0.000000000000000000",
      current_weight: "0.000000000000000000",
      regime: null,
    })),
    intraday: {
      ran_at: iso(60_000),
      action: "none",
      reason: "Checked prices. No stop is near. Nothing to do.",
      assets: ["BTC-USD", "ETH-USD"].map((symbol) => ({
        symbol,
        last_price: 60000,
        nearest_stop_distance: null,
        drop_bid: 54000,
        drop_bid_distance: 0.1,
      })),
    },
    parameters: [
      { name: "Assets", value: "BTC-USD, ETH-USD" },
      { name: "Volatility target", value: "40%" },
    ],
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
