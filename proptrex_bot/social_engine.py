from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Dict, List, Optional
import math
import re


@dataclass
class SocialSignal:
    symbol: str
    x_sentiment: str
    square_bias: str
    mention_velocity: float
    narrative_strength: float
    hype_risk: float
    social_conviction: float
    why_lines: List[str]

    def to_dict(self) -> Dict:
        return asdict(self)


class SocialEngine:
    """
    Lightweight production-ready fallback social engine.
    Live API entegrasyonu yoksa text payload ile çalışır.
    X / Binance Square için future adapter-ready yapı.
    """

    POSITIVE_TERMS = {
        "breakout", "bullish", "accumulation", "reversal", "support",
        "squeeze", "spot buying", "undervalued", "trend up", "strength",
        "higher high", "higher low", "reclaim", "volume expansion",
    }
    NEGATIVE_TERMS = {
        "breakdown", "bearish", "distribution", "rejection", "weakness",
        "dump", "sell-off", "rug", "overbought", "lower high",
        "lower low", "resistance rejection", "capitulation",
    }
    HYPE_TERMS = {
        "moon", "100x", "gem", "send it", "parabolic", "ape", "all in",
        "next btc", "insane gains", "easy money",
    }

    def _score_texts(self, texts: List[str]) -> Dict[str, float]:
        if not texts:
            return {
                "positive": 0.0,
                "negative": 0.0,
                "hype": 0.0,
                "count": 0.0,
            }

        positive = 0
        negative = 0
        hype = 0

        for raw in texts:
            t = raw.lower()
            for term in self.POSITIVE_TERMS:
                if term in t:
                    positive += 1
            for term in self.NEGATIVE_TERMS:
                if term in t:
                    negative += 1
            for term in self.HYPE_TERMS:
                if term in t:
                    hype += 1

        return {
            "positive": float(positive),
            "negative": float(negative),
            "hype": float(hype),
            "count": float(len(texts)),
        }

    def analyze(
        self,
        symbol: str,
        x_posts: Optional[List[str]] = None,
        square_posts: Optional[List[str]] = None,
        historical_x_count: Optional[int] = None,
        historical_square_count: Optional[int] = None,
    ) -> SocialSignal:
        x_posts = x_posts or []
        square_posts = square_posts or []

        x_score = self._score_texts(x_posts)
        sq_score = self._score_texts(square_posts)

        x_total = max(1.0, x_score["positive"] + x_score["negative"])
        sq_total = max(1.0, sq_score["positive"] + sq_score["negative"])

        x_sentiment_ratio = (x_score["positive"] - x_score["negative"]) / x_total
        sq_sentiment_ratio = (sq_score["positive"] - sq_score["negative"]) / sq_total

        x_sentiment = "Bullish" if x_sentiment_ratio > 0.15 else "Bearish" if x_sentiment_ratio < -0.15 else "Neutral"
        square_bias = "Positive" if sq_sentiment_ratio > 0.15 else "Negative" if sq_sentiment_ratio < -0.15 else "Neutral"

        hx = historical_x_count or max(1, len(x_posts))
        hs = historical_square_count or max(1, len(square_posts))

        mention_velocity_x = len(x_posts) / max(1, hx)
        mention_velocity_sq = len(square_posts) / max(1, hs)
        mention_velocity = round(((mention_velocity_x + mention_velocity_sq) / 2.0) * 100.0, 2)

        narrative_strength = (
            max(0.0, x_sentiment_ratio) * 30.0
            + max(0.0, sq_sentiment_ratio) * 25.0
            + min(25.0, mention_velocity * 0.15)
            + min(20.0, (x_score["count"] + sq_score["count"]) * 0.8)
        )
        narrative_strength = round(min(100.0, narrative_strength), 2)

        hype_base = x_score["hype"] + sq_score["hype"]
        hype_risk = round(min(100.0, hype_base * 7.5), 2)

        social_conviction = round(
            max(
                0.0,
                min(
                    100.0,
                    narrative_strength * 0.7
                    + max(0.0, x_sentiment_ratio) * 15.0
                    + max(0.0, sq_sentiment_ratio) * 15.0
                    - hype_risk * 0.25,
                ),
            ),
            2,
        )

        why_lines: List[str] = []
        if x_sentiment == "Bullish":
            why_lines.append("X sentiment is supportive")
        if square_bias == "Positive":
            why_lines.append("Binance Square tone is positive")
        if mention_velocity >= 115:
            why_lines.append("mention velocity is expanding")
        if narrative_strength >= 65:
            why_lines.append("narrative strength is elevated")
        if hype_risk >= 60:
            why_lines.append("hype risk is elevated")

        if not why_lines:
            why_lines.append("social flow is neutral")

        return SocialSignal(
            symbol=symbol,
            x_sentiment=x_sentiment,
            square_bias=square_bias,
            mention_velocity=mention_velocity,
            narrative_strength=narrative_strength,
            hype_risk=hype_risk,
            social_conviction=social_conviction,
            why_lines=why_lines,
        )
