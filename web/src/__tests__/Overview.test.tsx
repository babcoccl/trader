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
  },
}));

const { api } = await import("../api/client");
const statusMock = vi.mocked(api.status);
const decisionsMock = vi.mocked(api.decisions);
const equityMock = vi.mocked(api.equityReport);

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
    decisionsMock.mockResolvedValue([
      makeDecision({ id: 2, cycle: "daily", reason: "Checked BTC-USD. 5 of 9 rules positive." }),
      makeDecision({ id: 1, cycle: "intraday", reason: "Checked prices. Nothing to do." }),
    ]);
    equityMock.mockResolvedValue([
      { time: "2026-11-21T00:00:00Z", account: "10420.5", cash: "10420.5", btc: "10300", btc_eth: "10350" },
    ]);
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

    expect(screen.getByText("What the system thinks right now")).toBeInTheDocument();
    expect(screen.getAllByText(/5 of 9 rules positive/).length).toBeGreaterThan(0);
    expect(screen.getByText("Why nothing has happened lately")).toBeInTheDocument();
  });
});
