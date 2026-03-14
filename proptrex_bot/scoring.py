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
    signal_type: str          # DIP_BUY | MOMENTUM_LONG | SHORT | MOMENTUM_SHORT
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


# ── Structural TP/SL constants ────────────────────────────────────────────────
_TF_WEIGHT = {"4h": 4.0, "1h": 3.0, "15m": 2.0, "5m": 1.0}
_SOURCE_BONUS = {"EQUAL_LEVEL": 1.5, "VOB": 1.3, "SWING": 1.0, "FVG": 0.8}


def find_swings(df: pd.DataFrame, left_bars: int = 5, right_bars: int = 5) -> List[dict]:
    """5-bar pivot detection: swing high/low seviyeleri."""
    highs = df["high"].values
    lows = df["low"].values
    swings: List[dict] = []
    for i in range(left_bars, len(df) - right_bars):
        is_high = all(highs[i] > highs[i - j] for j in range(1, left_bars + 1)) and \
                  all(highs[i] > highs[i + j] for j in range(1, right_bars + 1))
        is_low  = all(lows[i]  < lows[i - j]  for j in range(1, left_bars + 1)) and \
                  all(lows[i]  < lows[i + j]  for j in range(1, right_bars + 1))
        if is_high:
            swings.append({"price": float(highs[i]), "type": "RESISTANCE", "bar": i, "source": "SWING"})
        if is_low:
            swings.append({"price": float(lows[i]),  "type": "SUPPORT",    "bar": i, "source": "SWING"})
    return swings


def find_equal_levels(swings: List[dict], threshold: float = 0.002) -> List[dict]:
    """Birbirine yakın 2+ swing = likidite havuzu (equal highs/lows)."""
    clusters: List[dict] = []
    for i in range(len(swings)):
        for j in range(i + 1, len(swings)):
            if swings[i]["type"] != swings[j]["type"]:
                continue
            diff = abs(swings[i]["price"] - swings[j]["price"]) / (swings[i]["price"] + 1e-9)
            if diff < threshold:
                clusters.append({
                    "price": (swings[i]["price"] + swings[j]["price"]) / 2,
                    "type":   swings[i]["type"],
                    "strength": 2,
                    "source": "EQUAL_LEVEL",
                })
    return clusters


def find_fvg(df: pd.DataFrame) -> List[dict]:
    """Fair Value Gap tespiti: 3-mum pattern ile oluşan boşluklar."""
    highs = df["high"].values
    lows  = df["low"].values
    closes = df["close"].values
    last_price = float(closes[-1])
    gaps: List[dict] = []

    for i in range(2, len(df)):
        # Bullish FVG: 3. mumun dibi > 1. mumun tepesi
        if lows[i] > highs[i - 2]:
            gaps.append({
                "upper": float(lows[i]),
                "lower": float(highs[i - 2]),
                "mid":   float((lows[i] + highs[i - 2]) / 2),
                "type":  "BULL_FVG",
                "bar":   i,
            })
        # Bearish FVG: 3. mumun tepesi < 1. mumun dibi
        if highs[i] < lows[i - 2]:
            gaps.append({
                "upper": float(lows[i - 2]),
                "lower": float(highs[i]),
                "mid":   float((lows[i - 2] + highs[i]) / 2),
                "type":  "BEAR_FVG",
                "bar":   i,
            })

    # Henüz doldurulmamış gap'leri döndür
    result = []
    for g in gaps:
        if g["type"] == "BULL_FVG" and last_price < g["lower"]:
            result.append(g)
        elif g["type"] == "BEAR_FVG" and last_price > g["upper"]:
            result.append(g)
    return result


def find_vob(df: pd.DataFrame) -> List[dict]:
    """Volume Order Block: yüksek hacimli güçlü gövdeli mumlar = kurumsal emir bölgeleri."""
    if len(df) < 5:
        return []
    opens   = df["open"].values
    highs   = df["high"].values
    lows    = df["low"].values
    closes  = df["close"].values
    volumes = df["volume"].values
    avg_vol = float(np.mean(volumes))
    if avg_vol <= 0:
        return []

    zones: List[dict] = []
    last_price = float(closes[-1])

    for i in range(2, len(df) - 1):
        if volumes[i] < avg_vol * 1.5:
            continue
        body  = abs(closes[i] - opens[i])
        candle_range = highs[i] - lows[i]
        if candle_range <= 0 or body / candle_range < 0.4:
            continue

        is_bullish = closes[i] > opens[i]
        ob_top = float(max(opens[i], closes[i]))
        ob_bot = float(min(opens[i], closes[i]))

        # Sonraki mumlar bu bölgeyi test etti mi?
        tested = False
        for j in range(i + 1, min(i + 20, len(df))):
            if is_bullish and lows[j] <= opens[i]:
                tested = True; break
            if not is_bullish and highs[j] >= opens[i]:
                tested = True; break

        # Fiyat bölgeyi tamamen geçmiş mi?
        valid = True
        if is_bullish and last_price < ob_bot:
            valid = False
        if not is_bullish and last_price > ob_top:
            valid = False

        if valid:
            zones.append({
                "type":      "BULL_OB" if is_bullish else "BEAR_OB",
                "upper":     ob_top,
                "lower":     ob_bot,
                "mid":       float((ob_top + ob_bot) / 2),
                "volume":    float(volumes[i]),
                "vol_ratio": float(volumes[i] / avg_vol),
                "tested":    tested,
                "bar":       i,
                "source":    "VOB",
            })

    zones.sort(key=lambda z: z["volume"], reverse=True)
    return zones[:10]


def filter_by_direction(levels: List[dict], direction: str, current_price: float) -> List[dict]:
    """LONG → fiyat üstündeki seviyeleri yakından uzağa, SHORT → tersine."""
    if direction == "LONG":
        filtered = [l for l in levels if l["price"] > current_price * 1.002]
        filtered.sort(key=lambda l: l["price"])
    else:
        filtered = [l for l in levels if l["price"] < current_price * 0.998]
        filtered.sort(key=lambda l: l["price"], reverse=True)
    return filtered


def prioritize_levels(levels: List[dict]) -> List[dict]:
    """Her seviyeye TF ağırlığı × kaynak bonusu × güç skoru ata, yüksekten düşüğe sırala."""
    for lv in levels:
        tf_w  = _TF_WEIGHT.get(lv.get("tf", "5m"), 1.0)
        src_b = _SOURCE_BONUS.get(lv.get("source", "SWING"), 1.0)
        lv["priority"] = tf_w * src_b * lv.get("strength", 1)
    levels.sort(key=lambda l: l["priority"], reverse=True)
    return levels


def clamp_by_higher_tf(tps: List[dict], higher_tf_levels: List[dict], direction: str) -> List[dict]:
    """Üst TF bariyerini aşan TP'leri bariyer seviyesine çek."""
    if not higher_tf_levels:
        return tps
    barrier = higher_tf_levels[0]
    result = []
    for tp in tps:
        if direction == "LONG" and tp["price"] > barrier["price"]:
            result.append({**tp, "price": barrier["price"] * 0.997, "clamped": True})
        elif direction == "SHORT" and tp["price"] < barrier["price"]:
            result.append({**tp, "price": barrier["price"] * 1.003, "clamped": True})
        else:
            result.append(tp)
    return result


def build_final_tp(
    candidates: List[dict],
    atr: float,
    current_price: float,
    direction: str,
    stop_price: float,
) -> List[dict]:
    """Min mesafe filtresi uygula, max 4 TP seç; fallback → ATR."""
    min_gap        = atr * 0.6
    min_from_entry = atr * 0.3
    risk = abs(current_price - stop_price) if stop_price and abs(current_price - stop_price) > 1e-9 else atr * 1.5

    final: List[dict] = []
    last_price = current_price

    for c in candidates:
        if abs(c["price"] - current_price) < min_from_entry:
            continue
        if final and abs(c["price"] - last_price) < min_gap:
            if c.get("priority", 0) > final[-1].get("priority", 0):
                final[-1] = c
                last_price = c["price"]
            continue
        final.append(c)
        last_price = c["price"]
        if len(final) >= 4:
            break

    # Yapısal seviye bulunamadıysa ATR fallback
    if not final:
        sign = 1 if direction == "LONG" else -1
        final.append({"price": current_price + sign * atr * 1.5, "source": "ATR_FALLBACK", "priority": 0.1, "tf": "na"})
        final.append({"price": current_price + sign * atr * 3.0, "source": "ATR_FALLBACK", "priority": 0.1, "tf": "na"})

    return [
        {
            **tp,
            "label":        f"TP{i + 1}",
            "rr":           round(abs(tp["price"] - current_price) / risk, 2) if risk > 0 else 0.0,
            "distance_pct": f"{(tp['price'] - current_price) / current_price * 100:+.2f}%",
        }
        for i, tp in enumerate(final)
    ]


def snap_to_structure(tps: List[dict], all_levels: List[dict], atr: float) -> List[dict]:
    """ATR fallback TP'yi yakındaki yapısal seviyeye yapıştır."""
    snap_radius = atr * 1.2
    result = []
    for tp in tps:
        nearest = next(
            (l for l in all_levels
             if l.get("source") != "ATR_FALLBACK" and abs(l["price"] - tp["price"]) < snap_radius),
            None,
        )
        if nearest:
            result.append({**tp, "price": nearest["price"], "source": nearest["source"], "snapped": True})
        else:
            result.append(tp)
    return result


def compute_stop_loss_structural(
    direction: str, current_price: float, df: pd.DataFrame, atr: float
) -> float:
    """Swing high/low bazlı yapısal stop loss; ATR sadece küçük buffer için."""
    swings = find_swings(df, left_bars=3, right_bars=3)
    if direction == "LONG":
        recent_lows = sorted(
            [s for s in swings if s["type"] == "SUPPORT" and s["price"] < current_price],
            key=lambda s: s["price"], reverse=True,
        )
        if recent_lows:
            return round(recent_lows[0]["price"] - atr * 0.2, 8)
        return round(current_price - atr * 1.5, 8)
    else:
        recent_highs = sorted(
            [s for s in swings if s["type"] == "RESISTANCE" and s["price"] > current_price],
            key=lambda s: s["price"],
        )
        if recent_highs:
            return round(recent_highs[0]["price"] + atr * 0.2, 8)
        return round(current_price + atr * 1.5, 8)


def build_tp_matrix_structural(
    direction: str,
    current_price: float,
    klines_by_tf: Dict[str, pd.DataFrame],
    atr: float,
) -> Tuple[List[dict], float]:
    """
    5 adımlı yapısal TP pipeline.
    Returns: (tp_list, structural_stop_price)
    tp_list elemanları: {price, source, tf, label, rr, distance_pct, hit, hit_at, snapped}
    """
    TFs = ["5m", "15m", "1h", "4h"]
    all_levels: List[dict] = []

    # ── ADIM 1: Tüm TF'lerden yapısal seviyeleri topla ───────────────────────
    for tf in TFs:
        df_tf = klines_by_tf.get(tf)
        if df_tf is None or len(df_tf) < 30:
            continue

        swings = find_swings(df_tf)
        for s in swings:
            all_levels.append({**s, "tf": tf})

        eq = find_equal_levels(swings)
        for e in eq:
            all_levels.append({**e, "tf": tf})

        fvgs = find_fvg(df_tf)
        for f in fvgs:
            is_target = (direction == "LONG"  and f["type"] == "BEAR_FVG") or \
                        (direction == "SHORT" and f["type"] == "BULL_FVG")
            if is_target:
                all_levels.append({"price": f["mid"], "tf": tf, "source": "FVG", "type": f["type"]})

    # ── ADIM 2: VOB seviyeleri (1h ve 4h) ────────────────────────────────────
    for tf in ["1h", "4h"]:
        df_tf = klines_by_tf.get(tf)
        if df_tf is None or len(df_tf) < 30:
            continue
        zones = find_vob(df_tf)
        for z in zones:
            is_target = (direction == "LONG"  and z["type"] == "BEAR_OB") or \
                        (direction == "SHORT" and z["type"] == "BULL_OB")
            if is_target:
                all_levels.append({"price": z["mid"], "tf": tf, "source": "VOB", "vol_ratio": z["vol_ratio"]})

    # ── Yapısal stop loss ─────────────────────────────────────────────────────
    primary_df = None
    for _k in ["15m", "5m"]:
        _c = klines_by_tf.get(_k)
        if _c is not None:
            primary_df = _c
            break
    if primary_df is None and klines_by_tf:
        primary_df = next(iter(klines_by_tf.values()))
    if primary_df is not None:
        stop_price = compute_stop_loss_structural(direction, current_price, primary_df, atr)
    else:
        sign = -1 if direction == "LONG" else 1
        stop_price = round(current_price + sign * atr * 1.5, 8)

    # ── ADIM 3: Yöne göre filtrele ────────────────────────────────────────────
    filtered = filter_by_direction(all_levels, direction, current_price)

    # ── ADIM 4: TF hiyerarşisi ile önceliklendir ─────────────────────────────
    prioritized = prioritize_levels(filtered)

    # ── ADIM 4b: Üst TF bariyerine göre kısıtla ──────────────────────────────
    higher_tf_barriers = [l for l in prioritized if _TF_WEIGHT.get(l.get("tf", "5m"), 0) >= 3.0]
    clamped = clamp_by_higher_tf(prioritized, higher_tf_barriers, direction)

    # ── ADIM 5: Min mesafe filtresi + final seçim ────────────────────────────
    tps = build_final_tp(clamped, atr, current_price, direction, stop_price)

    # ── ADIM 5b: Yapısal seviyeye snap ───────────────────────────────────────
    tps = snap_to_structure(tps, all_levels, atr)

    tp_list = [
        {
            "price":        round(float(tp["price"]), 8),
            "source":       tp.get("source", "SWING"),
            "tf":           tp.get("tf", "multi"),
            "label":        tp["label"],
            "rr":           float(tp.get("rr", 0)),
            "distance_pct": tp.get("distance_pct", "0%"),
            "hit":          False,
            "hit_at":       None,
            "snapped":      tp.get("snapped", False),
        }
        for tp in tps
    ]
    return tp_list, stop_price


def derive_levels(df: pd.DataFrame, side: str) -> Tuple[float, float, float, float, float, float]:
    row = df.iloc[-1]
    atr = float(row["atr"])
    close = float(row["close"])

    if side == "LONG":
        entry_low = close - atr * 0.30
        entry_high = close + atr * 0.10
        # Structural stop: swing low - buffer, capped at 2.5×ATR below entry
        struct_stop = compute_stop_loss_structural("LONG", close, df, atr)
        stop = max(struct_stop, entry_high - atr * 2.5)
        risk = entry_high - stop
        tp1 = entry_high + risk * 1.5
        tp2 = entry_high + risk * 2.5
        tp3 = entry_high + risk * 4.0
    else:
        entry_low = close - atr * 0.10
        entry_high = close + atr * 0.30
        # Structural stop: swing high + buffer, capped at 2.5×ATR above entry
        struct_stop = compute_stop_loss_structural("SHORT", close, df, atr)
        stop = min(struct_stop, entry_low + atr * 2.5)
        risk = stop - entry_low
        tp1 = entry_low - risk * 1.5
        tp2 = entry_low - risk * 2.5
        tp3 = entry_low - risk * 4.0

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


def compute_change_24h(df: pd.DataFrame, timeframe: str) -> float:
    """Güncel kapanış ile 24 saat önceki kapanış arasındaki % değişim."""
    bars_per_day = {
        "1m": 1440, "3m": 480, "5m": 288, "15m": 96,
        "30m": 48, "1h": 24, "4h": 6, "1d": 1,
    }
    lookback = bars_per_day.get(timeframe, 96)
    if len(df) <= lookback:
        return 0.0
    price_now = float(df.iloc[-1]["close"])
    price_24h = float(df.iloc[-(lookback + 1)]["close"])
    if price_24h <= 0:
        return 0.0
    return round((price_now - price_24h) / price_24h * 100.0, 2)


def classify_signal_type(side: str, rsi: float, change_24h: float) -> str:
    """
    Sinyal tipini belirle:
      DIP_BUY      — fiyat düşmüş, destek bölgesine gelmiş, toparlanma bekleniyor
      MOMENTUM_LONG — zaten koşuyor, devam momentumu
      SHORT         — kısa pozisyon fırsatı (standart)
      MOMENTUM_SHORT — aşırı satımdan sonra düşüş devamı
    """
    if side == "LONG":
        # Coin son 24 saatte çok az düştü veya hafif negatifse → dip alım
        if change_24h <= 5.0 and rsi < 55:
            return "DIP_BUY"
        # Coin zaten yukarı koşuyor → momentum
        return "MOMENTUM_LONG"
    else:
        if change_24h >= -5.0 and rsi > 45:
            return "MOMENTUM_SHORT"
        return "SHORT"


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

    # ── 24 saatlik değişim — aşırı alım koruması ──────────────────────────────
    change_24h = compute_change_24h(df, timeframe)
    row_last = df.iloc[-1]
    rsi_now = float(row_last["rsi"])

    # LONG sinyali engelleme koşulları:
    #   • Son 24 saatte +30%+ pump yapmış  → tepeden al değil, dağılım bölgesi
    #   • RSI > 75                         → aşırı alım, dip alım değil
    overbought_block = change_24h > 30.0 or rsi_now > 75.0

    # SHORT sinyali engelleme koşulları:
    #   • Son 24 saatte -%30+ çökmüş ve RSI < 25 → aşırı satım, short değil
    oversold_block = change_24h < -30.0 and rsi_now < 25.0

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
        not overbought_block
        and structure_bias in ["BULLISH", "DECISION", "RANGE"]
        and buyer_pct >= 50.1
        and whale_action in ["BUY", "NONE"]
        and mom_bias in ["BULLISH", "NEUTRAL"]
    ):
        side = "LONG"

    elif (
        not oversold_block
        and structure_bias in ["BEARISH", "DECISION", "RANGE"]
        and seller_pct >= 50.1
        and whale_action in ["SELL", "NONE"]
        and mom_bias in ["BEARISH", "NEUTRAL"]
    ):
        side = "SHORT"

    if side == "NONE":
        import os
        debug_path = os.path.join(os.path.dirname(__file__), "bot_debug.txt")
        msg = (
            f"{symbol} | struct={structure_bias}, buy={buyer_pct:.1f}, sell={seller_pct:.1f}, "
            f"whale={whale_action}, mom={mom_bias}, rsi={rsi_now:.1f}, 24h={change_24h:+.1f}%"
            f"{' [OB_BLOCK]' if overbought_block else ''}{' [OS_BLOCK]' if oversold_block else ''}\n"
        )
        with open(debug_path, "a") as f:
            f.write(msg)
        return None

    # ── Sinyal tipi sınıflandırması ───────────────────────────────────────────
    signal_type = classify_signal_type(side, rsi_now, change_24h)

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
        signal_type=signal_type,
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
