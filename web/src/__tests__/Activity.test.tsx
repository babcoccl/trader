import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Activity } from "../pages/Activity";

vi.mock("../api/client", () => ({
  api: { activity: vi.fn() },
}));

const { api } = await import("../api/client");
const activityMock = vi.mocked(api.activity);

function renderActivity() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <Activity />
    </QueryClientProvider>,
  );
}

const ITEMS = [
  {
    time: "2026-11-21T18:29:00Z",
    kind: "decision",
    ref_id: 61,
    text: "Checked prices. Nothing to do.",
    details: { cycle: "intraday", action: "none" },
  },
  {
    time: "2026-11-21T18:20:00Z",
    kind: "alarm",
    ref_id: 4,
    text: "collector silent",
    details: { severity: "warn" },
  },
];

afterEach(() => vi.restoreAllMocks());

describe("Activity", () => {
  it("renders newest-first items and expands a row to its raw record", async () => {
    activityMock.mockResolvedValue(ITEMS);
    renderActivity();

    expect(await screen.findByText(/Checked prices/)).toBeInTheDocument();
    expect(screen.getByText("collector silent")).toBeInTheDocument();

    await userEvent.click(screen.getByText(/Checked prices/));
    expect(await screen.findByText(/"action": "none"/)).toBeInTheDocument();
  });

  it("kind filter and search call the API with the right parameters", async () => {
    activityMock.mockResolvedValue(ITEMS);
    renderActivity();

    await waitFor(() =>
      expect(activityMock).toHaveBeenCalledWith(
        expect.objectContaining({ limit: 50 }),
      ),
    );

    await userEvent.selectOptions(
      screen.getByLabelText("kind filter"),
      "decision",
    );
    await waitFor(() =>
      expect(activityMock).toHaveBeenLastCalledWith(
        expect.objectContaining({ kind: "decision", q: undefined }),
      ),
    );

    await userEvent.type(screen.getByLabelText("search"), "stop");
    await waitFor(() =>
      expect(activityMock).toHaveBeenLastCalledWith(
        expect.objectContaining({ kind: "decision", q: "stop" }),
      ),
    );
  });

  it("load more raises the limit", async () => {
    const page = Array.from({ length: 50 }, (_, i) => ({
      time: "2026-11-21T18:29:00Z",
      kind: "heartbeat",
      ref_id: i + 1,
      text: `engine: ok ${i}`,
      details: {},
    }));
    activityMock.mockResolvedValue(page);
    renderActivity();

    await screen.findByText("engine: ok 0");
    await userEvent.click(screen.getByRole("button", { name: "load more" }));
    await waitFor(() =>
      expect(activityMock).toHaveBeenLastCalledWith(
        expect.objectContaining({ limit: 100 }),
      ),
    );
  });
});
