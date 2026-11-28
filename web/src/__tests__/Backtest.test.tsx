import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, api } from "../api/client";
import type { BacktestResult, BacktestSweepResult } from "../api/client";
import { Backtest } from "../pages/Backtest";

function renderBacktest() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <Backtest />
    </QueryClientProvider>,
  );
}

const MOCK_RESULT: BacktestResult = {
  summary_metrics: {
    cagr: 0.184,
    benchmark_cagr: 0.121,
    max_drawdown: 0.147,
    total_trades: 2,
    win_rate: 0.61,
    total_fees_paid: "87.40",
  },
  timeseries_data: [
    {
      timestamp: "2024-01-01",
      equity: "10000.0",
      benchmark_equity: "10000.0",
      drawdown: 0,
      asset_price: "42300.0",
    },
    {
      timestamp: "2024-02-01",
      equity: "10420.0",
      benchmark_equity: "10250.0",
      drawdown: 0.01,
      asset_price: "43100.0",
    },
  ],
  trade_log: [
    {
      date: "2024-01-15",
      asset: "BTC-USD",
      side: "BUY",
      size: "0.12",
      price: "42800",
      pnl: "0",
      fee: "5.14",
    },
    {
      date: "2024-06-20",
      asset: "ETH-USD",
      side: "SELL",
      size: "0.05",
      price: "2420",
      pnl: "82.50",
      fee: "2.18",
    },
  ],
};

const MOCK_COMPACT_RESULT: BacktestResult = {
  ...MOCK_RESULT,
  compact_summary: {
    strategy: "funding_downtrend",
    fee_tier: "adv2_spot",
    execution_mode: "maker",
    data_status: "ok",
    splits: {
      oos_2022_plus: {
        cagr: 0.067,
        sharpe: 0.55,
        max_dd: -0.12,
        trades_per_mo: 3.2,
        win_rate: 0.52,
      },
    },
    oos_regimes: {
      downtrend: { days_pct: 0.4, contrib_cagr: 0.05, trades: 10, net_pnl_pct: 0.08 },
    },
    gates: {
      G1_OOS_SHARPE: "FAIL",
      G2_NON_UPTREND: "PASS",
      G3_DD_LIMIT: "PASS",
      G4_ADV1_SURVIVAL: "PASS",
      G5_CADENCE: "PASS",
    },
  },
  compact_report: "# funding_downtrend @ adv2_spot (maker)\nsummary",
};

const MOCK_SWEEP: BacktestSweepResult = {
  rows: [
    {
      strategy: "funding_downtrend",
      fee_tier: "adv2_spot",
      data_status: "ok",
      splits: { oos_2022_plus: { cagr: 0.067, sharpe: 0.55, max_dd: -0.12 } },
      oos_regimes: { downtrend: { net_pnl_pct: 0.08 } },
      gates: { G1_OOS_SHARPE: "FAIL" },
    },
  ],
  combined_markdown: "| strategy | tier |\n|funding_downtrend|adv2_spot|",
};

afterEach(() => {
  vi.restoreAllMocks();
});

describe("Backtest", () => {
  it("renders the empty state, textarea and run button on load", () => {
    renderBacktest();

    expect(screen.getByLabelText("Backtest parameters")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Run Backtest" })).toBeInTheDocument();
    expect(
      screen.getByText("Configure parameters and click Run Backtest to simulate."),
    ).toBeInTheDocument();
    expect(screen.queryByText("Net CAGR")).not.toBeInTheDocument();
  });

  it("shows an inline error on invalid JSON and does not call the API", async () => {
    const runMock = vi.spyOn(api, "runBacktest");
    renderBacktest();
    const textarea = screen.getByLabelText("Backtest parameters");

    await userEvent.clear(textarea);
    await userEvent.type(textarea, "not json");
    await userEvent.click(screen.getByRole("button", { name: "Run Backtest" }));

    expect(screen.getByText(/Unexpected token|JSON/)).toBeInTheDocument();
    expect(runMock).not.toHaveBeenCalled();
  });

  it("calls the API with parsed params and renders the results", async () => {
    const runMock = vi.spyOn(api, "runBacktest").mockResolvedValue(MOCK_RESULT);
    renderBacktest();
    const textarea = screen.getByLabelText("Backtest parameters");

    fireEvent.change(textarea, {
      target: {
        value:
          '{"strategy_sleeve":"trend_momentum","start_date":"2024-01-01","end_date":"2024-12-31"}',
      },
    });
    await userEvent.click(screen.getByRole("button", { name: "Run Backtest" }));

    expect(runMock).toHaveBeenCalledWith(
      {
        strategy_sleeve: "trend_momentum",
        start_date: "2024-01-01",
        end_date: "2024-12-31",
      },
      expect.anything(),
    );

    expect(await screen.findByText("Net CAGR")).toBeInTheDocument();
    expect(screen.getByText("18.4%")).toBeInTheDocument();
    expect(screen.getByText("benchmark 12.1%")).toBeInTheDocument();
    expect(screen.getByText("Max Drawdown")).toBeInTheDocument();
    expect(screen.getByText("win rate 61.0%")).toBeInTheDocument();
    expect(screen.getByText("$87.40")).toBeInTheDocument();

    expect(screen.getByText("Equity vs benchmark")).toBeInTheDocument();
    expect(screen.getByText("Drawdown")).toBeInTheDocument();

    expect(screen.getByText("Trade log")).toBeInTheDocument();
    expect(screen.getByText("Cost")).toBeInTheDocument();
    expect(screen.getByText("$5,136.00")).toBeInTheDocument();
    expect(screen.getByText("$121.00")).toBeInTheDocument();
    expect(screen.getByText("2024-01-15")).toBeInTheDocument();
    expect(screen.getByText("BTC-USD")).toBeInTheDocument();
    expect(screen.getByText("ETH-USD")).toBeInTheDocument();
  });

  it("renders the API error detail inline on failure", async () => {
    vi.spyOn(api, "runBacktest").mockRejectedValue(
      new ApiError(400, '{"detail":"vol_target must be positive"}'),
    );
    renderBacktest();

    await userEvent.click(screen.getByRole("button", { name: "Run Backtest" }));

    expect(await screen.findByText("vol_target must be positive")).toBeInTheDocument();
  });

  it("shows candidate selectors and sends candidate fields", async () => {
    const runMock = vi.spyOn(api, "runBacktest").mockResolvedValue(MOCK_COMPACT_RESULT);
    renderBacktest();

    // Pick a candidate sleeve from the strategy dropdown.
    fireEvent.change(screen.getByRole("combobox"), {
      target: { value: "funding_downtrend" },
    });

    // Candidate controls appear.
    expect(screen.getByText("Fee tier")).toBeInTheDocument();
    expect(screen.getByText("Execution")).toBeInTheDocument();
    expect(screen.getByText("Combine w/ Trend")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Run Backtest" }));

    const sent = runMock.mock.calls[0][0];
    expect(sent.strategy_sleeve).toBe("funding_downtrend");
    expect(sent.execution_mode).toBe("maker");

    // Compact scorecard + gate badges + copy button render.
    expect(await screen.findByText("Compact scorecard")).toBeInTheDocument();
    expect(screen.getByText("G1_OOS_SHARPE FAIL")).toBeInTheDocument();
    expect(screen.getByText("Copy LLM Report")).toBeInTheDocument();
  });

  it("copies the LLM report to the clipboard", async () => {
    vi.spyOn(api, "runBacktest").mockResolvedValue(MOCK_COMPACT_RESULT);
    const writeText = vi.fn().mockResolvedValue(undefined);
    vi.stubGlobal("navigator", { ...navigator, clipboard: { writeText } });
    renderBacktest();
    fireEvent.change(screen.getByRole("combobox"), {
      target: { value: "funding_downtrend" },
    });
    await userEvent.click(screen.getByRole("button", { name: "Run Backtest" }));
    await userEvent.click(
      await screen.findByRole("button", { name: "Copy LLM Report" }),
    );
    expect(writeText).toHaveBeenCalledWith(MOCK_COMPACT_RESULT.compact_report);
  });

  it("renders the sweep leaderboard and copies the full sweep", async () => {
    const sweepMock = vi
      .spyOn(api, "runBacktestSweep")
      .mockResolvedValue(MOCK_SWEEP);
    const writeText = vi.fn().mockResolvedValue(undefined);
    vi.stubGlobal("navigator", { ...navigator, clipboard: { writeText } });
    renderBacktest();

    await userEvent.click(
      screen.getByRole("button", { name: "Run Candidate Sweep" }),
    );

    expect(sweepMock).toHaveBeenCalled();
    expect(await screen.findByText("Candidate sweep")).toBeInTheDocument();
    expect(screen.getByText("funding_downtrend")).toBeInTheDocument();
    expect(screen.getByText("adv2_spot")).toBeInTheDocument();

    await userEvent.click(
      screen.getByRole("button", { name: "Copy Full Sweep for LLM" }),
    );
    expect(writeText).toHaveBeenCalledWith(MOCK_SWEEP.combined_markdown);
  });
});
