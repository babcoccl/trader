import { useState } from "react";
import {
  Area,
  AreaChart,
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { pct, usd } from "../lib/time";

interface BacktestSummaryMetrics {
  cagr: number;
  benchmark_cagr: number;
  max_drawdown: number;
  total_trades: number;
  win_rate: number;
  total_fees_paid: number;
}

interface BacktestPoint {
  timestamp: string;
  equity: number;
  benchmark_equity: number;
  drawdown: number;
  asset_price: number;
}

interface BacktestTrade {
  date: string;
  asset: string;
  side: "BUY" | "SELL";
  size: number;
  price: number;
  pnl: number;
  fee: number;
}

interface BacktestResult {
  summary_metrics: BacktestSummaryMetrics;
  timeseries_data: BacktestPoint[];
  trade_log: BacktestTrade[];
}

const MOCK_BACKTEST_RESULT: BacktestResult = {
  summary_metrics: {
    cagr: 0.184,
    benchmark_cagr: 0.121,
    max_drawdown: 0.147,
    total_trades: 23,
    win_rate: 0.61,
    total_fees_paid: 87.4,
  },
  timeseries_data: [
    { timestamp: "2024-01-01", equity: 10000, benchmark_equity: 10000, drawdown: 0, asset_price: 42300 },
    { timestamp: "2024-02-01", equity: 10420, benchmark_equity: 10250, drawdown: 0, asset_price: 43100 },
    { timestamp: "2024-03-01", equity: 11080, benchmark_equity: 10640, drawdown: 0, asset_price: 45200 },
    { timestamp: "2024-04-01", equity: 10630, benchmark_equity: 10490, drawdown: 0.041, asset_price: 43800 },
    { timestamp: "2024-05-01", equity: 10970, benchmark_equity: 10720, drawdown: 0.01, asset_price: 44500 },
    { timestamp: "2024-06-01", equity: 10220, benchmark_equity: 10180, drawdown: 0.077, asset_price: 41900 },
    { timestamp: "2024-07-01", equity: 9580, benchmark_equity: 9730, drawdown: 0.135, asset_price: 39600 },
    { timestamp: "2024-08-01", equity: 9670, benchmark_equity: 9820, drawdown: 0.147, asset_price: 40100 },
    { timestamp: "2024-09-01", equity: 10290, benchmark_equity: 10350, drawdown: 0.071, asset_price: 42600 },
    { timestamp: "2024-10-01", equity: 11150, benchmark_equity: 10940, drawdown: 0, asset_price: 44900 },
    { timestamp: "2024-11-01", equity: 11730, benchmark_equity: 11380, drawdown: 0, asset_price: 47100 },
    { timestamp: "2024-12-01", equity: 11840, benchmark_equity: 11210, drawdown: 0, asset_price: 46800 },
  ],
  trade_log: [
    { date: "2024-01-15", asset: "BTC-USD", side: "BUY", size: 0.12, price: 42800, pnl: 0, fee: 5.14 },
    { date: "2024-03-08", asset: "ETH-USD", side: "BUY", size: 1.4, price: 2420, pnl: 0, fee: 3.39 },
    { date: "2024-06-20", asset: "BTC-USD", side: "SELL", size: 0.05, price: 43500, pnl: 82.5, fee: 2.18 },
    { date: "2024-09-02", asset: "ETH-USD", side: "SELL", size: 0.8, price: 2610, pnl: 152.0, fee: 2.09 },
    { date: "2024-11-12", asset: "BTC-USD", side: "SELL", size: 0.04, price: 46900, pnl: 196.4, fee: 1.88 },
  ],
};

const DEFAULT_PARAMS_JSON = `{
  "strategy_sleeve": "trend_momentum",
  "asset_universe": ["BTC-USD", "ETH-USD"],
  "start_date": "2024-01-01",
  "end_date": "2024-12-31",
  "starting_capital": 10000,
  "assumed_fee_tier": "tier_1_maker",
  "vol_target": 0.40,
  "drawdown_limit": 0.35
}`;

function StatCard({ title, value, sub }: { title: string; value: string; sub?: string }) {
  return (
    <div className="card stat-card">
      <div className="stat-card__title">{title}</div>
      <div className="stat-card__value">{value}</div>
      {sub ? <div className="stat-card__sub">{sub}</div> : null}
    </div>
  );
}

export function Backtest() {
  const [params, setParams] = useState(DEFAULT_PARAMS_JSON);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<BacktestResult>(MOCK_BACKTEST_RESULT);

  const runBacktest = () => {
    try {
      JSON.parse(params);
      setError(null);
      setResult({ ...MOCK_BACKTEST_RESULT });
    } catch (e) {
      setError(e instanceof Error ? e.message : "Invalid JSON");
    }
  };

  const s = result.summary_metrics;
  const chartData = result.timeseries_data.map((p) => ({
    ...p,
    drawdown_pct: p.drawdown * 100,
  }));

  return (
    <div className="page">
      <div className="backtest-layout">
        <div className="card backtest-layout__params">
          <div className="card__title">Parameters</div>
          <textarea
            className="backtest-params"
            aria-label="Backtest parameters"
            rows={18}
            value={params}
            onChange={(e) => setParams(e.target.value)}
          />
          <button onClick={runBacktest}>Run Backtest</button>
          {error ? <p className="error">{error}</p> : null}
        </div>

        <div className="backtest-layout__results">
          <div className="stat-grid">
            <StatCard
              title="Net CAGR"
              value={pct(s.cagr)}
              sub={`benchmark ${pct(s.benchmark_cagr)}`}
            />
            <StatCard title="Max Drawdown" value={pct(s.max_drawdown)} />
            <StatCard
              title="Total Trades"
              value={String(s.total_trades)}
              sub={`win rate ${pct(s.win_rate)}`}
            />
            <StatCard title="Total Fees Paid" value={usd(s.total_fees_paid)} />
          </div>

          <div className="card">
            <div className="card__title">Equity vs benchmark</div>
            <ResponsiveContainer width="100%" height={280}>
              <LineChart data={chartData}>
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis dataKey="timestamp" fontSize={11} />
                <YAxis fontSize={11} domain={["auto", "auto"]} />
                <Tooltip />
                <Line
                  type="monotone"
                  dataKey="equity"
                  name="Strategy equity"
                  stroke="#191919"
                  dot={false}
                />
                <Line
                  type="monotone"
                  dataKey="benchmark_equity"
                  name="Benchmark equity"
                  stroke="#2563eb"
                  strokeDasharray="5 5"
                  dot={false}
                />
              </LineChart>
            </ResponsiveContainer>
          </div>

          <div className="card">
            <div className="card__title">Drawdown</div>
            <ResponsiveContainer width="100%" height={180}>
              <AreaChart data={chartData}>
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis dataKey="timestamp" fontSize={11} />
                <YAxis fontSize={11} unit="%" />
                <Tooltip />
                <Area
                  type="monotone"
                  dataKey="drawdown_pct"
                  name="Drawdown"
                  stroke="#f53b3a"
                  fill="rgb(245 59 58 / 0.15)"
                />
              </AreaChart>
            </ResponsiveContainer>
          </div>

          <div className="card">
            <div className="card__title">Trade log</div>
            <div className="table-scroll">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Date</th>
                    <th>Asset</th>
                    <th>Side</th>
                    <th>Size</th>
                    <th>Price</th>
                    <th>PnL</th>
                    <th>Fee</th>
                  </tr>
                </thead>
                <tbody>
                  {result.trade_log.map((t, i) => (
                    <tr key={i}>
                      <td>{t.date}</td>
                      <td>{t.asset}</td>
                      <td>{t.side}</td>
                      <td>{t.size}</td>
                      <td>{usd(t.price)}</td>
                      <td>{usd(t.pnl)}</td>
                      <td>${t.fee.toFixed(2)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
