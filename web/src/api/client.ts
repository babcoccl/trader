// Typed client for the dashboard API (docs/API.md).
// Money and quantities are JSON strings (NUMERIC(38,18) on the wire);
// timestamps are ISO 8601 UTC strings.

export interface ServiceStatus {
  name: string;
  status: string;
  last_heartbeat_at: string | null;
}

export interface StatusOut {
  mode: "paper" | "live";
  halted: boolean;
  halt_reason: string | null;
  equity_usd: string;
  invested_fraction: number;
  drawdown: number;
  halt_at: number;
  pending_plans: number;
  last_check_at: string | null;
  last_check_reason: string | null;
  next_daily_decision_at: string | null;
  last_trade_at: string | null;
  services: ServiceStatus[];
}

export interface DecisionOut {
  id: number;
  cycle: "daily" | "intraday";
  ran_at: string;
  symbol: string | null;
  target_weight: string | null;
  current_weight: string | null;
  action: "none" | "plan_proposed" | "blocked" | "error";
  reason: string;
  details: Record<string, unknown>;
}

export interface OrderOut {
  id: number;
  client_order_id: string;
  side: "buy" | "sell";
  type: "limit" | "market" | "stop";
  price: string | null;
  stop_price: string | null;
  quantity: string;
  status: string;
  created_at: string;
}

export interface PlanOrderSpec {
  side: "buy" | "sell";
  type: "limit" | "market" | "stop";
  price: string | null;
  stop_price: string | null;
  quantity: string;
  market_after_seconds?: number;
  maker_or_cancel?: boolean;
  reprice_seconds?: number;
}

export interface PlanOut {
  id: number;
  sleeve: "trend" | "dip_bid";
  symbol: string;
  side: "buy" | "sell";
  status: "pending" | "approved" | "denied" | "expired" | "voided" | "completed";
  reference_price: string;
  target_weight: string;
  size_fraction: string;
  entry_orders: PlanOrderSpec[];
  stop_price: string | null;
  worst_case_loss: string;
  est_fees: string;
  rationale: string;
  expires_at: string;
  decided_at: string | null;
  decision_id: number | null;
  created_at: string;
}

export interface OpenOrderOut extends OrderOut {
  symbol: string;
  plan_id: number | null;
}

export interface PositionOut {
  asset: string;
  symbol: string;
  quantity: string;
  price: string;
  value_usd: string;
  share: number;
  avg_cost: string | null;
  unrealised_gain_usd: string | null;
  stop_price: string | null;
  plan_id: number | null;
  resting_orders: OrderOut[];
}

export interface ActivityItem {
  time: string;
  kind: string;
  ref_id: number;
  text: string;
  details: Record<string, unknown>;
}

export interface FundingRateOut {
  symbol: string;
  venue: string;
  time: string;
  rate: string;
  interval_hours: number;
  annual_rate: string;
}

export interface FundingOut {
  rates: FundingRateOut[];
  t_bill_rate: number;
}

export interface EquityPoint {
  time: string;
  account: string;
  cash: string | null;
  btc: string | null;
  btc_eth: string | null;
}

export interface SummaryOut {
  from_at: string | null;
  to_at: string | null;
  return_pct: number | null;
  worst_drop_pct: number | null;
  fees_usd: string;
  vetoes: { plans: number; shadow_fills: number; result_usd: string | null };
  paper_vs_backtest_gap_pp: number | null;
}

export interface WeeklyReportOut {
  week_start: string;
  week_end: string;
  message: string;
  body_md: string;
  tracking_gap_points: number | null;
  paper_return: number | null;
  backtest_return: number | null;
  mean_uptime: number | null;
}

export interface DigestOut {
  id: number;
  day: string;
  body_md: string;
  stats: Record<string, unknown>;
}

export interface SettingOut {
  key: string;
  value: unknown;
  updated_at: string | null;
}

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    credentials: "same-origin",
    headers: init?.body ? { "Content-Type": "application/json" } : undefined,
    ...init,
  });
  if (!res.ok) {
    const body = await res.text().catch(() => "");
    throw new ApiError(res.status, body || res.statusText);
  }
  return (await res.json()) as T;
}

export const api = {
  login: (password: string) =>
    request<{ status: string }>("/api/login", {
      method: "POST",
      body: JSON.stringify({ password }),
    }),
  logout: () => request<{ status: string }>("/api/logout", { method: "POST" }),
  status: () => request<StatusOut>("/api/status"),
  decisions: (params?: { cycle?: string; limit?: number; before?: string }) => {
    const q = new URLSearchParams();
    if (params?.cycle) q.set("cycle", params.cycle);
    if (params?.limit) q.set("limit", String(params.limit));
    if (params?.before) q.set("before", params.before);
    const qs = q.toString();
    return request<DecisionOut[]>(`/api/decisions${qs ? `?${qs}` : ""}`);
  },
  plans: (status?: string) =>
    request<PlanOut[]>(`/api/plans${status ? `?status=${status}` : ""}`),
  plan: (id: number) => request<PlanOut>(`/api/plans/${id}`),
  approvePlan: (id: number, sizeFraction: number) =>
    request<PlanOut>(`/api/plans/${id}/approve`, {
      method: "POST",
      body: JSON.stringify({ size_fraction: sizeFraction }),
    }),
  denyPlan: (id: number) =>
    request<PlanOut>(`/api/plans/${id}/deny`, { method: "POST" }),
  positions: () => request<PositionOut[]>("/api/positions"),
  openOrders: () => request<OpenOrderOut[]>("/api/orders/open"),
  funding: () => request<FundingOut>("/api/funding"),
  activity: (params?: {
    kind?: string;
    q?: string;
    limit?: number;
    before?: ActivityItem;
  }) => {
    const q = new URLSearchParams();
    if (params?.kind) q.set("kind", params.kind);
    if (params?.q) q.set("q", params.q);
    if (params?.limit) q.set("limit", String(params.limit));
    if (params?.before) {
      q.set("before", params.before.time);
      q.set("before_kind", params.before.kind);
      q.set("before_id", String(params.before.ref_id));
    }
    const qs = q.toString();
    return request<ActivityItem[]>(`/api/activity${qs ? `?${qs}` : ""}`);
  },
  equityReport: (params?: { from?: string; to?: string }) => {
    const q = new URLSearchParams();
    if (params?.from) q.set("from", params.from);
    if (params?.to) q.set("to", params.to);
    const qs = q.toString();
    return request<EquityPoint[]>(`/api/reports/equity${qs ? `?${qs}` : ""}`);
  },
  summary: () => request<SummaryOut>("/api/reports/summary"),
  weeklyReports: (limit?: number) =>
    request<WeeklyReportOut[]>(
      `/api/reports/weekly${limit ? `?limit=${limit}` : ""}`,
    ),
  digests: (limit?: number) =>
    request<DigestOut[]>(`/api/digests${limit ? `?limit=${limit}` : ""}`),
  settings: () => request<SettingOut[]>("/api/settings"),
  putSetting: (key: string, value: unknown) =>
    request<SettingOut>(`/api/settings/${key}`, {
      method: "PUT",
      body: JSON.stringify({ value }),
    }),
  halt: () =>
    request<{ status: string }>("/api/controls/halt", {
      method: "POST",
      body: JSON.stringify({}),
    }),
  resume: () =>
    request<{ status: string }>("/api/controls/resume", { method: "POST" }),
  flatten: (confirm: string) =>
    request<{ status: string }>("/api/controls/flatten", {
      method: "POST",
      body: JSON.stringify({ confirm }),
    }),
};
