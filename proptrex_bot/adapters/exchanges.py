from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

import ccxt
import pandas as pd


@dataclass
class ExchangeMarketData:
    exchange: str
    symbol: str
    timeframe: str
    df: pd.DataFrame


class ExchangeClientFactory:
    @staticmethod
    def build(name: str):
        name = name.lower().strip()
        mapping = {
            "binance": ccxt.binance,
            "mexc": ccxt.mexc,
            "gateio": ccxt.gateio,
            "kucoin": ccxt.kucoin,
            "okx": ccxt.okx,
        }
        if name not in mapping:
            raise ValueError(f"Unsupported exchange: {name}")
        client = mapping[name](
            {
                "enableRateLimit": True,
                "options": {
                    "defaultType": "spot",
                },
            }
        )
        return client


class MultiExchangeScanner:
    def __init__(self, exchanges: List[str]):
        self.clients: Dict[str, ccxt.Exchange] = {}
        for ex in exchanges:
            try:
                self.clients[ex] = ExchangeClientFactory.build(ex)
            except Exception:
                continue

    def fetch_ohlcv(
        self,
        exchange: str,
        symbol: str,
        timeframe: str = "1m",
        limit: int = 300,
    ) -> Optional[ExchangeMarketData]:
        client = self.clients.get(exchange)
        if client is None:
            return None
        try:
            ohlcv = client.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
            if not ohlcv:
                return None
            df = pd.DataFrame(
                ohlcv,
                columns=["timestamp", "open", "high", "low", "close", "volume"],
            )
            df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
            for col in ["open", "high", "low", "close", "volume"]:
                df[col] = pd.to_numeric(df[col], errors="coerce")
            df = df.dropna().reset_index(drop=True)
            if df.empty:
                return None
            return ExchangeMarketData(
                exchange=exchange,
                symbol=symbol,
                timeframe=timeframe,
                df=df,
            )
        except Exception:
            return None

    def fetch_first_available(
        self,
        symbol: str,
        timeframe: str = "1m",
        limit: int = 300,
    ) -> Optional[ExchangeMarketData]:
        for ex in self.clients.keys():
            data = self.fetch_ohlcv(ex, symbol, timeframe, limit)
            if data is not None:
                return data
        return None

    def fetch_universe(
        self,
        symbols: List[str],
        timeframe: str = "1m",
        limit: int = 300,
    ) -> List[ExchangeMarketData]:
        results: List[ExchangeMarketData] = []
        for symbol in symbols:
            data = self.fetch_first_available(symbol=symbol, timeframe=timeframe, limit=limit)
            if data is not None:
                results.append(data)
        return results

    def fetch_top_symbols_by_volume(
        self,
        quote: str = "USDT",
        top_n: int = 100,
        min_volume_usd: float = 500000,
    ) -> List[str]:
        """
        Tüm exchange'lerden 24 saatlik quote hacmine göre en yüksek USDT paritelerini getirir.
        Her exchange için fetch_tickers() tek API çağrısıyla tüm hacimleri çeker.
        Aynı sembol birden fazla exchange'de varsa en yüksek hacim korunur.
        """
        volume_map: Dict[str, float] = {}

        for ex, client in self.clients.items():
            try:
                tickers = client.fetch_tickers()
                for symbol, ticker in tickers.items():
                    if not symbol.endswith(f"/{quote}"):
                        continue
                    # Bazı exchange'ler quoteVolume yerine farklı alan kullanır
                    vol = float(
                        ticker.get("quoteVolume")
                        or ticker.get("info", {}).get("quoteVol")
                        or ticker.get("info", {}).get("turnover24h")
                        or 0
                    )
                    if vol < min_volume_usd:
                        continue
                    # Aynı sembol için en yüksek hacmi tut
                    if symbol not in volume_map or vol > volume_map[symbol]:
                        volume_map[symbol] = vol
            except Exception:
                continue

        sorted_symbols = sorted(volume_map.items(), key=lambda x: x[1], reverse=True)
        return [s for s, _ in sorted_symbols[:top_n]]

    def fetch_universe_dynamic(
        self,
        timeframe: str = "1m",
        limit: int = 300,
        min_volume_usd: float = 500000,
        top_n: int = 100,
        quote: str = "USDT",
    ) -> List[ExchangeMarketData]:
        """
        Volume bazlı dinamik tarama:
        1. Tüm exchange'lerden top_n sembolü hacme göre seç
        2. Her sembol için OHLCV çek (ilk başarılı exchange kullanılır)
        3. Tekrar eden semboller atlanır
        """
        symbols = self.fetch_top_symbols_by_volume(
            quote=quote,
            top_n=top_n,
            min_volume_usd=min_volume_usd,
        )

        results: List[ExchangeMarketData] = []
        seen: set = set()

        for symbol in symbols:
            if symbol in seen:
                continue
            data = self.fetch_first_available(symbol=symbol, timeframe=timeframe, limit=limit)
            if data is not None:
                results.append(data)
                seen.add(symbol)

        return results
