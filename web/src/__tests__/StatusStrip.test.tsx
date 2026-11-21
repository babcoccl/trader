import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
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
});
