from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Dict, Optional
import ccxt


@dataclass
class OrderbookSignal:
    symbol: str
    exchange: str
    bid_notional_top: float
    ask_notional_top: float
    bid_ask_imbalance: float
    spread_pct: float
    dominant_side: str
    score: float

    def to_dict(self) -> Dict:
        return asdict(self)


class OrderbookEngine:
    def __init__(self, exchange_name: str = "binance"):
        mapping = {
            "binance": ccxt.binance,
            "mexc": ccxt.mexc,
            "gateio": ccxt.gateio,
            "kucoin": ccxt.kucoin,
            "okx": ccxt.okx,
        }
        if exchange_name not in mapping:
            raise ValueError(f"Unsupported exchange: {exchange_name}")
        self.exchange_name = exchange_name
        self.client = mapping[exchange_name]({"enableRateLimit": True})

    def analyze(self, symbol: str, depth: int = 20) -> Optional[OrderbookSignal]:
        try:
            ob = self.client.fetch_order_book(symbol, limit=depth)
            bids = ob.get("bids", []) or []
            asks = ob.get("asks", []) or []

            if not bids or not asks:
                return None

            bid_notional = sum(float(price) * float(size) for price, size in bids[:depth])
            ask_notional = sum(float(price) * float(size) for price, size in asks[:depth])

            best_bid = float(bids[0][0])
            best_ask = float(asks[0][0])
            mid = (best_bid + best_ask) / 2.0 if best_bid and best_ask else 0.0
            spread_pct = ((best_ask - best_bid) / mid * 100.0) if mid else 0.0

            total = bid_notional + ask_notional
            imbalance = ((bid_notional - ask_notional) / total * 100.0) if total else 0.0

            if imbalance > 8:
                dominant = "BUYERS"
            elif imbalance < -8:
                dominant = "SELLERS"
            else:
                dominant = "NEUTRAL"

            score = min(100.0, abs(imbalance) * 3.5)

            return OrderbookSignal(
                symbol=symbol,
                exchange=self.exchange_name,
                bid_notional_top=round(bid_notional, 2),
                ask_notional_top=round(ask_notional, 2),
                bid_ask_imbalance=round(imbalance, 2),
                spread_pct=round(spread_pct, 4),
                dominant_side=dominant,
                score=round(score, 2),
            )
        except Exception:
            return None
