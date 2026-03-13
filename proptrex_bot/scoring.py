from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd
from ta.momentum import RSIIndicator
from ta.trend import EMAIndicator, MACD
from ta.volatility import AverageTrueRange


@dataclass
class SignalResult:
    symbol: str
    exchange: str
    timeframe: str
    side: str
    status: str
    opportunity_score: float
    why_enter_score: float
    buyer_dominance: float
    seller_pressure: float
    whale_strength: float
    liquidity_score: float
    structure_bias: str
    high_type: str
    low_type: str
    entry_low: float
    entry_high: float
    stop_loss: float
    tp1: float
    tp2: float
    tp3: float
    tp1_tf: str
    tp2_tf: str
    tp3_tf: str
    expected_hold: str
    expiry_minutes: int
    entry_freshness: str
    why_lines: List[str]
    invalidation_lines: List[str]
    meta: Dict

    def to_dict(self):
        return asdict(self)


def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["ema50"] = EMAIndicator(close=out["close"], window=50).ema_indicator()
    out["ema200"] = EMAIndicator(close=out["close"], window=200).ema_indicator()
    out["rsi"] = RSIIndicator(close=out["close"], window=14).rsi()
    macd = MACD(close=out["close"], window_fast=12, window_slow=26, window_sign=9)
    out["macd"] = macd.macd()
    out["macd_signal"] = macd.macd_signal()
    out["macd_hist"] = macd.macd_diff()
    out["atr"] = AverageTrueRange(
        high=out["high"], low=out["low"], close=out["close"], window=14
    ).average_true_range()
    out["vol_ma20"] = out["volume"].rolling(20).mean()
    out["usd_volume"] = out["close"] * out["volume"]
    return out.dropna().reset_index(drop=True)


def classify_structure(df: pd.DataFrame, lookback: int = 40) -> Tuple[str, str, str]:
    sub = df.tail(lookback).copy()
    highs = sub["high"].values
    lows = sub["low"].values

    prev_high = np.max(highs[:-10]) if len(highs) > 12 else np.max(highs[:-1])
    last_high = np.max(highs[-10:]) if len(highs) > 10 else highs[-1]
    prev_low = np.min(lows[:-10]) if len(lows) > 12 else np.min(lows[:-1])
    last_low = np.min(lows[-10:]) if len(lows) > 10 else lows[-1]

    high_type = "HH" if last_high > prev_high else "LH"
    low_type = "HL" if last_low > prev_low else "LL"

    if high_type == "HH" and low_type == "HL":
        bias = "BULLISH"
    elif high_type == "LH" and low_type == "LL":
        bias = "BEARISH"
    elif high_type == "HH" and low_type == "LL":
        bias = "DECISION"
    else:
        bias = "RANGE"

    return bias, high_type, low_type


def buyer_seller_pressure(df: pd.DataFrame, lookback: int = 20) -> Tuple[float, float]:
    sub = df.tail(lookback)
    buy_vol = sub.loc[sub["close"] > sub["open"], "volume"].sum()
    sell_vol = sub.loc[sub["close"] < sub["open"], "volume"].sum()
    total = buy_vol + sell_vol
    if total <= 0:
        return 50.0, 50.0
    buy_pct = float(buy_vol / total * 100.0)
    sell_pct = 100.0 - buy_pct
    return buy_pct, sell_pct


def detect_whale(df: pd.DataFrame, vol_mult: float = 1.5) -> Tuple[str, float, float]:
    row = df.iloc[-1]
    if row["vol_ma20"] <= 0:
        return "NONE", 0.0, 1.0
    mult = float(row["volume"] / row["vol_ma20"])
    if mult >= vol_mult and row["close"] > row["open"]:
        return "BUY", min(100.0, mult * 25.0), mult
    if mult >= vol_mult and row["close"] < row["open"]:
        return "SELL", min(100.0, mult * 25.0), mult
    return "NONE", min(100.0, mult * 15.0), mult


def detect_liquidity_sweep(df: pd.DataFrame, window: int = 20) -> Tuple[str, float]:
    if len(df) < window + 2:
        return "NONE", 0.0
    row = df.iloc[-1]
    prev = df.iloc[:-1]
    highest_prev = prev.tail(window)["high"].max()
    lowest_prev = prev.tail(window)["low"].min()

    if row["high"] > highest_prev and row["close"] < row["high"]:
        return "BUY_SIDE_SWEPT", 78.0
    if row["low"] < lowest_prev and row["close"] > row["low"]:
        return "SELL_SIDE_SWEPT", 78.0
    return "NONE", 35.0


def momentum_score(df: pd.DataFrame) -> Tuple[float, str]:
    row = df.iloc[-1]
    score = 0.0
    bias = "NEUTRAL"

    if row["close"] > row["ema50"] > row["ema200"]:
        score += 34
        bias = "BULLISH"
    elif row["close"] < row["ema50"] < row["ema200"]:
        score += 34
        bias = "BEARISH"

    if row["rsi"] > 52:
        score += 18
        if bias == "NEUTRAL":
            bias = "BULLISH"
    elif row["rsi"] < 48:
        score += 18
        if bias == "NEUTRAL":
            bias = "BEARISH"

    if row["macd"] > row["macd_signal"] and row["macd_hist"] > 0:
        score += 18
    elif row["macd"] < row["macd_signal"] and row["macd_hist"] < 0:
        score += 18

    if row["volume"] > row["vol_ma20"]:
        score += 10

    return min(100.0, score), bias


def accumulation_distribution_score(
    df: pd.DataFrame, buyer_pct: float, seller_pct: float, whale_action: str
) -> Tuple[float, float]:
    row = df.iloc[-1]
    atr_pct = float((row["atr"] / row["close"]) * 100.0) if row["close"] else 0.0

    accumulation = 0.0
    distribution = 0.0

    if buyer_pct > 57:
        accumulation += 25
    if seller_pct > 57:
        distribution += 25

    if whale_action == "BUY":
        accumulation += 20
    elif whale_action == "SELL":
        distribution += 20

    if row["close"] > row["ema50"]:
        accumulation += 12
    else:
        distribution += 12

    if row["rsi"] > 50:
        accumulation += 10
    else:
        distribution += 10

    if row["macd_hist"] > 0:
        accumulation += 10
    else:
        distribution += 10

    if atr_pct < 4:
        accumulation += 6
        distribution += 6

    return min(100.0, accumulation), min(100.0, distribution)


def derive_levels(df: pd.DataFrame, side: str) -> Tuple[float, float, float, float, float, float]:
    row = df.iloc[-1]
    atr = float(row["atr"])
    close = float(row["close"])

    # Use recent swing structure for stop placement
    recent = df.tail(30)
    swing_low = float(recent["low"].min())
    swing_high = float(recent["high"].max())

    if side == "LONG":
        entry_low = close - atr * 0.30
        entry_high = close + atr * 0.10
        # Stop below swing low, capped at 2.5×ATR below entry_high
        stop = max(swing_low - atr * 0.15, entry_high - atr * 2.5)
        risk = entry_high - stop          # actual R per unit
        tp1 = entry_high + risk * 1.5    # 1.5R
        tp2 = entry_high + risk * 2.5    # 2.5R
        tp3 = entry_high + risk * 4.0    # 4R
    else:
        entry_low = close - atr * 0.10
        entry_high = close + atr * 0.30
        # Stop above swing high, capped at 2.5×ATR above entry_low
        stop = min(swing_high + atr * 0.15, entry_low + atr * 2.5)
        risk = stop - entry_low           # actual R per unit
        tp1 = entry_low - risk * 1.5     # 1.5R
        tp2 = entry_low - risk * 2.5     # 2.5R
        tp3 = entry_low - risk * 4.0     # 4R

    return (
        round(entry_low, 6),
        round(entry_high, 6),
        round(stop, 6),
        round(tp1, 6),
        round(tp2, 6),
        round(tp3, 6),
    )


def entry_freshness(df: pd.DataFrame, side: str, entry_low: float, entry_high: float) -> str:
    close = float(df.iloc[-1]["close"])
    atr = float(df.iloc[-1]["atr"])

    if entry_low <= close <= entry_high:
        return "Fresh"

    # LONG: chase risk when price ran above entry_high
    # SHORT: chase risk when price dropped below entry_low
    if side == "LONG":
        dist = close - entry_high if close > entry_high else abs(entry_low - close)
    else:
        dist = entry_low - close if close < entry_low else abs(close - entry_high)

    if dist <= atr * 0.5:
        return "Acceptable"
    if dist <= atr * 1.0:
        return "Late"
    return "Do Not Chase"


def hold_profile(timeframe: str) -> Tuple[str, int]:
    mapping = {
        "1m": ("5–90 min", 45),
        "3m": ("15–180 min", 60),
        "5m": ("20 min–4 h", 90),
        "15m": ("1–8 h", 180),
        "30m": ("2–12 h", 240),
        "1h": ("4–24 h", 480),
        "4h": ("1–5 d", 1440),
    }
    return mapping.get(timeframe, ("30 min–6 h", 90))


def score_market_context(context_map: Dict[str, str], side: str, coin_bias: str = "NEUTRAL") -> float:
    # Coin's own structure — 40% weight
    if (side == "LONG" and coin_bias == "BULLISH") or (side == "SHORT" and coin_bias == "BEARISH"):
        self_score = 100.0
    elif coin_bias == "DECISION":
        self_score = 55.0
    elif coin_bias == "RANGE":
        self_score = 20.0
    else:
        self_score = 0.0

    # BTC/ETH/SOL macro alignment — 60% weight
    aligned = 0
    total = 0
    for _, bias in context_map.items():
        total += 1
        if side == "LONG" and bias == "BULLISH":
            aligned += 1
        elif side == "SHORT" and bias == "BEARISH":
            aligned += 1
    macro_score = round((aligned / total) * 100.0, 2) if total else 50.0

    return round(self_score * 0.40 + macro_score * 0.60, 2)


def build_signal(
    symbol: str,
    exchange: str,
    timeframe: str,
    df: pd.DataFrame,
    context_map: Dict[str, str],
) -> SignalResult | None:
    df = compute_indicators(df)
    if len(df) < 180:
        return None

    buyer_pct, seller_pct = buyer_seller_pressure(df)
    whale_action, whale_strength, whale_mult = detect_whale(df)
    liquidity_event, liquidity_score = detect_liquidity_sweep(df)
    structure_bias, high_type, low_type = classify_structure(df)
    mom_score, mom_bias = momentum_score(df)
    accum_score, dist_score = accumulation_distribution_score(
        df, buyer_pct, seller_pct, whale_action
    )

    side = "NONE"

    if (
        structure_bias in ["BULLISH", "DECISION", "RANGE"]
        and buyer_pct >= 50.1
        and whale_action in ["BUY", "NONE"]
        and mom_bias in ["BULLISH", "NEUTRAL"]
    ):
        side = "LONG"

    elif (
        structure_bias in ["BEARISH", "DECISION", "RANGE"]
        and seller_pct >= 50.1
        and whale_action in ["SELL", "NONE"]
        and mom_bias in ["BEARISH", "NEUTRAL"]
    ):
        side = "SHORT"

    if side == "NONE":
        import os
        debug_path = os.path.join(os.path.dirname(__file__), "bot_debug.txt")
        msg = f"{symbol} | struct={structure_bias}, buy={buyer_pct:.1f}, sell={seller_pct:.1f}, whale={whale_action}, mom={mom_bias}\n"
        with open(debug_path, "a") as f:
            f.write(msg)
        return None

    context_score = score_market_context(context_map, side, coin_bias=structure_bias)
    if side == "LONG":
        base_struct_score = 85 if structure_bias == "BULLISH" else 62
        flow_score = buyer_pct
        hidden_flow_score = accum_score
    else:
        base_struct_score = 85 if structure_bias == "BEARISH" else 62
        flow_score = seller_pct
        hidden_flow_score = dist_score

    opportunity_score = round(
        (
            base_struct_score * 0.15
            + flow_score * 0.15
            + whale_strength * 0.12
            + liquidity_score * 0.10
            + mom_score * 0.24
            + hidden_flow_score * 0.16
            + context_score * 0.08
        ),
        2,
    )

    why_enter_score = round(
        (
            flow_score * 0.22
            + whale_strength * 0.18
            + liquidity_score * 0.12
            + mom_score * 0.20
            + hidden_flow_score * 0.18
            + context_score * 0.10
        ),
        2,
    )

    entry_low, entry_high, stop_loss, tp1, tp2, tp3 = derive_levels(df, side)
    freshness = entry_freshness(df, side, entry_low, entry_high)
    expected_hold, expiry_minutes = hold_profile(timeframe)

    if opportunity_score >= 70 and why_enter_score >= 65:
        status = "TRADEABLE"
    elif opportunity_score >= 60:
        status = "WATCHLIST"
    else:
        print(f"[debug-score] {symbol} -> side={side} | opp={opportunity_score}, why={why_enter_score}")
        return None

    why_lines = []
    if side == "LONG":
        why_lines.extend(
            [
                "buyers remain in control",
                "bullish structure is intact",
                "whale buy activity supports continuation",
            ]
        )
        if liquidity_event == "SELL_SIDE_SWEPT":
            why_lines.append("sell-side liquidity sweep reclaimed")
        if buyer_pct >= 60:
            why_lines.append("buyer dominance is decisively positive")
    else:
        why_lines.extend(
            [
                "sellers remain in control",
                "bearish structure is intact",
                "whale sell activity supports continuation",
            ]
        )
        if liquidity_event == "BUY_SIDE_SWEPT":
            why_lines.append("buy-side liquidity sweep rejected")
        if seller_pct >= 60:
            why_lines.append("seller pressure is decisively positive")

    invalidation_lines = []
    if side == "LONG":
        invalidation_lines = [
            f"5m close below {stop_loss}",
            "buyer dominance falls below 52",
            "whale support fades",
        ]
    else:
        invalidation_lines = [
            f"5m close above {stop_loss}",
            "seller pressure falls below 52",
            "whale sell pressure fades",
        ]

    return SignalResult(
        symbol=symbol,
        exchange=exchange,
        timeframe=timeframe,
        side=side,
        status=status,
        opportunity_score=opportunity_score,
        why_enter_score=why_enter_score,
        buyer_dominance=round(buyer_pct, 2),
        seller_pressure=round(seller_pct, 2),
        whale_strength=round(whale_strength, 2),
        liquidity_score=round(liquidity_score, 2),
        structure_bias=structure_bias,
        high_type=high_type,
        low_type=low_type,
        entry_low=entry_low,
        entry_high=entry_high,
        stop_loss=stop_loss,
        tp1=tp1,
        tp2=tp2,
        tp3=tp3,
        tp1_tf=timeframe,
        tp2_tf={"1m": "5m", "3m": "15m", "5m": "15m", "15m": "1h", "30m": "1h", "1h": "4h", "4h": "1d"}.get(timeframe, "1h"),
        tp3_tf={"1m": "15m", "3m": "30m", "5m": "1h", "15m": "4h", "30m": "4h", "1h": "1d", "4h": "1w"}.get(timeframe, "4h"),
        expected_hold=expected_hold,
        expiry_minutes=expiry_minutes,
        entry_freshness=freshness,
        why_lines=why_lines,
        invalidation_lines=invalidation_lines,
        meta={
            "whale_action": whale_action,
            "whale_mult": round(whale_mult, 2),
            "liquidity_event": liquidity_event,
            "momentum_bias": mom_bias,
            "context_score": context_score,
            "accumulation_score": round(accum_score, 2),
            "distribution_score": round(dist_score, 2),
        },
    )
