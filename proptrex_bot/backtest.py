from __future__ import annotations

import argparse
from dataclasses import dataclass, asdict
from typing import List

import pandas as pd

from scoring import build_signal


@dataclass
class Trade:
    timestamp: str
    symbol: str
    side: str
    entry: float
    stop: float
    tp1: float
    tp2: float
    tp3: float
    outcome: str
    pnl_r: float


def run_backtest(df: pd.DataFrame, symbol: str, exchange: str, timeframe: str) -> List[Trade]:
    trades: List[Trade] = []

    for i in range(230, len(df) - 10):
        window = df.iloc[: i + 1].copy().reset_index(drop=True)
        signal = build_signal(
            symbol=symbol,
            exchange=exchange,
            timeframe=timeframe,
            df=window,
            context_map={},
        )
        if signal is None or signal.status != "TRADEABLE":
            continue

        forward = df.iloc[i + 1 : i + 61].copy()
        entry = (signal.entry_low + signal.entry_high) / 2.0
        risk = abs(entry - signal.stop_loss)
        if risk <= 0:
            continue

        side = signal.side
        outcome = "EXPIRED"
        pnl_r = 0.0

        for _, row in forward.iterrows():
            high = float(row["high"])
            low = float(row["low"])

            if side == "LONG":
                if low <= signal.stop_loss:
                    outcome = "STOP"
                    pnl_r = -1.0
                    break
                if high >= signal.tp3:
                    outcome = "TP3"
                    pnl_r = 3.0
                    break
                if high >= signal.tp2:
                    outcome = "TP2"
                    pnl_r = 2.0
                    break
                if high >= signal.tp1:
                    outcome = "TP1"
                    pnl_r = 1.0
                    break
            else:
                if high >= signal.stop_loss:
                    outcome = "STOP"
                    pnl_r = -1.0
                    break
                if low <= signal.tp3:
                    outcome = "TP3"
                    pnl_r = 3.0
                    break
                if low <= signal.tp2:
                    outcome = "TP2"
                    pnl_r = 2.0
                    break
                if low <= signal.tp1:
                    outcome = "TP1"
                    pnl_r = 1.0
                    break

        trades.append(
            Trade(
                timestamp=str(df.iloc[i]["timestamp"]),
                symbol=symbol,
                side=side,
                entry=round(entry, 6),
                stop=signal.stop_loss,
                tp1=signal.tp1,
                tp2=signal.tp2,
                tp3=signal.tp3,
                outcome=outcome,
                pnl_r=pnl_r,
            )
        )

    return trades


def print_summary(trades: List[Trade]):
    if not trades:
        print("No trades.")
        return

    total = len(trades)
    wins = sum(1 for t in trades if t.pnl_r > 0)
    losses = sum(1 for t in trades if t.pnl_r < 0)
    neutral = total - wins - losses
    net_r = sum(t.pnl_r for t in trades)
    win_rate = wins / total * 100

    print("=== BACKTEST SUMMARY ===")
    print(f"Total Trades: {total}")
    print(f"Wins: {wins}")
    print(f"Losses: {losses}")
    print(f"Neutral/Expired: {neutral}")
    print(f"Win Rate: {win_rate:.2f}%")
    print(f"Net R: {net_r:.2f}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv", required=True, help="CSV path with OHLCV columns")
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--exchange", default="binance")
    parser.add_argument("--interval", default="1m")
    args = parser.parse_args()

    df = pd.read_csv(args.csv)
    required = {"timestamp", "open", "high", "low", "close", "volume"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"CSV missing columns: {missing}")

    trades = run_backtest(df, args.symbol, args.exchange, args.interval)
    print_summary(trades)

    if trades:
        out = pd.DataFrame([asdict(t) for t in trades])
        out_file = f"backtest_results_{args.symbol.replace('/', '_')}.csv"
        out.to_csv(out_file, index=False)
        print(f"Saved results to {out_file}")


if __name__ == "__main__":
    main()
