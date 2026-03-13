from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Dict, List


@dataclass
class PositionPlan:
    symbol: str
    side: str
    entry_low: float
    entry_high: float
    stop_loss: float
    tp1: float
    tp2: float
    tp3: float
    risk_per_trade_pct: float
    account_size: float
    notional_size: float
    quantity: float
    max_loss: float


class PortfolioManager:
    def __init__(self, account_size: float = 10000.0, risk_per_trade_pct: float = 1.0):
        self.account_size = account_size
        self.risk_per_trade_pct = risk_per_trade_pct

    def build_plan(
        self,
        symbol: str,
        side: str,
        entry_low: float,
        entry_high: float,
        stop_loss: float,
        tp1: float,
        tp2: float,
        tp3: float,
    ) -> PositionPlan:
        entry = (entry_low + entry_high) / 2.0
        risk_per_unit = abs(entry - stop_loss)
        capital_at_risk = self.account_size * (self.risk_per_trade_pct / 100.0)

        if risk_per_unit <= 0:
            quantity = 0.0
        else:
            quantity = capital_at_risk / risk_per_unit

        notional = quantity * entry

        return PositionPlan(
            symbol=symbol,
            side=side,
            entry_low=round(entry_low, 6),
            entry_high=round(entry_high, 6),
            stop_loss=round(stop_loss, 6),
            tp1=round(tp1, 6),
            tp2=round(tp2, 6),
            tp3=round(tp3, 6),
            risk_per_trade_pct=self.risk_per_trade_pct,
            account_size=self.account_size,
            notional_size=round(notional, 2),
            quantity=round(quantity, 6),
            max_loss=round(capital_at_risk, 2),
        )

    def plan_to_lines(self, plan: PositionPlan) -> List[str]:
        return [
            f"Account Size: ${plan.account_size:,.2f}",
            f"Risk per Trade: {plan.risk_per_trade_pct:.2f}%",
            f"Position Size: {plan.quantity}",
            f"Notional: ${plan.notional_size:,.2f}",
            f"Max Loss: ${plan.max_loss:,.2f}",
        ]
