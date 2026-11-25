import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Overview } from "../pages/Overview";
import { makeDecision, makeStatus } from "./fixtures";

vi.mock("../api/client", () => ({
  api: {
    status: vi.fn(),
    decisions: vi.fn(),
    equityReport: vi.fn(),
    subsidy: vi.fn(),
  },
}));

const { api } = await import("../api/client");
const statusMock = vi.mocked(api.status);
const decisionsMock = vi.mocked(api.decisions);
const equityMock = vi.mocked(api.equityReport);
const subsidyMock = vi.mocked(api.subsidy);

function renderOverview() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <Overview />
    </QueryClientProvider>,
  );
}

afterEach(() => vi.restoreAllMocks());

describe("Overview", () => {
  it("renders the four stat cards and decision sections", async () => {
    statusMock.mockResolvedValue(makeStatus());
    const dailyDecision = makeDecision({
      id: 2,
      cycle: "daily",
      reason: "Checked BTC-USD. 5 of 9 rules positive.",
    });
    const intradayDecision = makeDecision({
      id: 1,
      cycle: "intraday",
      reason: "Checked prices. Nothing to do.",
    });
    decisionsMock.mockImplementation((params) => {
      if (params?.cycle === "daily") return Promise.resolve([dailyDecision]);
      if (params?.cycle === "intraday") return Promise.resolve([intradayDecision]);
      return Promise.resolve([intradayDecision, dailyDecision]);
    });
    equityMock.mockResolvedValue([
      { time: "2026-11-21T00:00:00Z", account: "10420.5", cash: "10420.5", btc: "10300", btc_eth: "10350" },
    ]);
    subsidyMock.mockResolvedValue({
      mode: "paper",
      status: "active",
      reason: "Boosting volume toward the next fee tier.",
      trailing_30d_volume_usd: "5200",
      today_booster_volume_usd: "120",
      booster_trades_30d: 14,
      booster_volume_30d_usd: "1800",
      booster_fees_30d_usd: "7.20",
      tier: "Tier 1",
      maker_bps: 40,
      taker_bps: 60,
      target_volume_usd: "10000",
      remaining_to_target_usd: "4800",
      progress_pct: 0.52,
      daily_cap_usd: "250",
      adx: 18.4,
      adx_max: 25,
      open_orders_usd: "750",
      booster_net_pnl_30d_usd: "6.04",
      recent_fills: [
        {
          time: "2026-11-21T18:00:00Z",
          side: "sell",
          price: "50000.000000000000000000",
          quantity: "0.010000000000000000",
          notional_usd: "500.000000000000000000",
          fee_usd: "2.000000000000000000",
        },
        {
          time: "2026-11-21T17:00:00Z",
          side: "buy",
          price: "49000.000000000000000000",
          quantity: "0.020000000000000000",
          notional_usd: "980.000000000000000000",
          fee_usd: "3.920000000000000000",
        },
      ],
    });
    renderOverview();

    expect(await screen.findByText("Account value")).toBeInTheDocument();
    expect(screen.getByText("$10,421")).toBeInTheDocument();
    expect(screen.getByText("Invested now")).toBeInTheDocument();
    expect(screen.getByText("42.0%")).toBeInTheDocument();
    expect(screen.getByText("Drop from peak")).toBeInTheDocument();
    expect(screen.getByText("3.0%")).toBeInTheDocument();
    expect(screen.getByText(/halts at 35\.0%/)).toBeInTheDocument();
    expect(screen.getByText("Needs your decision")).toBeInTheDocument();
    expect(screen.getByText("1")).toBeInTheDocument();

    expect(await screen.findByText("Fee-Tier Subsidy")).toBeInTheDocument();
    expect(screen.getByText("Active")).toBeInTheDocument();
    expect(screen.getByText("Boosting volume toward the next fee tier.")).toBeInTheDocument();
    expect(screen.getByText("Tier 1 (40 / 60 bps)")).toBeInTheDocument();
    expect(screen.getByText("BTC ADX 18.4")).toBeInTheDocument();
    expect(screen.getByText(/\$5,200 of \$10,000 · 52\.0%/)).toBeInTheDocument();
    expect(screen.getByRole("progressbar", { name: "Fee-tier progress" })).toHaveAttribute(
      "aria-valuenow",
      "52",
    );
    expect(screen.getByText(/\$4,800 to next tier/)).toBeInTheDocument();
    expect(screen.getByText("Booster Trades")).toBeInTheDocument();
    expect(screen.getByText("14")).toBeInTheDocument();
    expect(screen.getByText("Booster Volume")).toBeInTheDocument();
    expect(screen.getByText("$1,800")).toBeInTheDocument();
    expect(screen.getByText("Booster Fees Spent")).toBeInTheDocument();
    expect(screen.getByText("$7.20")).toBeInTheDocument();
    expect(screen.getByText("Open Orders")).toBeInTheDocument();
    expect(screen.getByText("$750")).toBeInTheDocument();
    expect(screen.getByText("30d Net PnL")).toBeInTheDocument();
    expect(screen.getByText("$6.04")).toBeInTheDocument();
    expect(screen.getByText("Recent Ping-Pong Fills")).toBeInTheDocument();
    expect(screen.getByText("sell")).toBeInTheDocument();
    expect(screen.getByText("$50,000.00")).toBeInTheDocument();
    // Trailing zeros stripped from fill quantities.
    expect(screen.getByText("0.01")).toBeInTheDocument();
    expect(screen.getByText("0.02")).toBeInTheDocument();
    expect(screen.getByText("$980.00")).toBeInTheDocument();
    expect(screen.getByText("$3.92")).toBeInTheDocument();

    expect(screen.getByText("What the system thinks right now")).toBeInTheDocument();
    expect(screen.getAllByText(/5 of 9 rules positive/).length).toBeGreaterThan(0);
    expect(screen.getByText("Why nothing has happened lately")).toBeInTheDocument();
  });
});
