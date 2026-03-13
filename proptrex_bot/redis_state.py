from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from typing import Optional

import redis


class RedisStateStore:
    def __init__(
        self,
        url: str = "redis://localhost:6379/0",
        namespace: str = "proptrex",
    ):
        self.url = url
        self.namespace = namespace
        self.client = redis.from_url(url, decode_responses=True)

    def _key(self, key: str) -> str:
        return f"{self.namespace}:{key}"

    def allow(self, key: str, payload_hash: str, cooldown_minutes: int = 45) -> bool:
        now = datetime.now(timezone.utc).isoformat()
        redis_key = self._key(key)
        raw = self.client.get(redis_key)

        if raw is None:
            payload = {
                "payload_hash": payload_hash,
                "last_sent_at": now,
                "cooldown_minutes": cooldown_minutes,
            }
            self.client.set(redis_key, json.dumps(payload))
            return True

        try:
            data = json.loads(raw)
        except Exception:
            data = {
                "payload_hash": "",
                "last_sent_at": now,
                "cooldown_minutes": cooldown_minutes,
            }

        if data.get("payload_hash") != payload_hash:
            data["payload_hash"] = payload_hash
            data["last_sent_at"] = now
            data["cooldown_minutes"] = cooldown_minutes
            self.client.set(redis_key, json.dumps(data))
            return True

        last_sent = datetime.fromisoformat(data["last_sent_at"])
        cool = timedelta(minutes=int(data.get("cooldown_minutes", cooldown_minutes)))
        if datetime.now(timezone.utc) - last_sent >= cool:
            data["last_sent_at"] = now
            self.client.set(redis_key, json.dumps(data))
            return True

        return False

    def set_open_position(self, symbol: str, payload: dict):
        self.client.set(self._key(f"open:{symbol}"), json.dumps(payload))

    def get_open_position(self, symbol: str) -> Optional[dict]:
        raw = self.client.get(self._key(f"open:{symbol}"))
        if not raw:
            return None
        try:
            return json.loads(raw)
        except Exception:
            return None

    def clear_open_position(self, symbol: str):
        self.client.delete(self._key(f"open:{symbol}"))
