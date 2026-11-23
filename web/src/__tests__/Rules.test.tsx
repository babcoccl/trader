import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Rules } from "../pages/Rules";

vi.mock("../api/client", () => ({
  api: {
    rules: vi.fn(),
  },
}));

const { api } = await import("../api/client");
import { makeRulesOut } from "./fixtures";

const rulesMock = vi.mocked(api.rules);

function renderRules() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <Rules />
    </QueryClientProvider>,
  );
}

afterEach(() => vi.restoreAllMocks());

describe("Rules", () => {
  it("renders nine rule rows per asset, the observation line and settings", async () => {
    rulesMock.mockResolvedValue(makeRulesOut());
    const { container } = renderRules();

    expect(
      await screen.findByText("Trend rules (decided daily at 00:05 UTC)"),
    ).toBeInTheDocument();
    expect(screen.getByText("BTC-USD: 2 of 9 rules on")).toBeInTheDocument();
    expect(screen.getByText("ETH-USD: 2 of 9 rules on")).toBeInTheDocument();

    // Nine rows per asset, two assets.
    const rows = container.querySelectorAll("tbody tr");
    expect(rows).toHaveLength(20); // 18 rule rows + 2 settings rows
    expect(screen.getAllByText("On")).toHaveLength(4);
    expect(screen.getAllByText("Off")).toHaveLength(12);
    expect(screen.getAllByText("No data")).toHaveLength(2);
    expect(
      screen.getAllByText("Price above its 50-day average"),
    ).toHaveLength(2);

    expect(
      screen.getByText(
        "Observation only. The sudden-drop sleeve is parked and places no orders.",
      ),
    ).toBeInTheDocument();
    expect(
      screen.getAllByText(/Sudden-drop level 54,000 \(10.0% under the price\)/),
    ).toHaveLength(2);

    expect(screen.getByText("Fixed settings")).toBeInTheDocument();
    expect(screen.getByText("BTC-USD, ETH-USD")).toBeInTheDocument();
    expect(screen.getByText("40%")).toBeInTheDocument();
  });

  it("renders the empty state when no daily decision exists", async () => {
    const data = makeRulesOut();
    data.assets = data.assets.map((a) => ({ ...a, decided_at: null }));
    rulesMock.mockResolvedValue(data);
    renderRules();

    expect(
      await screen.findAllByText("No daily decision recorded yet."),
    ).toHaveLength(2);
  });

  it("warns when the latest daily run was blocked", async () => {
    const data = makeRulesOut();
    data.latest_daily = {
      ran_at: new Date().toISOString(),
      action: "blocked",
      reason: "Yesterday's BTC-USD candle is missing.",
    };
    rulesMock.mockResolvedValue(data);
    renderRules();

    expect(
      await screen.findByText(/Latest daily run blocked:/),
    ).toBeInTheDocument();
  });

  it("shows the regime text in the heading and a muted regime line", async () => {
    const data = makeRulesOut();
    data.assets = data.assets.map((a) => ({
      ...a,
      regime: {
        trend: "downtrend",
        volatility: "volatile",
        sideways: false,
        vol_percentile: 0.83,
        adx: 24.63,
        text: "Downtrend, volatile",
      },
    }));
    rulesMock.mockResolvedValue(data);
    renderRules();

    expect(
      await screen.findByText(
        "BTC-USD: 2 of 9 rules on. Downtrend, volatile",
      ),
    ).toBeInTheDocument();
    expect(
      screen.getAllByText(
        "Regime: ADX 24.6 (sideways reading under 20). Volatility is higher than on 83.0% of the last 365 days.",
      ),
    ).toHaveLength(2);
  });

  it("has no form controls", async () => {
    rulesMock.mockResolvedValue(makeRulesOut());
    const { container } = renderRules();
    await screen.findByText("Trend rules (decided daily at 00:05 UTC)");
    expect(
      container.querySelectorAll("input, select, textarea, button"),
    ).toHaveLength(0);
  });
});
