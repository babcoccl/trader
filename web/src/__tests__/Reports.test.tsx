import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Reports } from "../pages/Reports";

vi.mock("../api/client", () => ({
  api: {
    summary: vi.fn(),
    weeklyReports: vi.fn(),
    digests: vi.fn(),
  },
}));

const { api } = await import("../api/client");
import { makeWeeklyReport } from "./fixtures";

const summaryMock = vi.mocked(api.summary);
const weeklyMock = vi.mocked(api.weeklyReports);
const digestsMock = vi.mocked(api.digests);

function renderReports() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <Reports />
    </QueryClientProvider>,
  );
}

afterEach(() => vi.restoreAllMocks());

describe("Reports", () => {
  it("renders the four stat cards, scorecards and digests", async () => {
    summaryMock.mockResolvedValue({
      from_at: "2026-10-01T00:00:00Z",
      to_at: "2026-11-20T00:00:00Z",
      return_pct: 0.084,
      worst_drop_pct: 0.031,
      fees_usd: "46.2",
      vetoes: { plans: 3, shadow_fills: 5, result_usd: "-45.0" },
      paper_vs_backtest_gap_pp: -0.3,
    });
    weeklyMock.mockResolvedValue([
      makeWeeklyReport(),
      makeWeeklyReport({ week_start: "2026-11-02", week_end: "2026-11-08" }),
    ]);
    digestsMock.mockResolvedValue([
      {
        id: 7,
        day: "2026-11-20",
        body_md: "No trades today because every target was inside the band.",
        stats: {},
      },
    ]);
    renderReports();

    expect(await screen.findByText("Return since start")).toBeInTheDocument();
    expect(screen.getByText("+8.4%")).toBeInTheDocument();
    expect(screen.getByText("Worst drop so far")).toBeInTheDocument();
    expect(screen.getByText("-3.1%")).toBeInTheDocument();
    expect(screen.getByText("Fees paid")).toBeInTheDocument();
    expect(screen.getByText("$46")).toBeInTheDocument();
    expect(screen.getByText("Live vs backtest")).toBeInTheDocument();
    expect(screen.getByText("-0.3 points")).toBeInTheDocument();

    expect(screen.getByText("Scorecards")).toBeInTheDocument();
    expect(screen.getByText(/3 denied, expired or voided/)).toBeInTheDocument();
    expect(screen.getByText(/News view/)).toBeInTheDocument();
    expect(screen.getByText(/Promotion gate/)).toBeInTheDocument();

    expect(screen.getByText("Weekly paper reports")).toBeInTheDocument();
    expect(screen.getByText("Week of 2026-11-09 to 2026-11-15")).toBeInTheDocument();
    expect(screen.getByText("Week of 2026-11-02 to 2026-11-08")).toBeInTheDocument();

    expect(screen.getByText("Daily digests")).toBeInTheDocument();
    expect(screen.getByText("2026-11-20")).toBeInTheDocument();
  });

  it("shows placeholders when there is no history yet", async () => {
    summaryMock.mockResolvedValue({
      from_at: null,
      to_at: null,
      return_pct: null,
      worst_drop_pct: null,
      fees_usd: "0",
      vetoes: { plans: 0, shadow_fills: 0, result_usd: null },
      paper_vs_backtest_gap_pp: null,
    });
    weeklyMock.mockResolvedValue([]);
    digestsMock.mockResolvedValue([]);
    renderReports();

    expect(await screen.findByText("No denied, expired or voided plans yet."))
      .toBeInTheDocument();
    expect(screen.getByText(/No digests yet/)).toBeInTheDocument();
    expect(
      screen.getByText(/No weekly report yet; the first is written on the first Monday/),
    ).toBeInTheDocument();
  });
});
