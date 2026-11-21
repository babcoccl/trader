import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { PlanOut } from "../api/client";
import { Approvals } from "../pages/Approvals";
import { makeStatus } from "./fixtures";

vi.mock("../api/client", () => ({
  api: {
    status: vi.fn(),
    plans: vi.fn(),
    approvePlan: vi.fn(),
    denyPlan: vi.fn(),
    activity: vi.fn(),
    positions: vi.fn(),
  },
}));

const { api } = await import("../api/client");
const statusMock = vi.mocked(api.status);
const plansMock = vi.mocked(api.plans);
const approveMock = vi.mocked(api.approvePlan);
const denyMock = vi.mocked(api.denyPlan);
const activityMock = vi.mocked(api.activity);
const positionsMock = vi.mocked(api.positions);

function makePlan(overrides: Partial<PlanOut> = {}): PlanOut {
  return {
    id: 42,
    sleeve: "trend",
    symbol: "ETH-USD",
    side: "buy",
    status: "pending",
    reference_price: "1785",
    target_weight: "0.24",
    size_fraction: "1",
    entry_orders: [
      {
        side: "buy",
        type: "limit",
        price: "1785",
        stop_price: null,
        quantity: "0.728",
        market_after_seconds: 3600,
        maker_or_cancel: true,
        reprice_seconds: 300,
      },
    ],
    stop_price: "1340",
    worst_case_loss: "335",
    est_fees: "9",
    rationale: "I would like to raise ETH-USD from 12% to 24% of the account.",
    expires_at: new Date(Date.now() + 5 * 3600_000).toISOString(),
    decided_at: null,
    decision_id: 7,
    created_at: new Date().toISOString(),
    ...overrides,
  };
}

function renderApprovals() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <Approvals />
    </QueryClientProvider>,
  );
}

function seed(plan: PlanOut) {
  statusMock.mockResolvedValue(makeStatus());
  plansMock.mockImplementation((status) =>
    Promise.resolve(status === "pending" ? (plan.status === "pending" ? [plan] : []) : [plan]),
  );
  activityMock.mockResolvedValue([]);
  positionsMock.mockResolvedValue([]);
  approveMock.mockResolvedValue({ ...plan, status: "approved" });
  denyMock.mockResolvedValue({ ...plan, status: "denied" });
}

describe("Approvals", () => {
  it("approve at half sends size_fraction 0.5 and rescales the figures", async () => {
    const user = userEvent.setup();
    seed(makePlan());
    renderApprovals();

    await screen.findByText(/Plan #42/);
    await user.click(screen.getByRole("button", { name: "Half" }));
    // 0.728 * 1785 * 0.5 ≈ $650; worst case 335 * 0.5 ≈ $168.
    expect(screen.getByText(/-\$16[78]/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Approve" }));
    expect(approveMock).toHaveBeenCalledWith(42, 0.5);
  });

  it("approves at full size by default and denies", async () => {
    const user = userEvent.setup();
    seed(makePlan());
    renderApprovals();
    await screen.findByText(/Plan #42/);
    await user.click(screen.getByRole("button", { name: "Approve" }));
    expect(approveMock).toHaveBeenCalledWith(42, 1);
    await user.click(screen.getByRole("button", { name: "Deny" }));
    expect(denyMock).toHaveBeenCalledWith(42);
  });

  it("disables approve and deny once the plan has expired", async () => {
    seed(makePlan({ expires_at: new Date(Date.now() - 60_000).toISOString() }));
    renderApprovals();
    await screen.findByText("Expired");
    expect(screen.getByRole("button", { name: "Approve" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Deny" })).toBeDisabled();
  });

  it("offers no size above Full", async () => {
    seed(makePlan());
    renderApprovals();
    const group = await screen.findByRole("group", { name: "Plan size" });
    const labels = within(group)
      .getAllByRole("button")
      .map((b) => b.textContent);
    expect(labels).toEqual(["Full", "Half", "Quarter"]);
  });

  it("shows shadow-order results for dead plans in past decisions", async () => {
    seed(makePlan({ id: 40, status: "denied", decided_at: new Date().toISOString() }));
    activityMock.mockResolvedValue([
      {
        time: new Date().toISOString(),
        kind: "fill",
        ref_id: 9,
        text: "Buy fill",
        details: {
          plan_id: 40,
          shadow: true,
          symbol: "ETH-USD",
          side: "buy",
          price: "1000",
          quantity: "1",
          fee: "3",
          fee_currency: "USD",
          liquidity: "taker",
        },
      },
    ]);
    positionsMock.mockResolvedValue([
      {
        asset: "ETH",
        symbol: "ETH-USD",
        quantity: "0",
        price: "1100",
        value_usd: "0",
        share: 0,
        avg_cost: null,
        unrealised_gain_usd: null,
        stop_price: null,
        plan_id: null,
        resting_orders: [],
      },
    ]);
    renderApprovals();
    // Would-have-been: -1000 - 3 + 1 * 1100 = +97; owner missed a winner.
    expect(await screen.findByText("+$97")).toBeInTheDocument();
    expect(screen.getByText("$0 (missed)")).toBeInTheDocument();
  });

  it("marks held shadow units at the fill price when the asset is unheld", async () => {
    seed(makePlan({ id: 41, status: "denied", decided_at: new Date().toISOString() }));
    activityMock.mockResolvedValue([
      {
        time: new Date().toISOString(),
        kind: "fill",
        ref_id: 10,
        text: "Buy fill",
        details: {
          plan_id: 41,
          shadow: true,
          symbol: "BTC-USD",
          side: "buy",
          price: "1000",
          quantity: "1",
          fee: "3",
          fee_currency: "USD",
          liquidity: "taker",
        },
      },
    ]);
    positionsMock.mockResolvedValue([]);
    renderApprovals();
    // No mark for BTC-USD: held 1 unit valued at its fill price -> -$3 fee.
    expect(await screen.findByText("-$3")).toBeInTheDocument();
    expect(screen.getByText("$0 (veto helped)")).toBeInTheDocument();
  });
});
