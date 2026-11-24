import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { Backtest } from "../pages/Backtest";

describe("Backtest", () => {
  it("renders the parameter textarea, run button, KPI cards and trade log", () => {
    render(<Backtest />);

    expect(screen.getByLabelText("Backtest parameters")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Run Backtest" })).toBeInTheDocument();

    expect(screen.getByText("Net CAGR")).toBeInTheDocument();
    expect(screen.getByText("18.4%")).toBeInTheDocument();
    expect(screen.getByText("benchmark 12.1%")).toBeInTheDocument();
    expect(screen.getByText("Max Drawdown")).toBeInTheDocument();
    expect(screen.getByText("Total Trades")).toBeInTheDocument();
    expect(screen.getByText("win rate 61.0%")).toBeInTheDocument();
    expect(screen.getByText("Total Fees Paid")).toBeInTheDocument();

    expect(screen.getByText("Trade log")).toBeInTheDocument();
    for (const col of ["Date", "Asset", "Side", "Size", "Price", "PnL", "Fee"]) {
      expect(screen.getByText(col)).toBeInTheDocument();
    }
    expect(screen.getByText("2024-01-15")).toBeInTheDocument();
    expect(screen.getAllByText("BTC-USD").length).toBeGreaterThan(0);
  });

  it("shows an inline error when the parameters are not valid JSON", async () => {
    render(<Backtest />);
    const textarea = screen.getByLabelText("Backtest parameters");

    await userEvent.clear(textarea);
    await userEvent.type(textarea, "not json");
    await userEvent.click(screen.getByRole("button", { name: "Run Backtest" }));

    expect(screen.getByText(/Unexpected token|JSON/)).toBeInTheDocument();
  });
});
