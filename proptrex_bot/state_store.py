from __future__ import annotations

import json
import os
from dataclasses import dataclass, asdict
from datetime import datetime, timezone, timedelta
from typing import Dict, Optional


@dataclass
class SignalState:
    key: str
    symbol: str
    exchange: str
    side: str
    status: str
    last_sent_at: str
    cooldown_minutes: int
    payload_hash: str


class JsonStateStore:
    def __init__(self, path: str = "signal_state.json"):
        self.path = path
        self.state: Dict[str, SignalState] = {}
        self._load()

    def _load(self):
        if not os.path.exists(self.path):
            self.state = {}
            return
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            self.state = {
                k: SignalState(**v) for k, v in raw.items()
            }
        except Exception:
            self.state = {}

    def _save(self):
        raw = {k: asdict(v) for k, v in self.state.items()}
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(raw, f, ensure_ascii=False, indent=2)

    def allow(self, key: str, payload_hash: str, cooldown_minutes: int = 45) -> bool:
        now = datetime.now(timezone.utc)
        item = self.state.get(key)

        if item is None:
            self.state[key] = SignalState(
                key=key,
                symbol="",
                exchange="",
                side="",
                status="",
                last_sent_at=now.isoformat(),
                cooldown_minutes=cooldown_minutes,
                payload_hash=payload_hash,
            )
            self._save()
            return True

        last_sent = datetime.fromisoformat(item.last_sent_at)
        cooldown = timedelta(minutes=item.cooldown_minutes)

        if item.payload_hash != payload_hash:
            item.last_sent_at = now.isoformat()
            item.payload_hash = payload_hash
            self._save()
            return True

        if now - last_sent >= cooldown:
            item.last_sent_at = now.isoformat()
            item.payload_hash = payload_hash
            self._save()
            return True

        return False

    def prune(self, max_age_multiplier: float = 3.0) -> int:
        """Remove entries whose cooldown has expired by max_age_multiplier times.
        Returns the number of removed entries."""
        now = datetime.now(timezone.utc)
        expired = [
            k for k, v in self.state.items()
            if now - datetime.fromisoformat(v.last_sent_at)
            >= timedelta(minutes=v.cooldown_minutes * max_age_multiplier)
        ]
        for k in expired:
            del self.state[k]
        if expired:
            self._save()
        return len(expired)

    def upsert_metadata(self, key: str, symbol: str, exchange: str, side: str, status: str):
        if key not in self.state:
            return
        self.state[key].symbol = symbol
        self.state[key].exchange = exchange
        self.state[key].side = side
        self.state[key].status = status
        self._save()
