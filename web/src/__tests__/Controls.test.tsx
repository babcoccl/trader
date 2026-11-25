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
    { key: "subsidy_enabled", value: false, updated_at: null },
    { key: "subsidy_target_volume_usd", value: 10000, updated_at: null },
    { key: "subsidy_daily_cap_usd", value: 2000, updated_at: null },
    { key: "subsidy_adx_max", value: 20, updated_at: null },
    { key: "subsidy_order_notional_usd", value: 250, updated_at: null },
    { key: "subsidy_order_levels", value: 3, updated_at: null },
    { key: "subsidy_spread_bps", value: 8.5, updated_at: null },
    { key: "subsidy_refresh_seconds", value: 15, updated_at: null },
    { key: "subsidy_auto_seed", value: false, updated_at: null },
    { key: "auto_approve_enabled", value: false, updated_at: null },
    { key: "auto_approve_max_notional_usd", value: 750, updated_at: null },
    { key: "auto_approve_max_loss_pct", value: 0.015, updated_at: null },
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

    // Daily digest, the other editable row, saves the same way.
    putSettingMock.mockResolvedValue({
      key: "digest_time_utc",
      value: "06:15",
      updated_at: "2026-11-21T12:05:00Z",
    });
    const digestRow = screen.getByText("Daily digest").closest("tr")!;
    await userEvent.click(
      Array.from(digestRow.querySelectorAll("button")).find(
        (b) => b.textContent === "Edit",
      )!,
    );
    const digestInput = screen.getByLabelText("Daily digest value");
    await userEvent.clear(digestInput);
    await userEvent.type(digestInput, "06:15");
    settingsMock.mockResolvedValue(
      defaultSettings().map((s) =>
        s.key === "digest_time_utc" ? { ...s, value: "06:15" } : s,
      ),
    );
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(putSettingMock).toHaveBeenCalledWith("digest_time_utc", "06:15");
    expect(await screen.findByText(/06:15 UTC/)).toBeInTheDocument();
  });

  it("renders subsidy and auto-approve controls and saves them", async () => {
    statusMock.mockResolvedValue(makeStatus());
    settingsMock.mockResolvedValue(defaultSettings());
    putSettingMock.mockResolvedValue({
      key: "subsidy_adx_max",
      value: 25,
      updated_at: "2026-11-21T12:00:00Z",
    });
    renderControls();

    // Labels render with formatted values.
    expect(await screen.findByText("Fee-tier subsidy")).toBeInTheDocument();
    expect(screen.getByText("Subsidy target volume")).toBeInTheDocument();
    expect(screen.getByText("Subsidy daily cap")).toBeInTheDocument();
    expect(screen.getByText("Subsidy max ADX")).toBeInTheDocument();
    expect(screen.getByText("Subsidy order size")).toBeInTheDocument();
    expect(screen.getByText("Auto-approve small plans")).toBeInTheDocument();
    expect(screen.getByText("Auto-approve max notional")).toBeInTheDocument();
    const lossRow = screen.getByText("Auto-approve max loss").closest("tr")!;
    expect(lossRow).toHaveTextContent("1.5%");

    // Number input: subsidy max ADX saves as a number.
    const adxRow = screen.getByText("Subsidy max ADX").closest("tr")!;
    await userEvent.click(
      Array.from(adxRow.querySelectorAll("button")).find((b) => b.textContent === "Edit")!,
    );
    const adxInput = screen.getByLabelText("Subsidy max ADX value");
    await userEvent.clear(adxInput);
    await userEvent.type(adxInput, "25");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(putSettingMock).toHaveBeenCalledWith("subsidy_adx_max", 25);

    // Boolean select: fee-tier subsidy saves true/false, not a string.
    putSettingMock.mockResolvedValue({
      key: "subsidy_enabled",
      value: true,
      updated_at: "2026-11-21T12:01:00Z",
    });
    const toggleRow = screen.getByText("Fee-tier subsidy").closest("tr")!;
    await userEvent.click(
      Array.from(toggleRow.querySelectorAll("button")).find(
        (b) => b.textContent === "Edit",
      )!,
    );
    const select = screen.getByLabelText("Fee-tier subsidy value");
    await userEvent.selectOptions(select, "true");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(putSettingMock).toHaveBeenCalledWith("subsidy_enabled", true);

    // Max loss input works in percent but saves the fraction.
    putSettingMock.mockResolvedValue({
      key: "auto_approve_max_loss_pct",
      value: 0.02,
      updated_at: "2026-11-21T12:02:00Z",
    });
    await userEvent.click(
      Array.from(lossRow.querySelectorAll("button")).find((b) => b.textContent === "Edit")!,
    );
    const lossInput = screen.getByLabelText("Auto-approve max loss value");
    expect(lossInput).toHaveValue(1.5);
    await userEvent.clear(lossInput);
    await userEvent.type(lossInput, "2");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(putSettingMock).toHaveBeenCalledWith("auto_approve_max_loss_pct", 0.02);

    // Ping-pong ladder settings render with formatted values.
    const levelsRow = screen.getByText("Ping-pong order levels").closest("tr")!;
    expect(levelsRow).toHaveTextContent("3 per side");
    const spreadRow = screen.getByText("Ping-pong spread").closest("tr")!;
    expect(spreadRow).toHaveTextContent("8.5 bps");
    const refreshRow = screen.getByText("Ping-pong refresh").closest("tr")!;
    expect(refreshRow).toHaveTextContent("15s");

    // Order levels saves as a parsed integer.
    putSettingMock.mockResolvedValue({
      key: "subsidy_order_levels",
      value: 4,
      updated_at: "2026-11-21T12:03:00Z",
    });
    await userEvent.click(
      Array.from(levelsRow.querySelectorAll("button")).find(
        (b) => b.textContent === "Edit",
      )!,
    );
    const levelsInput = screen.getByLabelText("Ping-pong order levels value");
    await userEvent.clear(levelsInput);
    await userEvent.type(levelsInput, "4");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(putSettingMock).toHaveBeenCalledWith("subsidy_order_levels", 4);

    // Spread saves as a parsed float.
    putSettingMock.mockResolvedValue({
      key: "subsidy_spread_bps",
      value: 12.5,
      updated_at: "2026-11-21T12:04:00Z",
    });
    await userEvent.click(
      Array.from(spreadRow.querySelectorAll("button")).find(
        (b) => b.textContent === "Edit",
      )!,
    );
    const spreadInput = screen.getByLabelText("Ping-pong spread value");
    await userEvent.clear(spreadInput);
    await userEvent.type(spreadInput, "12.5");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(putSettingMock).toHaveBeenCalledWith("subsidy_spread_bps", 12.5);

    // Refresh saves as a parsed integer.
    putSettingMock.mockResolvedValue({
      key: "subsidy_refresh_seconds",
      value: 30,
      updated_at: "2026-11-21T12:05:00Z",
    });
    await userEvent.click(
      Array.from(refreshRow.querySelectorAll("button")).find(
        (b) => b.textContent === "Edit",
      )!,
    );
    const refreshInput = screen.getByLabelText("Ping-pong refresh value");
    await userEvent.clear(refreshInput);
    await userEvent.type(refreshInput, "30");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(putSettingMock).toHaveBeenCalledWith("subsidy_refresh_seconds", 30);
  });

  it("renders Ping-pong auto-seed and saves it as a boolean", async () => {
    statusMock.mockResolvedValue(makeStatus());
    settingsMock.mockResolvedValue(defaultSettings());
    putSettingMock.mockResolvedValue({
      key: "subsidy_auto_seed",
      value: true,
      updated_at: "2026-11-21T12:06:00Z",
    });
    renderControls();

    const row = (await screen.findByText("Ping-pong auto-seed")).closest("tr")!;
    expect(row).toHaveTextContent("Disabled");
    await userEvent.click(
      Array.from(row.querySelectorAll("button")).find((b) => b.textContent === "Edit")!,
    );
    const select = screen.getByLabelText("Ping-pong auto-seed value");
    await userEvent.selectOptions(select, "true");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(putSettingMock).toHaveBeenCalledWith("subsidy_auto_seed", true);
  });

  it("shows read-only limits; autonomy and approval expiry have no edit button", async () => {
    statusMock.mockResolvedValue(makeStatus());
    settingsMock.mockResolvedValue(defaultSettings());
    renderControls();

    expect(await screen.findByText("Drawdown halt")).toBeInTheDocument();
    expect(screen.getByText("35% from peak")).toBeInTheDocument();
    expect(
      screen.getByText(
        /Volatility target 40%, assets set by TRADER_SYMBOLS \(default BTC and ETH\), long only, no leverage/
      )
    ).toBeInTheDocument();
    expect(screen.getByText(/Bitstamp/)).toBeInTheDocument();
    expect(screen.getByText("paper")).toBeInTheDocument();

    const autonomyRow = screen.getByText("Autonomy level").closest("tr")!;
    expect(autonomyRow).toHaveTextContent(
      "Ping-pong subsidy plans and small bounded plans execute autonomously when enabled below; every other plan needs your approval",
    );
    expect(autonomyRow.querySelector("button")).toBeNull();

    const expiryRow = screen.getByText("Approval expiry").closest("tr")!;
    expect(expiryRow).toHaveTextContent("6 hours (fixed)");
    expect(expiryRow.querySelector("button")).toBeNull();
  });
});
