from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional
import requests


@dataclass
class XPost:
    text: str
    created_at: str
    author_id: Optional[str] = None


class XAdapter:
    """
    Minimal X recent search adapter.
    Requires Bearer token.
    Falls back to [] on any failure.
    """

    SEARCH_URL = "https://api.twitter.com/2/tweets/search/recent"

    def __init__(self, bearer_token: Optional[str] = None, timeout: int = 12):
        self.bearer_token = bearer_token
        self.timeout = timeout

    def is_enabled(self) -> bool:
        return bool(self.bearer_token)

    def search_symbol(self, symbol: str, limit: int = 20) -> List[XPost]:
        if not self.bearer_token:
            return []

        base = symbol.split("/")[0].replace("USDT", "").replace("USD", "").strip()
        query = f'("{base}" OR "${base}") lang:en -is:retweet'
        headers = {"Authorization": f"Bearer {self.bearer_token}"}
        params = {
            "query": query,
            "max_results": min(max(10, limit), 100),
            "tweet.fields": "created_at,author_id",
        }

        try:
            r = requests.get(
                self.SEARCH_URL,
                headers=headers,
                params=params,
                timeout=self.timeout,
            )
            r.raise_for_status()
            data = r.json()
            items = data.get("data", []) or []
            posts = [
                XPost(
                    text=item.get("text", ""),
                    created_at=item.get("created_at", ""),
                    author_id=item.get("author_id"),
                )
                for item in items
            ]
            return posts
        except Exception:
            return []

    def texts_for_symbol(self, symbol: str, limit: int = 20) -> List[str]:
        return [p.text for p in self.search_symbol(symbol, limit=limit)]
