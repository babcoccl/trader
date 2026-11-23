import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Shadow } from "../pages/Shadow";

vi.mock("../api/client", () => ({
  api: {
    shadow: vi.fn(),
  },
}));

const { api } = await import("../api/client");
import { makeShadowOut } from "./fixtures";

const shadowMock = vi.mocked(api.shadow);

function renderShadow() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <Shadow />
    </QueryClientProvider>,
  );
}

afterEach(() => vi.restoreAllMocks());

describe("Shadow", () => {
  it("shows the notice, a sleeve card and both trade tables", async () => {
    shadowMock.mockResolvedValue(makeShadowOut());
    renderShadow();

    expect(
      await screen.findByText(
        "Hypothetical results. These sleeves place no orders and do not use the account.",
      ),
    ).toBeInTheDocument();
    expect(screen.getByText("Dip-buying in a mixed regime")).toBeInTheDocument();
    expect(
      screen.getByText(/Since 2026-11-20 \(3 days\): -1.0% on a \$10,000 book/),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/Trading account over the same days: \+2.0%\./),
    ).toBeInTheDocument();
    expect(
      screen.getByText("1 closed trades, 1 with a gain."),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        "2026-11-22: BTC: holding since 2026-11-20, sells on 2026-11-25.",
      ),
    ).toBeInTheDocument();

    // Open trade and recently closed trade.
    expect(screen.getByText("BTC-USD")).toBeInTheDocument();
    expect(screen.getByText("ETH-USD")).toBeInTheDocument();
    expect(screen.getByText("held 5 days")).toBeInTheDocument();
  });

  it("shows the empty state when a sleeve has no mark", async () => {
    const data = makeShadowOut();
    data.sleeves[0] = {
      ...data.sleeves[0],
      started_on: null,
      days: 0,
      equity_usd: null,
      return_fraction: null,
      worst_drawdown: null,
      account_return_fraction: null,
      closed_trades: 0,
      winning_trades: 0,
      fees_usd: "0",
      last_note: null,
      last_day: null,
      open: [],
      recent_closed: [],
    };
    shadowMock.mockResolvedValue(data);
    renderShadow();

    expect(
      await screen.findByText(
        "No record yet. The first entry appears after the next daily decision.",
      ),
    ).toBeInTheDocument();
  });

  it("has no form controls", async () => {
    shadowMock.mockResolvedValue(makeShadowOut());
    const { container } = renderShadow();
    await screen.findByText("Dip-buying in a mixed regime");
    expect(
      container.querySelectorAll("input, select, textarea, button"),
    ).toHaveLength(0);
  });
});
