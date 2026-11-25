import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, api } from "../api/client";
import type { BacktestResult } from "../api/client";
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
});
