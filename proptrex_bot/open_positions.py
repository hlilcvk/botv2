from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Dict, Optional
from datetime import datetime, timezone


@dataclass
class OpenPosition:
    symbol: str
    exchange: str
    side: str
    entry_low: float
    entry_high: float
    entry_avg: float
    stop_loss: float
    tp1: float
    tp2: float
    tp3: float
    status: str
    opened_at: str
    tp1_hit: bool = False
    tp2_hit: bool = False
    tp3_hit: bool = False
    breakeven_armed: bool = False

    def to_dict(self) -> Dict:
        return asdict(self)


class OpenPositionBook:
    def __init__(self):
        self.positions: Dict[str, OpenPosition] = {}

    def open_from_signal(self, signal) -> OpenPosition:
        avg = round((signal.entry_low + signal.entry_high) / 2.0, 6)
        pos = OpenPosition(
            symbol=signal.symbol,
            exchange=signal.exchange,
            side=signal.side,
            entry_low=signal.entry_low,
            entry_high=signal.entry_high,
            entry_avg=avg,
            stop_loss=signal.stop_loss,
            tp1=signal.tp1,
            tp2=signal.tp2,
            tp3=signal.tp3,
            status="OPEN",
            opened_at=datetime.now(timezone.utc).isoformat(),
        )
        self.positions[signal.symbol] = pos
        return pos

    def get(self, symbol: str) -> Optional[OpenPosition]:
        return self.positions.get(symbol)

    def close(self, symbol: str):
        if symbol in self.positions:
            del self.positions[symbol]

    def update(self, symbol: str, current_price: float) -> Optional[dict]:
        pos = self.positions.get(symbol)
        if pos is None:
            return None

        if pos.side == "LONG":
            if current_price <= pos.stop_loss:
                pos.status = "INVALIDATED"
                return {"status": pos.status, "action": "close"}
            if not pos.tp1_hit and current_price >= pos.tp1:
                pos.tp1_hit = True
                pos.breakeven_armed = True
                pos.status = "TP1_HIT"
                return {"status": pos.status, "action": "de-risk"}
            if not pos.tp2_hit and current_price >= pos.tp2:
                pos.tp2_hit = True
                pos.status = "TP2_HIT"
                return {"status": pos.status, "action": "realize_more"}
            if not pos.tp3_hit and current_price >= pos.tp3:
                pos.tp3_hit = True
                pos.status = "TP3_HIT"
                return {"status": pos.status, "action": "close_runner"}
        else:
            if current_price >= pos.stop_loss:
                pos.status = "INVALIDATED"
                return {"status": pos.status, "action": "close"}
            if not pos.tp1_hit and current_price <= pos.tp1:
                pos.tp1_hit = True
                pos.breakeven_armed = True
                pos.status = "TP1_HIT"
                return {"status": pos.status, "action": "de-risk"}
            if not pos.tp2_hit and current_price <= pos.tp2:
                pos.tp2_hit = True
                pos.status = "TP2_HIT"
                return {"status": pos.status, "action": "realize_more"}
            if not pos.tp3_hit and current_price <= pos.tp3:
                pos.tp3_hit = True
                pos.status = "TP3_HIT"
                return {"status": pos.status, "action": "close_runner"}

        return {"status": "OPEN", "action": "hold"}
