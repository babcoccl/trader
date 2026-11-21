import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Controls } from "../pages/Controls";
import { makeStatus } from "./fixtures";

vi.mock("../api/client", () => ({
  api: {
    status: vi.fn(),
    settings: vi.fn(),
    putSetting: vi.fn(),
    halt: vi.fn(),
    resume: vi.fn(),
    flatten: vi.fn(),
  },
}));

const { api } = await import("../api/client");
const statusMock = vi.mocked(api.status);
const settingsMock = vi.mocked(api.settings);
const putSettingMock = vi.mocked(api.putSetting);
const haltMock = vi.mocked(api.halt);
const resumeMock = vi.mocked(api.resume);
const flattenMock = vi.mocked(api.flatten);

function renderControls() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <Controls />
    </QueryClientProvider>,
  );
}

function defaultSettings() {
  return [
    { key: "approval_expiry_hours", value: 6, updated_at: null },
    { key: "autonomy", value: "approve_every_plan", updated_at: null },
    { key: "digest_time_utc", value: "00:30", updated_at: null },
    { key: "silence_alarm_minutes", value: 5, updated_at: null },
  ];
}

afterEach(() => vi.restoreAllMocks());

describe("Controls", () => {
  it("halts in one click and then shows Resume", async () => {
    statusMock.mockResolvedValue(makeStatus());
    settingsMock.mockResolvedValue(defaultSettings());
    haltMock.mockResolvedValue({ status: "ok" });
    renderControls();

    const haltButton = await screen.findByRole("button", {
      name: /Halt: cancel orders/,
    });
    // After halting, status says halted and the button becomes Resume.
    statusMock.mockResolvedValue(
      makeStatus({ halted: true, halt_reason: "Manual halt from the dashboard." }),
    );
    await userEvent.click(haltButton);
    expect(haltMock).toHaveBeenCalledTimes(1);
    expect(await screen.findByRole("button", { name: "Resume trading" }))
      .toBeInTheDocument();
    expect(screen.getByText("Halted")).toBeInTheDocument();

    resumeMock.mockResolvedValue({ status: "ok" });
    await userEvent.click(screen.getByRole("button", { name: "Resume trading" }));
    expect(resumeMock).toHaveBeenCalledTimes(1);
  });

  it("cannot flatten without the typed word FLATTEN", async () => {
    statusMock.mockResolvedValue(makeStatus());
    settingsMock.mockResolvedValue(defaultSettings());
    flattenMock.mockResolvedValue({ status: "ok" });
    renderControls();

    await userEvent.click(
      await screen.findByRole("button", { name: /Flatten: sell everything/ }),
    );
    const confirmButton = screen.getByRole("button", { name: "Flatten now" });
    expect(confirmButton).toBeDisabled();

    const input = screen.getByLabelText("Type FLATTEN to confirm");
    await userEvent.type(input, "flatten");
    expect(confirmButton).toBeDisabled();
    await userEvent.clear(input);
    await userEvent.type(input, "FLATTEN");
    expect(confirmButton).toBeEnabled();

    await userEvent.click(confirmButton);
    expect(flattenMock).toHaveBeenCalledWith("FLATTEN", expect.anything());
    expect(await screen.findByText(/Flatten requested/)).toBeInTheDocument();
  });

  it("edits a setting through PUT and shows the audited value", async () => {
    statusMock.mockResolvedValue(makeStatus());
    settingsMock.mockResolvedValue(defaultSettings());
    putSettingMock.mockResolvedValue({
      key: "silence_alarm_minutes",
      value: 10,
      updated_at: "2026-11-21T12:00:00Z",
    });
    renderControls();

    const row = (await screen.findByText("Silence alarm")).closest("tr")!;
    expect(row).toHaveTextContent("5 minutes");
    await userEvent.click(
      Array.from(row.querySelectorAll("button")).find((b) => b.textContent === "Edit")!,
    );

    const input = screen.getByLabelText("Silence alarm value");
    await userEvent.clear(input);
    await userEvent.type(input, "10");

    // The audited value comes back via a settings refetch after the PUT.
    settingsMock.mockResolvedValue(
      defaultSettings().map((s) =>
        s.key === "silence_alarm_minutes" ? { ...s, value: 10 } : s,
      ),
    );
    await userEvent.click(screen.getByRole("button", { name: "Save" }));

    expect(putSettingMock).toHaveBeenCalledWith("silence_alarm_minutes", 10);
    expect(await screen.findByText(/10 minutes/)).toBeInTheDocument();
  });

  it("shows read-only limits and the autonomy select has only approve-every-plan", async () => {
    statusMock.mockResolvedValue(makeStatus());
    settingsMock.mockResolvedValue(defaultSettings());
    renderControls();

    expect(await screen.findByText("Drawdown halt")).toBeInTheDocument();
    expect(screen.getByText("35% from peak")).toBeInTheDocument();
    expect(screen.getByText(/Volatility target 40%/)).toBeInTheDocument();
    expect(screen.getByText(/Bitstamp/)).toBeInTheDocument();
    expect(screen.getByText("paper")).toBeInTheDocument();

    const row = screen.getByText("Autonomy level").closest("tr")!;
    await userEvent.click(row.querySelector("button")!);
    const select = screen.getByLabelText("Autonomy level value");
    expect(select.querySelectorAll("option")).toHaveLength(1);
    expect(select).toHaveValue("approve_every_plan");
  });
});
