from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional


@dataclass
class LifecycleUpdate:
    status: str
    action: str
    lines: list[str]


class SignalLifecycle:
    """
    Trade sonrası yönetim katmanı.
    Basit v1 lifecycle:
    - TRIGGERED
    - TP1_HIT
    - TP2_HIT
    - TP3_HIT
    - INVALIDATED
    """

    def evaluate(
        self,
        side: str,
        current_price: float,
        entry_low: float,
        entry_high: float,
        stop_loss: float,
        tp1: float,
        tp2: float,
        tp3: float,
    ) -> LifecycleUpdate:
        entry_mid = (entry_low + entry_high) / 2.0

        if side == "LONG":
            if current_price <= stop_loss:
                return LifecycleUpdate(
                    status="INVALIDATED",
                    action="Exit remaining position",
                    lines=[
                        "stop loss breached",
                        "setup is no longer valid",
                    ],
                )
            if current_price >= tp3:
                return LifecycleUpdate(
                    status="TP3_HIT",
                    action="Close remaining runner",
                    lines=[
                        "final target reached",
                        "full expansion objective hit",
                    ],
                )
            if current_price >= tp2:
                return LifecycleUpdate(
                    status="TP2_HIT",
                    action="Realize more size and trail stop",
                    lines=[
                        "secondary target reached",
                        "keep only runner if structure remains intact",
                    ],
                )
            if current_price >= tp1:
                return LifecycleUpdate(
                    status="TP1_HIT",
                    action="De-risk and move stop to breakeven",
                    lines=[
                        "first target reached",
                        "lock risk off the table",
                    ],
                )
            if entry_low <= current_price <= entry_high:
                return LifecycleUpdate(
                    status="TRIGGERED",
                    action="Entry zone active",
                    lines=[
                        "position is in execution zone",
                    ],
                )

        else:
            if current_price >= stop_loss:
                return LifecycleUpdate(
                    status="INVALIDATED",
                    action="Exit remaining position",
                    lines=[
                        "stop loss breached",
                        "setup is no longer valid",
                    ],
                )
            if current_price <= tp3:
                return LifecycleUpdate(
                    status="TP3_HIT",
                    action="Close remaining runner",
                    lines=[
                        "final target reached",
                        "full downside objective hit",
                    ],
                )
            if current_price <= tp2:
                return LifecycleUpdate(
                    status="TP2_HIT",
                    action="Realize more size and trail stop",
                    lines=[
                        "secondary target reached",
                        "keep only runner if structure remains intact",
                    ],
                )
            if current_price <= tp1:
                return LifecycleUpdate(
                    status="TP1_HIT",
                    action="De-risk and move stop to breakeven",
                    lines=[
                        "first target reached",
                        "lock risk off the table",
                    ],
                )
            if entry_low <= current_price <= entry_high:
                return LifecycleUpdate(
                    status="TRIGGERED",
                    action="Entry zone active",
                    lines=[
                        "position is in execution zone",
                    ],
                )

        return LifecycleUpdate(
            status="ACTIVE",
            action="Hold current plan",
            lines=["structure still active"],
        )
