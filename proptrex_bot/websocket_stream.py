from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import AsyncIterator, Dict, Optional

import websockets


@dataclass
class TickEvent:
    exchange: str
    symbol: str
    price: float
    quantity: float
    side: str
    timestamp: int


class BinanceTradeStream:
    """
    Minimal live trade stream.
    Symbol format input: BTC/USDT
    Internal ws format: btcusdt
    """

    def __init__(self, symbol: str):
        self.symbol = symbol
        self.ws_symbol = symbol.replace("/", "").lower()

    async def stream(self) -> AsyncIterator[TickEvent]:
        url = f"wss://stream.binance.com:9443/ws/{self.ws_symbol}@trade"
        async with websockets.connect(url, ping_interval=20, ping_timeout=20) as ws:
            async for message in ws:
                data = json.loads(message)
                yield TickEvent(
                    exchange="binance",
                    symbol=self.symbol,
                    price=float(data["p"]),
                    quantity=float(data["q"]),
                    side="SELL" if data.get("m", False) else "BUY",
                    timestamp=int(data["T"]),
                )


class TickAggregator:
    """
    Tick akışından mikro alıcı/satıcı baskısı çıkarır.
    """

    def __init__(self, max_events: int = 500):
        self.max_events = max_events
        self.buffer: list[TickEvent] = []

    def push(self, event: TickEvent):
        self.buffer.append(event)
        if len(self.buffer) > self.max_events:
            self.buffer = self.buffer[-self.max_events:]

    def snapshot(self) -> Dict:
        if not self.buffer:
            return {
                "buyer_aggression": 50.0,
                "seller_aggression": 50.0,
                "notional_buy": 0.0,
                "notional_sell": 0.0,
            }

        buy_notional = 0.0
        sell_notional = 0.0
        for e in self.buffer:
            notional = e.price * e.quantity
            if e.side == "BUY":
                buy_notional += notional
            else:
                sell_notional += notional

        total = buy_notional + sell_notional
        if total <= 0:
            return {
                "buyer_aggression": 50.0,
                "seller_aggression": 50.0,
                "notional_buy": 0.0,
                "notional_sell": 0.0,
            }

        return {
            "buyer_aggression": round((buy_notional / total) * 100.0, 2),
            "seller_aggression": round((sell_notional / total) * 100.0, 2),
            "notional_buy": round(buy_notional, 2),
            "notional_sell": round(sell_notional, 2),
        }
