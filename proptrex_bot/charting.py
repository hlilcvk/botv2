from __future__ import annotations

import os
import tempfile
from typing import Optional

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from scoring import compute_indicators


def render_signal_chart(
    df: pd.DataFrame,
    signal,
    title: Optional[str] = None,
    bars: int = 140,
) -> str:
    data = compute_indicators(df).tail(bars).copy().reset_index(drop=True)
    x = np.arange(len(data))

    fig = plt.figure(figsize=(12, 7.2), dpi=120)
    gs = fig.add_gridspec(3, 1, height_ratios=[6, 1.6, 1.6], hspace=0.08)

    ax = fig.add_subplot(gs[0])
    ax_rsi = fig.add_subplot(gs[1], sharex=ax)
    ax_macd = fig.add_subplot(gs[2], sharex=ax)

    ax.set_facecolor("white")
    ax_rsi.set_facecolor("white")
    ax_macd.set_facecolor("white")

    # Vectorized candlestick
    colors = np.where(data["close"] >= data["open"], "#16a34a", "#dc2626")
    bottoms = data[["open", "close"]].min(axis=1)
    heights = (data["close"] - data["open"]).abs()
    heights = heights.clip(lower=(data["high"] - data["low"]) * 0.02).clip(lower=1e-8)

    # Wicks (vectorized via LineCollection)
    from matplotlib.collections import LineCollection
    wick_segments = [[(i, data["low"].iloc[i]), (i, data["high"].iloc[i])] for i in range(len(data))]
    wick_collection = LineCollection(wick_segments, colors=colors, linewidths=1.0)
    ax.add_collection(wick_collection)

    # Bodies
    ax.bar(x, heights, bottom=bottoms, color=colors, alpha=0.9, width=0.66)

    ax.plot(x, data["ema50"], label="EMA 50", linewidth=1.5)
    ax.plot(x, data["ema200"], label="EMA 200", linewidth=1.5)

    # Trade levels
    ax.axhspan(signal.entry_low, signal.entry_high, color="#fde68a", alpha=0.35, label="Entry Zone")
    ax.axhline(signal.stop_loss, color="#ef4444", linestyle="--", linewidth=1.2, label="Stop")
    ax.axhline(signal.tp1, color="#22c55e", linestyle="--", linewidth=1.0, label="TP1")
    ax.axhline(signal.tp2, color="#16a34a", linestyle="--", linewidth=1.0, label="TP2")
    ax.axhline(signal.tp3, color="#15803d", linestyle="--", linewidth=1.0, label="TP3")

    last_price = data.iloc[-1]["close"]
    ax.set_title(title or f"{signal.symbol} | {signal.side} | {signal.opportunity_score:.1f}")
    ax.text(
        0.99,
        0.98,
        (
            f"{signal.side} | {signal.status}\n"
            f"Opp: {signal.opportunity_score:.1f} | Why: {signal.why_enter_score:.1f}\n"
            f"Freshness: {signal.entry_freshness}\n"
            f"Last: {last_price:.6f}"
        ),
        transform=ax.transAxes,
        ha="right",
        va="top",
        fontsize=9,
        bbox=dict(boxstyle="round,pad=0.35", facecolor="white", alpha=0.85, edgecolor="#d1d5db"),
    )

    ax.legend(loc="upper left", fontsize=8)
    ax.grid(alpha=0.2)
    ax.autoscale_view()

    # RSI
    ax_rsi.plot(x, data["rsi"], color="#7c3aed", linewidth=1.2)
    ax_rsi.axhline(70, color="#9ca3af", linestyle="--", linewidth=0.8)
    ax_rsi.axhline(50, color="#d1d5db", linestyle="--", linewidth=0.8)
    ax_rsi.axhline(30, color="#9ca3af", linestyle="--", linewidth=0.8)
    ax_rsi.set_ylim(0, 100)
    ax_rsi.set_ylabel("RSI", fontsize=8)
    ax_rsi.grid(alpha=0.2)

    # MACD
    ax_macd.plot(x, data["macd"], color="#2563eb", linewidth=1.1, label="MACD")
    ax_macd.plot(x, data["macd_signal"], color="#f97316", linewidth=1.1, label="Signal")
    hist_colors = np.where(data["macd_hist"] >= 0, "#16a34a", "#dc2626")
    ax_macd.bar(x, data["macd_hist"], color=hist_colors, alpha=0.5, width=0.7)
    ax_macd.set_ylabel("MACD", fontsize=8)
    ax_macd.legend(loc="upper left", fontsize=7)
    ax_macd.grid(alpha=0.2)

    for axis in [ax, ax_rsi, ax_macd]:
        axis.spines["top"].set_visible(False)
        axis.spines["right"].set_visible(False)

    plt.setp(ax.get_xticklabels(), visible=False)
    plt.setp(ax_rsi.get_xticklabels(), visible=False)

    fd, path = tempfile.mkstemp(prefix="proptrex_chart_", suffix=".png")
    os.close(fd)
    plt.savefig(path, bbox_inches="tight")
    plt.close(fig)
    return path
