from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional
import requests
from bs4 import BeautifulSoup


@dataclass
class SquarePost:
    text: str
    created_at: str = ""


class BinanceSquareAdapter:
    """
    Public fallback scraper-style adapter.
    Official stable public API olmadığı için defensive tasarlandı.
    HTML değişirse [] döner.
    """

    SEARCH_URL = "https://www.binance.com/en/square/search"

    def __init__(self, timeout: int = 12):
        self.timeout = timeout

    def search_symbol(self, symbol: str, limit: int = 20) -> List[SquarePost]:
        base = symbol.split("/")[0].replace("USDT", "").replace("USD", "").strip()
        params = {"q": base}
        headers = {
            "User-Agent": "Mozilla/5.0",
        }

        try:
            r = requests.get(
                self.SEARCH_URL,
                params=params,
                headers=headers,
                timeout=self.timeout,
            )
            r.raise_for_status()
            soup = BeautifulSoup(r.text, "html.parser")

            texts: List[SquarePost] = []

            candidates = soup.find_all(["div", "p", "span"])
            for c in candidates:
                text = " ".join(c.get_text(" ", strip=True).split())
                if len(text) < 40:
                    continue
                if base.lower() not in text.lower():
                    continue
                texts.append(SquarePost(text=text))
                if len(texts) >= limit:
                    break

            return texts
        except Exception:
            return []

    def texts_for_symbol(self, symbol: str, limit: int = 20) -> List[str]:
        return [p.text for p in self.search_symbol(symbol, limit=limit)]
