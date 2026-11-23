import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Positions } from "../pages/Positions";

vi.mock("../api/client", () => ({
  api: {
    positions: vi.fn(),
    openOrders: vi.fn(),
    funding: vi.fn(),
    plan: vi.fn(),
  },
}));

const { api } = await import("../api/client");
import { makeOpenOrder } from "./fixtures";

const positionsMock = vi.mocked(api.positions);
const openOrdersMock = vi.mocked(api.openOrders);
const fundingMock = vi.mocked(api.funding);
const planMock = vi.mocked(api.plan);

function renderPositions() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <Positions />
    </QueryClientProvider>,
  );
}

const POSITION = {
  asset: "BTC",
  symbol: "BTC-USD",
  quantity: "0.01",
  price: "60000",
  value_usd: "600",
  share: 0.06,
  avg_cost: "60180",
  unrealised_gain_usd: "-1.8",
  stop_price: "45000",
  plan_id: 5,
  resting_orders: [
    {
      id: 9,
      client_order_id: "5-2",
      side: "sell" as const,
      type: "limit" as const,
      price: "61000",
      stop_price: null,
      quantity: "0.01",
      status: "open",
      created_at: "2026-11-21T18:00:00Z",
    },
  ],
};

afterEach(() => vi.restoreAllMocks());

describe("Positions", () => {
  it("renders the positions table and funding watch line", async () => {
    positionsMock.mockResolvedValue([POSITION]);
    openOrdersMock.mockResolvedValue([]);
    fundingMock.mockResolvedValue({
      rates: [
        {
          symbol: "BTC-USD",
          venue: "coinbase_perp",
          time: new Date().toISOString(),
          rate: "0.0001",
          interval_hours: 8,
          annual_rate: "0.12",
        },
      ],
      t_bill_rate: 0.04,
    });
    renderPositions();

    expect(await screen.findByText("BTC")).toBeInTheDocument();
    expect(screen.getByText("$600")).toBeInTheDocument();
    expect(screen.getByText("6.0%")).toBeInTheDocument();
    expect(screen.getByText("$45,000")).toBeInTheDocument();
    expect(screen.getByText(/sell limit 0\.01 @ \$61,000/)).toBeInTheDocument();
    expect(await screen.findByText(/Funding watch:/)).toBeInTheDocument();
    expect(screen.getByText(/BTC-USD 12\.0% a year/)).toBeInTheDocument();
    expect(screen.getByText(/T-bill 4\.0%/)).toBeInTheDocument();
    // Spec G4: no carry-trade suggestion (tested and rejected).
    expect(screen.queryByText(/carry trade/)).not.toBeInTheDocument();
  });

  it("shows the empty state and no funding rows", async () => {
    positionsMock.mockResolvedValue([]);
    openOrdersMock.mockResolvedValue([]);
    fundingMock.mockResolvedValue({ rates: [], t_bill_rate: 0 });
    renderPositions();

    expect(await screen.findByText(/All cash/)).toBeInTheDocument();
    expect(await screen.findByText("No open orders.")).toBeInTheDocument();
    expect(await screen.findByText(/no funding rates recorded yet/)).toBeInTheDocument();
  });

  it("renders the open orders card even with no positions", async () => {
    positionsMock.mockResolvedValue([]);
    openOrdersMock.mockResolvedValue([makeOpenOrder()]);
    fundingMock.mockResolvedValue({ rates: [], t_bill_rate: 0 });
    renderPositions();

    expect(await screen.findByText("Open orders")).toBeInTheDocument();
    expect(screen.getByText("ETH-USD")).toBeInTheDocument();
    expect(screen.getByText("buy")).toBeInTheDocument();
    expect(screen.getByText("limit")).toBeInTheDocument();
    expect(screen.getByText("$2,900")).toBeInTheDocument();
    expect(screen.getByText("0.1")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "plan 5" })).toBeInTheDocument();
  });

  it("plan link opens the plan", async () => {
    positionsMock.mockResolvedValue([POSITION]);
    openOrdersMock.mockResolvedValue([]);
    fundingMock.mockResolvedValue({ rates: [], t_bill_rate: 0 });
    planMock.mockResolvedValue({
      id: 5,
      sleeve: "trend",
      symbol: "BTC-USD",
      side: "buy",
      status: "completed",
      reference_price: "60000",
      target_weight: "0.06",
      size_fraction: "1",
      entry_orders: [],
      stop_price: "45000",
      worst_case_loss: "10",
      est_fees: "2",
      rationale: "raise BTC-USD",
      expires_at: "2026-11-22T00:00:00Z",
      decided_at: null,
      decision_id: 1,
      created_at: "2026-11-21T00:00:00Z",
    });
    renderPositions();

    const link = await screen.findByRole("button", { name: "plan 5" });
    await userEvent.click(link);

    await waitFor(() => expect(planMock).toHaveBeenCalledWith(5));
    expect(await screen.findByTestId("plan-panel")).toBeInTheDocument();
    expect(screen.getByText(/raise BTC-USD/)).toBeInTheDocument();
  });
});
