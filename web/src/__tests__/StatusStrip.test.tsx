import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { StatusStrip } from "../components/StatusStrip";
import { makeStatus } from "./fixtures";

vi.mock("../api/client", () => ({
  api: { status: vi.fn() },
}));

const { api } = await import("../api/client");
const statusMock = vi.mocked(api.status);

function renderStrip() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <StatusStrip />
    </QueryClientProvider>,
  );
}

afterEach(() => vi.restoreAllMocks());

describe("StatusStrip", () => {
  it("shows Running, the last check and the next-decision countdown", async () => {
    statusMock.mockResolvedValue(makeStatus());
    renderStrip();
    expect(await screen.findByText("Running")).toBeInTheDocument();
    expect(screen.getByText(/Checked prices\. Nothing to do\./)).toBeInTheDocument();
    expect(screen.getByText(/Last check/)).toBeInTheDocument();
    expect(screen.getByText(/Next daily decision/)).toBeInTheDocument();
    expect(screen.getByText(/Last trade/)).toBeInTheDocument();
  });

  it("shows Halted with the halt reason", async () => {
    statusMock.mockResolvedValue(
      makeStatus({ halted: true, halt_reason: "drawdown 36% >= 35%" }),
    );
    renderStrip();
    expect(await screen.findByText("Halted")).toBeInTheDocument();
    expect(screen.getByText(/drawdown 36% >= 35%/)).toBeInTheDocument();
  });

  it("turns red and names the service with a heartbeat older than 5 minutes", async () => {
    statusMock.mockResolvedValue(
      makeStatus({
        services: [
          {
            name: "collector",
            status: "ok",
            last_heartbeat_at: new Date(Date.now() - 30 * 60_000).toISOString(),
          },
          {
            name: "engine",
            status: "ok",
            last_heartbeat_at: new Date(Date.now() - 5_000).toISOString(),
          },
        ],
      }),
    );
    renderStrip();
    expect(await screen.findByText("Problem")).toBeInTheDocument();
    expect(screen.getByText(/collector/)).toBeInTheDocument();
    expect(document.querySelector(".status-strip--problem")).not.toBeNull();
  });

  it("flags a service that keeps beating but reports an error", async () => {
    statusMock.mockResolvedValue(
      makeStatus({
        services: [
          {
            name: "executor",
            status: "error",
            last_heartbeat_at: new Date(Date.now() - 5_000).toISOString(),
          },
        ],
      }),
    );
    renderStrip();
    expect(await screen.findByText("Problem")).toBeInTheDocument();
    expect(screen.getByText(/executor/)).toBeInTheDocument();
  });

  it("tracks alarms by key: one clear leaves the other open", async () => {
    statusMock.mockResolvedValue(makeStatus());
    renderStrip();
    expect(await screen.findByText("Running")).toBeInTheDocument();
    const sources = (
      EventSource as unknown as {
        instances: { emit: (type: string, data: unknown) => void }[];
      }
    ).instances;
    const source = sources[sources.length - 1];
    act(() => {
      source.emit("alarm", { key: "candle_stale:BTC-USD", kind: "alarm", message: "BTC stale" });
      source.emit("alarm", { key: "intraday_stale", kind: "alarm", message: "No intraday" });
      source.emit("alarm", { key: "intraday_stale", kind: "alarm_cleared", message: "Cleared" });
    });
    expect(await screen.findByText("Problem")).toBeInTheDocument();
    expect(screen.getByText(/BTC stale/)).toBeInTheDocument();
    expect(screen.queryByText(/No intraday/)).toBeNull();
    act(() => {
      source.emit("alarm", { key: "candle_stale:BTC-USD", kind: "alarm_cleared", message: "x" });
    });
    expect(await screen.findByText("Running")).toBeInTheDocument();
  });

  it("keeps the last-check line visible in a problem state", async () => {
    statusMock.mockResolvedValue(
      makeStatus({ halted: true, halt_reason: "drawdown 36% >= 35%" }),
    );
    renderStrip();
    expect(await screen.findByText(/Last check/)).toBeInTheDocument();
    expect(screen.getByText(/Next daily decision/)).toBeInTheDocument();
  });
});
