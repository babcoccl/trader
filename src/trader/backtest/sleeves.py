"""Pure weight-computation sleeves for the backtest engine."""

from typing import Any

import pandas as pd

from trader.strategy import ensemble, regime, rules, sizing

BTC = "BTC-USD"


def cross_sectional_momentum_weights(
    closes: pd.DataFrame,
    vol_target: float = 0.70,
    lookback_days: int = 30,
    top_k: int = 3,
    btc_gate: bool = True,
) -> pd.DataFrame:
    """Top-k risk-adjusted momentum weights per day.

    Score = ``close / close.shift(lookback_days) - 1`` divided by annualized
    vol. Assets with NaN score or non-positive momentum are ineligible. When
    ``btc_gate`` is set and BTC is in the frame, every weight is 0 on days
    BTC trades at or below its 50-day SMA. Selected assets share weight
    equally, scaled by ``min(1, vol_target / vol)``, gross capped at 1.
    """
    if lookback_days <= 0:
        raise ValueError(f"lookback_days must be positive, got {lookback_days}")
    symbols = list(closes.columns)
    rets = closes / closes.shift(lookback_days) - 1.0
    vols = pd.DataFrame(
        {s: sizing.annual_vol(closes[s]) for s in symbols}, index=closes.index
    )
    scores = rets / vols
    scores = scores.where((rets > 0) & scores.notna())

    if btc_gate and BTC in closes.columns:
        btc = closes[BTC]
        gate_ok = btc > btc.rolling(50).mean()
    else:
        gate_ok = pd.Series(True, index=closes.index)

    w = pd.DataFrame(0.0, index=closes.index, columns=symbols)
    for day in closes.index:
        if not gate_ok[day]:
            continue
        row = scores.loc[day].dropna()
        selected = row.nlargest(top_k)
        if selected.empty:
            continue
        share = 1.0 / len(selected)
        for s in selected.index:
            vol = vols.loc[day, s]
            scale = 1.0 if vol == 0 else min(1.0, vol_target / vol)
            w.loc[day, s] = share * scale
        gross = w.loc[day].sum()
        if gross > 1.0:
            w.loc[day] /= gross
    return w


def rules_on(
    close: pd.Series, sma_periods: tuple[int, ...] = (50, 100, 200)
) -> pd.Series:
    """Count of the nine trend rules that are on, per day (0-9)."""
    r = {f"sma{n}": rules.sma(close, n) for n in sma_periods}
    r.update({f"momentum{n}": rules.momentum(close, n) for n in rules.MOMENTUM_PERIODS})
    r.update({f"breakout{n}": rules.breakout(close, n) for n in rules.BREAKOUT_PERIODS})
    return sum((v.fillna(0.0) for v in r.values()), pd.Series(0.0, index=close.index))


def funding_downtrend_weights(
    closes: pd.DataFrame,
    funding: pd.DataFrame,
    max_rules: int = 2,
    funding_window_days: int = 7,
    funding_threshold: float = 0.0,
    funding_zscore: float | None = None,
    max_weight: float = 0.25,
    assets: list[str] | None = None,
) -> pd.DataFrame:
    """Hold ``max_weight`` per asset in BTC/ETH downtrends with soft funding.

    An asset is held on day D when the primary asset's rule count (day D,
    applied at D+1 open by the engine) is at most ``max_rules`` and that
    asset's rolling mean daily funding is below ``funding_threshold`` — or,
    when ``funding_zscore`` is set, when the funding z-score over
    ``funding_window_days`` is below it.
    """
    symbols = assets or [c.split("-")[0] for c in closes.columns]
    cols = [f"{a}-USD" for a in symbols if f"{a}-USD" in closes.columns]
    gate = (
        rules_on(closes[cols[0]]) <= max_rules
        if cols
        else pd.Series(False, index=closes.index)
    )
    w = pd.DataFrame(0.0, index=closes.index, columns=closes.columns)
    for c in cols:
        if c not in funding.columns:
            continue
        roll = funding[c].rolling(funding_window_days, min_periods=1).mean()
        if funding_zscore is not None:
            std = funding[c].rolling(funding_window_days, min_periods=2).std()
            mean = funding[c].rolling(funding_window_days).mean()
            ok = ((roll - mean) / std) < funding_zscore
        else:
            ok = roll < funding_threshold
        w.loc[gate & ok.reindex(closes.index).fillna(False), c] = max_weight
    return w


def _dip_state(close: pd.Series, dip_z: float, dip_hold_days: int) -> pd.Series:
    """1.0 for dip_hold_days after a Z-score < -dip_z dip, else 0.0."""
    z = (close - close.rolling(20).mean()) / close.rolling(20).std()
    state = pd.Series(0.0, index=close.index)
    hold = 0
    for i, zi in enumerate(z):
        if pd.notna(zi) and zi < -dip_z:
            hold = dip_hold_days
        state.iloc[i] = 1.0 if hold > 0 else 0.0
        hold = max(0, hold - 1)
    return state


def regime_composite_weights(
    closes: pd.DataFrame,
    funding: pd.DataFrame | None = None,
    rule_set: str = "50_200",
    vol_target: float = 0.40,
    mixed_mode: str = "dip",
    downtrend_mode: str = "funding",
    dip_z: float = 2.0,
    dip_hold_days: int = 5,
    dip_weight: float = 0.25,
) -> pd.DataFrame:
    """Variant H/H+ composite: trend in uptrend, dip-buy or cash in mixed,
    cash or funding carry in downtrend.

    ``rule_set`` picks the SMA trio the regime counts: ``"50_200"`` uses
    50/100/200-day means, ``"20_100"`` the faster 20/50/100 set.
    """
    sma_periods = (50, 100, 200) if rule_set == "50_200" else (20, 50, 100)
    btc = closes.columns[0]
    on = rules_on(closes[btc], sma_periods)
    labels = on.map(regime.trend)

    trend_w = pd.DataFrame(index=closes.index, columns=closes.columns, dtype=float)
    vols = {s: sizing.annual_vol(closes[s]) for s in closes.columns}
    for day in closes.index:
        if labels[day] != "uptrend" and mixed_mode != "trend":
            trend_w.loc[day] = 0.0
            continue
        sigs = {s: float(ensemble.signal(closes[s])[day]) for s in closes.columns}
        trend_w.loc[day] = sizing.target_weights(
            sigs, {s: vols[s][day] for s in closes.columns}, vol_target
        )
    trend_w = trend_w.fillna(0.0)

    w = pd.DataFrame(0.0, index=closes.index, columns=closes.columns)
    w[labels == "uptrend"] = trend_w[labels == "uptrend"]
    if mixed_mode == "trend":
        w[labels == "mixed"] = trend_w[labels == "mixed"]
    elif mixed_mode == "dip":
        for s in closes.columns:
            dip = _dip_state(closes[s], dip_z, dip_hold_days)
            w.loc[(labels == "mixed") & (dip == 1.0), s] = dip_weight
    # mixed_mode == "cash": leave 0
    if downtrend_mode == "funding" and funding is not None:
        fd = funding_downtrend_weights(closes, funding)
        w.loc[labels == "downtrend"] = fd.loc[labels == "downtrend"]
    return w


def trend_long_short_fut_weights(
    closes: pd.DataFrame,
    funding: pd.DataFrame | None = None,
    vol_target: float = 0.40,
    short_max_rules: int = 2,
    short_vol_target: float = 0.25,
    veto_neg_funding: bool = True,
    funding_window_days: int = 7,
) -> tuple[pd.DataFrame, pd.Series]:
    """Long trend leg plus a downtrend short leg; returns (weights, funding_cf).

    Long leg is the baseline trend sleeve at ``vol_target``. When the primary
    asset's rule count is at most ``short_max_rules`` — and, with
    ``veto_neg_funding``, the rolling funding mean is non-negative — each
    asset also carries a short of ``min(1, short_vol_target / vol) / n``.
    ``funding_cf`` is the daily funding cashflow of the short leg in
    normalized equity terms (shorts receive funding when it is positive):
    ``-short_weight * funding_rate`` summed over assets.
    """
    symbols = list(closes.columns)
    btc = symbols[0]
    on = rules_on(closes[btc])
    vols = {s: sizing.annual_vol(closes[s]) for s in symbols}

    long_w = pd.DataFrame(index=closes.index, columns=symbols, dtype=float)
    for day in closes.index:
        sigs = {s: float(ensemble.signal(closes[s])[day]) for s in symbols}
        long_w.loc[day] = sizing.target_weights(
            sigs, {s: vols[s][day] for s in symbols}, vol_target
        )
    long_w = long_w.fillna(0.0)

    short_w = pd.DataFrame(0.0, index=closes.index, columns=symbols)
    n = len(symbols)
    for s in symbols:
        scale = (short_vol_target / vols[s]).clip(upper=1.0).fillna(0.0)
        short_w[s] = -scale / n
    veto = pd.Series(False, index=closes.index)
    if veto_neg_funding and funding is not None and btc in funding.columns:
        veto = (
            (funding[btc].rolling(funding_window_days, min_periods=1).mean() < 0)
            .reindex(closes.index)
            .fillna(False)
        )
    short_w[(on > short_max_rules) | veto] = 0.0

    funding_cf = pd.Series(0.0, index=closes.index)
    if funding is not None:
        for s in symbols:
            if s in funding.columns:
                funding_cf += -short_w[s] * funding[s].reindex(closes.index).fillna(0.0)

    return (long_w + short_w), funding_cf


# Preset metadata exposed to the API/UI: default params plus UI bounds.
# ``requires`` names the extra datasets a preset needs beyond daily OHLCV.
CANDIDATE_PRESETS: dict[str, dict[str, Any]] = {
    "funding_downtrend": {
        "label": "Funding downtrend carry (spot)",
        "kind": "daily",
        "requires": ["funding"],
        "defaults": {
            "max_rules": 2,
            "funding_window_days": 7,
            "funding_threshold": 0.0,
            "funding_zscore": None,
            "max_weight": 0.25,
            "assets": ["BTC", "ETH"],
        },
        "bounds": {
            "max_rules": [0, 9],
            "funding_window_days": [1, 3, 7],
            "max_weight": [0.0, 1.0],
        },
    },
    "regime_composite": {
        "label": "Regime composite H/H+ (spot)",
        "kind": "daily",
        "requires": ["funding"],
        "defaults": {
            "rule_set": "50_200",
            "rebalance_band": 0.20,
            "vol_target": 0.40,
            "mixed_mode": "dip",
            "downtrend_mode": "funding",
            "dip_z": 2.0,
            "dip_hold_days": 5,
            "dip_weight": 0.25,
            "idle_cash_apy": 0.0,
        },
        "bounds": {
            "rule_set": ["50_200", "20_100"],
            "rebalance_band": [0.05, 0.20],
            "mixed_mode": ["trend", "dip", "cash"],
            "downtrend_mode": ["cash", "funding"],
            "idle_cash_apy": [0.0, 0.05],
        },
    },
    "intraday_flush_mr": {
        "label": "Intraday flush mean-reversion (spot)",
        "kind": "intraday",
        "requires": ["intraday"],
        "defaults": {
            "bar_tf": "4h",
            "atr_period": 14,
            "entry_atr_mult": 2.5,
            "target_atr_mult": 1.0,
            "max_hold_hours": 24,
            "crash_veto_24h": -0.12,
            "max_weight": 0.25,
            "trade_through_bps": 5.0,
        },
        "bounds": {
            "bar_tf": ["1h", "4h"],
            "entry_atr_mult": [2.0, 3.0],
            "max_hold_hours": [24, 48],
        },
    },
    "trend_long_short_fut": {
        "label": "Trend long/short (perp futures)",
        "kind": "daily",
        "requires": ["funding"],
        "defaults": {
            "vol_target": 0.40,
            "rebalance_band": 0.20,
            "short_max_rules": 2,
            "short_vol_target": 0.25,
            "veto_neg_funding": True,
            "include_funding_pnl": True,
        },
        "bounds": {
            "short_max_rules": [0, 9],
            "short_vol_target": [0.25, 0.40],
        },
        "default_fee_tier": "adv2_fut",
    },
}
