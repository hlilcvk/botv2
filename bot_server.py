"""
PROPTREX Signal Bot — FastAPI + WebSocket Server
Replaces the old Binance pump bot with the proptrex multi-exchange scoring engine.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sys
import time
from datetime import datetime
from typing import Dict, List, Optional

import requests
import tweepy
import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel

# ── proptrex engine imports ──────────────────────────────────────────────────
_BOT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_BOT_DIR, "proptrex_bot"))

from adapters.exchanges import MultiExchangeScanner  # noqa: E402
from charting import render_signal_chart             # noqa: E402
from open_positions import OpenPositionBook          # noqa: E402
from orderbook_engine import OrderbookEngine         # noqa: E402
from portfolio_manager import PortfolioManager       # noqa: E402
from scoring import (                                # noqa: E402
    build_signal,
    build_tp_matrix_structural,
    classify_structure,
    compute_indicators,
    derive_levels,
)
from signal_lifecycle import SignalLifecycle         # noqa: E402
from social_engine import SocialEngine               # noqa: E402
from square_adapter import BinanceSquareAdapter      # noqa: E402
from state_store import JsonStateStore               # noqa: E402
from x_adapter import XAdapter                       # noqa: E402

# ── App ──────────────────────────────────────────────────────────────────────

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

CONFIG_FILE = os.path.join(_BOT_DIR, "bot_config.json")

# In-memory signal store (last 50 signals)
recent_signals: List[dict] = []

# Signal counter for referral frequency tracking
_signal_counter = 0
_scan_counter = 0


_VALID_TIMEFRAMES = {"1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "12h", "1d"}

_TF_LADDER = ["1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "12h", "1d"]


def _get_upper_tfs(tf: str, count: int = 2) -> List[str]:
    """Return up to `count` timeframes above `tf` in the ladder."""
    try:
        idx = _TF_LADDER.index(tf)
        return _TF_LADDER[idx + 1: idx + 1 + count]
    except (ValueError, IndexError):
        return []

# ── Connection Manager ───────────────────────────────────────────────────────

class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, message: dict):
        for conn in list(self.active_connections):
            try:
                await conn.send_json(message)
            except Exception:
                self.disconnect(conn)


manager = ConnectionManager()


# ── Config Model ─────────────────────────────────────────────────────────────

class BotConfig(BaseModel):
    admin_password: str = "proptrex2026"
    # Telegram
    tg_token: str = ""
    tg_chat_id: str = ""
    tg_enabled: bool = False
    # Twitter / X
    tw_api_key: str = ""
    tw_api_secret: str = ""
    tw_access_token: str = ""
    tw_access_secret: str = ""
    tw_enabled: bool = False
    # Referral links
    ref_binance: str = "https://www.binance.com/activity/referral-entry/CPA?ref=CPA_00WPSCQYZA&utm_source=electron"
    ref_mexc: str = "https://promote.mexc.fm/r/KVaJdo8ook"
    ref_gate: str = "https://app.mbm06.com/referral/earn-together/invite/U1dNV1pe?ref=U1dNV1pe&ref_type=103&utm_cmp=rXJBDjtJ&activity_id=1772462196891"
    ref_kucoin: str = "https://www.kucoin.com"
    ref_okx: str = "https://www.okx.com"
    # Referral toggle + frequency
    ref_enabled: bool = True
    ref_every_n_signals: int = 3     # add referral block every N signals (0 = disabled)
    # Platform / promo
    platform_url: str = "https://panel.proptrex.com.tr"
    promo_link: str = "https://proptrex.com.tr"
    promo_enabled: bool = True
    # Scanner runtime (overrides proptrex config)
    scan_interval_seconds: int = 60
    timeframe: str = "15m"
    candle_limit: int = 500
    min_volume_usd: float = 10000
    min_opportunity_score: float = 55.0
    signals_per_scan: int = 5
    exclude_symbols: str = "USDC,USDT,FDUSD,TUSD,DAI,BUSD,USD1,XUSD,PAXG,XAUT,EUR,GBP,GOLD"
    dynamic_scan: bool = True
    top_n_symbols: int = 100
    exchanges: str = "binance,mexc,gateio,kucoin,okx"
    square_enabled: bool = True


def load_config() -> dict:
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r") as f:
                return json.load(f)
        except Exception as e:
            print(f"[config] load error: {e}")
    return BotConfig().model_dump()


def save_config(data: dict):
    with open(CONFIG_FILE, "w") as f:
        json.dump(data, f, indent=4)


# ── API Endpoints ─────────────────────────────────────────────────────────────

@app.get("/api/config")
async def get_config():
    return load_config()


@app.post("/api/config")
async def update_config(config: BotConfig):
    data = config.model_dump()
    # Preserve admin_password from disk if not explicitly changed
    existing = load_config()
    if existing.get("admin_password") and not config.admin_password:
        data["admin_password"] = existing["admin_password"]
    save_config(data)
    return {"status": "success"}


class LoginRequest(BaseModel):
    password: str


@app.post("/api/login")
async def login(req: LoginRequest):
    cfg = load_config()
    if req.password == cfg.get("admin_password", "proptrex2026"):
        return {"status": "success"}
    return {"status": "error", "message": "Geçersiz şifre"}


@app.get("/api/signals")
async def get_signals():
    return {"signals": recent_signals, "count": len(recent_signals)}


@app.get("/api/ticker")
async def proxy_ticker(exchange: str, symbol: str):
    """CORS proxy — Gate.io/KuCoin/OKX ticker verisi tarayıcı üzerinden alınamaz."""
    try:
        from adapters.exchanges import ExchangeClientFactory
        client = ExchangeClientFactory.build(exchange)
        loop = asyncio.get_event_loop()
        ticker = await loop.run_in_executor(None, lambda: client.fetch_ticker(symbol))
        return {
            "price":  float(ticker.get("last") or 0),
            "change": float(ticker.get("percentage") or 0),
            "vol":    float(ticker.get("quoteVolume") or ticker.get("baseVolume") or 0),
        }
    except Exception as e:
        return {"error": str(e)}, 502


@app.get("/api/orderbook")
async def proxy_orderbook(exchange: str, symbol: str, limit: int = 10):
    """CORS proxy — emir defteri."""
    try:
        from adapters.exchanges import ExchangeClientFactory
        client = ExchangeClientFactory.build(exchange)
        loop = asyncio.get_event_loop()
        ob = await loop.run_in_executor(None, lambda: client.fetch_order_book(symbol, limit))
        return {"bids": ob["bids"][:limit], "asks": ob["asks"][:limit]}
    except Exception as e:
        return {"error": str(e)}, 502


@app.get("/api/klines")
async def proxy_klines(exchange: str, symbol: str, timeframe: str = "15m", limit: int = 200):
    """CORS proxy — mum verisi."""
    try:
        from adapters.exchanges import ExchangeClientFactory
        client = ExchangeClientFactory.build(exchange)
        loop = asyncio.get_event_loop()
        ohlcv = await loop.run_in_executor(None, lambda: client.fetch_ohlcv(symbol, timeframe, limit=limit))
        candles = [
            {"time": int(k[0] / 1000), "open": k[1], "high": k[2], "low": k[3], "close": k[4], "volume": k[5]}
            for k in ohlcv if k[1] is not None
        ]
        return candles
    except Exception as e:
        return {"error": str(e)}, 502


@app.get("/")
async def get_index():
    return FileResponse(os.path.join(_BOT_DIR, "index.html"))


@app.get("/platform")
@app.get("/platform.html")
@app.get("/panel")
async def get_platform():
    return FileResponse(os.path.join(_BOT_DIR, "platform.html"))


@app.get("/platform2")
@app.get("/platform2.html")
async def get_platform2():
    return FileResponse(os.path.join(_BOT_DIR, "platform2.html"))


# ── Notification helpers ──────────────────────────────────────────────────────

def _sync_send_telegram(cfg: dict, text: str, photo_path: Optional[str]):
    token = cfg["tg_token"]
    chat_id = cfg["tg_chat_id"]
    if photo_path and os.path.exists(photo_path):
        url = f"https://api.telegram.org/bot{token}/sendPhoto"
        with open(photo_path, "rb") as img:
            requests.post(
                url,
                data={"chat_id": chat_id, "caption": text[:1024], "parse_mode": ""},
                files={"photo": img},
                timeout=15,
            )
    else:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        requests.post(
            url,
            data={"chat_id": chat_id, "text": text[:4096]},
            timeout=15,
        )
    print("[telegram] message sent")


async def send_telegram_raw(cfg: dict, text: str, photo_path: Optional[str] = None):
    """Send a Telegram message without blocking the asyncio event loop."""
    if not cfg.get("tg_enabled") or not cfg.get("tg_token") or not cfg.get("tg_chat_id"):
        return
    try:
        await asyncio.to_thread(_sync_send_telegram, cfg, text, photo_path)
    except Exception as e:
        print(f"[telegram] error: {e}")


def _sync_send_twitter(cfg: dict, text: str):
    client = tweepy.Client(
        consumer_key=cfg["tw_api_key"],
        consumer_secret=cfg["tw_api_secret"],
        access_token=cfg["tw_access_token"],
        access_token_secret=cfg["tw_access_secret"],
    )
    client.create_tweet(text=text[:280])
    print("[twitter] tweet sent")


async def send_twitter(cfg: dict, text: str):
    """Post a tweet without blocking the asyncio event loop."""
    if not cfg.get("tw_enabled") or not cfg.get("tw_api_key"):
        return
    try:
        await asyncio.to_thread(_sync_send_twitter, cfg, text)
    except Exception as e:
        print(f"[twitter] error: {e}")


def build_referral_block(cfg: dict) -> str:
    """Build the referral links footer block."""
    lines = ["", "Trade via our partner exchanges:"]
    for key, label in [
        ("ref_binance", "Binance"),
        ("ref_mexc", "MEXC"),
        ("ref_gate", "Gate.io"),
        ("ref_kucoin", "KuCoin"),
        ("ref_okx", "OKX"),
    ]:
        link = cfg.get(key, "")
        if link:
            lines.append(f"  {label}: {link}")
    if cfg.get("promo_enabled") and cfg.get("promo_link"):
        lines.append(f"\nPowered by PROPTREX: {cfg['promo_link']}")
    return "\n".join(lines)


def should_add_referral(cfg: dict) -> bool:
    """Check if the current signal should carry the referral block."""
    if not cfg.get("ref_enabled", True):
        return False
    n = int(cfg.get("ref_every_n_signals", 3))
    if n <= 0:
        return False
    return (_signal_counter % n) == 0


def build_caption(signal, social=None, portfolio_lines=None, orderbook=None) -> str:
    why_lines = "\n".join([f"• {x}" for x in signal.why_lines])
    invalidation_lines = "\n".join([f"• {x}" for x in signal.invalidation_lines])

    social_block = ""
    if social is not None:
        social_why = "\n".join([f"• {x}" for x in social.why_lines])
        social_block = (
            f"SOCIAL\n"
            f"X: {social.x_sentiment} | Square: {social.square_bias}\n"
            f"Conviction: {social.social_conviction:.1f}/100 | Hype Risk: {social.hype_risk:.1f}/100\n"
            f"{social_why}\n\n"
        )

    ob_block = ""
    if orderbook is not None:
        ob_block = (
            f"ORDERBOOK\n"
            f"Dominant: {orderbook.dominant_side} | Imbalance: {orderbook.bid_ask_imbalance:.1f}%\n"
            f"Score: {orderbook.score:.1f}/100 | Spread: {orderbook.spread_pct:.4f}%\n\n"
        )

    pm_block = ""
    if portfolio_lines:
        pm_block = "POSITION PLAN\n" + "\n".join([f"• {x}" for x in portfolio_lines]) + "\n\n"

    caption = (
        f"🚀 PROPTREX | {signal.symbol} | {signal.side} | {signal.status}\n\n"
        f"Exchange: {signal.exchange} | TF: {signal.timeframe}\n"
        f"Opp Score: {signal.opportunity_score:.1f} | Why Score: {signal.why_enter_score:.1f}\n\n"
        f"ENTRY ZONE\n{signal.entry_low} – {signal.entry_high}\n\n"
        f"STOP LOSS\n{signal.stop_loss}\n\n"
        f"TARGETS\n"
        f"TP1: {signal.tp1} [{signal.tp1_tf}]\n"
        f"TP2: {signal.tp2} [{signal.tp2_tf}]\n"
        f"TP3: {signal.tp3} [{signal.tp3_tf}]\n\n"
        f"ENTRY QUALITY: {signal.entry_freshness}\n"
        f"HOLD: {signal.expected_hold} | Expiry: {signal.expiry_minutes}min\n\n"
        f"WHY THIS TRADE?\n{why_lines}\n\n"
        f"FLOW\n"
        f"Buyers: {signal.buyer_dominance:.1f}% | Sellers: {signal.seller_pressure:.1f}%\n"
        f"Whale: {signal.meta.get('whale_action','NONE')} ({signal.whale_strength:.1f}/100)\n\n"
        f"STRUCTURE: {signal.structure_bias} | {signal.high_type}/{signal.low_type}\n\n"
        f"{social_block}"
        f"{ob_block}"
        f"{pm_block}"
        f"INVALIDATION\n{invalidation_lines}"
    )
    return caption


def classify_context(data) -> str:
    df = compute_indicators(data.df)
    if len(df) < 220:
        return "NEUTRAL"
    bias, _, _ = classify_structure(df)
    if bias == "BULLISH":
        return "BULLISH"
    if bias == "BEARISH":
        return "BEARISH"
    return "NEUTRAL"


# ── Shared engine state ───────────────────────────────────────────────────────

_engine: Optional[Dict] = None  # initialized on startup


def _build_engine(cfg: dict) -> dict:
    exchanges = [e.strip() for e in cfg.get("exchanges", "binance,mexc,gateio,kucoin,okx").split(",") if e.strip()]
    scanner = MultiExchangeScanner(exchanges=exchanges)
    store = JsonStateStore(path=os.path.join(_BOT_DIR, "signal_state.json"))
    position_book = OpenPositionBook()
    lifecycle = SignalLifecycle()
    social_engine = SocialEngine()
    pm = PortfolioManager(account_size=10000, risk_per_trade_pct=1.0)
    x_adapter = XAdapter(bearer_token="")
    square_adapter = BinanceSquareAdapter()

    ob_engines: Dict[str, OrderbookEngine] = {}
    for ex in exchanges:
        try:
            ob_engines[ex] = OrderbookEngine(exchange_name=ex)
        except Exception:
            pass

    return {
        "scanner": scanner,
        "store": store,
        "position_book": position_book,
        "lifecycle": lifecycle,
        "social_engine": social_engine,
        "pm": pm,
        "x_adapter": x_adapter,
        "square_adapter": square_adapter,
        "ob_engines": ob_engines,
        "exchanges": exchanges,
    }


# ── Lifecycle update checker ─────────────────────────────────────────────────


# ── Main scan loop ────────────────────────────────────────────────────────────

async def scan_loop():
    global _engine, _signal_counter, _scan_counter

    # Small delay on startup to let FastAPI finish
    await asyncio.sleep(5)

    cfg = load_config()
    _engine = _build_engine(cfg)
    print(f"[scanner] engine started — exchanges: {_engine['exchanges']}")

    while True:
        cfg = load_config()  # re-read each cycle so admin changes take effect

        # Rebuild engine if exchanges changed
        new_exchanges = [e.strip() for e in cfg.get("exchanges", "binance,mexc,gateio,kucoin,okx").split(",") if e.strip()]
        if new_exchanges != _engine["exchanges"]:
            print(f"[scanner] exchanges changed → rebuilding engine: {new_exchanges}")
            _engine = _build_engine(cfg)

        try:
            await _do_scan(cfg)
        except Exception as e:
            print(f"[scanner] error: {e}")

        _scan_counter += 1
        # Prune stale dedup entries every 10 scans
        if _scan_counter % 10 == 0:
            pruned = _engine["store"].prune()
            if pruned:
                print(f"[store] pruned {pruned} stale dedup entries")

        interval = int(cfg.get("scan_interval_seconds", 60))
        await asyncio.sleep(interval)


def _compute_signals_sync(cfg: dict, engine: dict) -> dict:
    """
    All blocking I/O (ccxt OHLCV fetches, orderbook, social scraping, charting)
    runs here in a thread pool so the asyncio event loop stays responsive.
    Returns a dict with lifecycle updates + scored signal items ready to dispatch.
    """
    scanner: MultiExchangeScanner = engine["scanner"]
    store = engine["store"]
    position_book: OpenPositionBook = engine["position_book"]
    social_engine_obj: SocialEngine = engine["social_engine"]
    pm: PortfolioManager = engine["pm"]
    x_adapter: XAdapter = engine["x_adapter"]
    square_adapter: BinanceSquareAdapter = engine["square_adapter"]
    ob_engines: Dict[str, OrderbookEngine] = engine["ob_engines"]

    timeframe = cfg.get("timeframe", "15m")
    if timeframe not in _VALID_TIMEFRAMES:
        print(f"[scanner] invalid timeframe '{timeframe}', falling back to 15m")
        timeframe = "15m"
    candle_limit = max(500, int(cfg.get("candle_limit", 500)))
    chart_bars = 140
    min_volume_usd = float(cfg.get("min_volume_usd", 10000))
    signals_per_scan = int(cfg.get("signals_per_scan", 5))
    exclude_raw = cfg.get("exclude_symbols", "USDC,USDT,FDUSD,TUSD,DAI,BUSD,USD1,XUSD,PAXG,XAUT,EUR,GBP,GOLD")
    exclude_set = {s.strip().upper() for s in exclude_raw.split(",") if s.strip()}
    dynamic_scan = bool(cfg.get("dynamic_scan", True))
    top_n = int(cfg.get("top_n_symbols", 100))
    dynamic_min_vol = 500000
    context_symbols = ["BTC/USDT", "ETH/USDT", "SOL/USDT"]
    square_enabled = bool(cfg.get("square_enabled", True))

    # ── Lifecycle data collection ────────────────────────────────────────────
    lifecycle_updates = []
    lifecycle: SignalLifecycle = engine["lifecycle"]
    for symbol, pos in list(position_book.positions.items()):
        if pos.status in ("TP3_HIT", "INVALIDATED"):
            position_book.close(symbol)
            continue
        data = scanner.fetch_first_available(symbol, timeframe=timeframe, limit=candle_limit)
        if data is None or data.df.empty:
            continue
        current_price = float(data.df.iloc[-1]["close"])
        update = lifecycle.evaluate(
            side=pos.side, current_price=current_price,
            entry_low=pos.entry_low, entry_high=pos.entry_high,
            stop_loss=pos.stop_loss, tp1=pos.tp1, tp2=pos.tp2, tp3=pos.tp3,
        )
        if update.status != "ACTIVE" and update.status != pos.status:
            pos.status = update.status
            lifecycle_updates.append({
                "symbol": symbol, "status": update.status,
                "action": update.action, "price": current_price,
                "lines": update.lines,
            })
            if update.status in ("TP3_HIT", "INVALIDATED"):
                position_book.close(symbol)

    # ── Macro context ────────────────────────────────────────────────────────
    print(f"[scanner] fetching macro context ({len(context_symbols)} symbols)...")
    context_map: Dict[str, str] = {}
    for s in context_symbols:
        cdata = scanner.fetch_first_available(s, timeframe=timeframe, limit=candle_limit)
        if cdata is None:
            continue
        context_map[s] = classify_context(cdata)
    print(f"[scanner] context: {context_map}")

    # ── Universe fetch ───────────────────────────────────────────────────────
    if dynamic_scan:
        print(f"[scanner] dynamic scan: top_n={top_n}, min_vol={dynamic_min_vol}")
        universe_pre = scanner.fetch_universe_dynamic(
            timeframe=timeframe, limit=candle_limit,
            min_volume_usd=dynamic_min_vol, top_n=top_n,
        )
    else:
        default_symbols = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "XRP/USDT", "DOGE/USDT",
                           "AVAX/USDT", "OP/USDT", "LINK/USDT", "ADA/USDT", "WIF/USDT"]
        print(f"[scanner] static scan: {len(default_symbols)} symbols")
        universe_pre = scanner.fetch_universe(default_symbols, timeframe=timeframe, limit=candle_limit)

    print(f"[scanner] universe fetched: {len(universe_pre)} items")

    for item in universe_pre:
        if item.symbol not in context_map:
            context_map[item.symbol] = classify_context(item)

    # ── Score ────────────────────────────────────────────────────────────────
    min_score = float(cfg.get("min_opportunity_score", 55.0))
    ranked = []
    skipped_vol = 0
    skipped_score = 0
    below_min = 0

    for item in universe_pre:
        if item.df.empty:
            continue
        vol_avg = float((item.df["close"] * item.df["volume"]).tail(10).mean())
        if vol_avg < min_volume_usd:
            skipped_vol += 1
            continue
        signal = build_signal(
            symbol=item.symbol, exchange=item.exchange,
            timeframe=item.timeframe, df=item.df, context_map=context_map,
        )
        if signal is None:
            skipped_score += 1
            continue
        base_symbol = signal.symbol.split("/")[0].upper()
        if base_symbol in exclude_set:
            skipped_score += 1
            continue
        if signal.opportunity_score < min_score:
            below_min += 1
            continue
        ranked.append((signal.opportunity_score, signal, item.df))

    ranked.sort(key=lambda x: x[0], reverse=True)
    top = ranked[:signals_per_scan]
    print(f"[scanner] scored — skipped_vol={skipped_vol} no_signal={skipped_score} "
          f"below_min={below_min} qualified={len(ranked)} sending_top={len(top)}")

    # ── Momentum Discovery Layer ──────────────────────────────────────────────
    # Catch coins like TAG/UP that pump before structure forms (vol spike + price move)
    momentum_events = []
    for item in universe_pre:
        if item.df.empty or len(item.df) < 22:
            continue
        df_m = item.df
        last_m = df_m.iloc[-1]
        vol_ma = float(df_m["volume"].tail(20).mean())
        if vol_ma <= 0:
            continue
        vol_mult = float(last_m["volume"]) / vol_ma
        open_p = float(last_m["open"])
        close_p = float(last_m["close"])
        if open_p <= 0:
            continue
        price_chg = (close_p - open_p) / open_p * 100
        if vol_mult >= 4.0 and abs(price_chg) >= 3.0:
            base_sym = item.symbol.split("/")[0].upper()
            if base_sym not in exclude_set:
                momentum_events.append({
                    "symbol": item.symbol,
                    "exchange": item.exchange,
                    "vol_mult": round(vol_mult, 1),
                    "price_change_pct": round(price_chg, 2),
                    "price": round(close_p, 6),
                    "side": "LONG" if price_chg > 0 else "SHORT",
                    "phase": "MOMENTUM_SPIKE",
                })
                print(f"[momentum] {item.symbol} vol×{vol_mult:.1f} chg{price_chg:+.1f}%")

    # ── Structural TP matrix for top signals (multi-TF klines) ───────────────
    _STRUCTURAL_TFS = ["5m", "15m", "1h", "4h"]
    top_enriched = []
    for score, signal, df in top:
        klines_by_tf: dict = {}
        for tf_s in _STRUCTURAL_TFS:
            try:
                udata = scanner.fetch_first_available(signal.symbol, timeframe=tf_s, limit=300)
                if udata is not None and not udata.df.empty:
                    udf = compute_indicators(udata.df)
                    if len(udf) >= 30:
                        klines_by_tf[tf_s] = udf
            except Exception as e:
                print(f"[tp_matrix] {signal.symbol} {tf_s}: {e}")

        current_price = float(df.iloc[-1]["close"])
        # ATR: klines_by_tf'den al (df ham olduğundan "atr" sütunu olmayabilir)
        _atr_df = (klines_by_tf.get(signal.timeframe)
                   or klines_by_tf.get("15m")
                   or klines_by_tf.get("5m")
                   or (next(iter(klines_by_tf.values()), None) if klines_by_tf else None))
        if _atr_df is not None:
            atr_val = float(_atr_df.iloc[-1]["atr"])
        else:
            # Fallback: stop-loss mesafesinden tahmin
            atr_val = abs(signal.entry_high - signal.stop_loss) / 1.5 or 1.0
        try:
            tp_list, struct_stop = build_tp_matrix_structural(
                signal.side, current_price, klines_by_tf, atr_val
            )
        except Exception as e:
            print(f"[tp_matrix_structural] {signal.symbol}: {e}")
            tp_list = [
                {"price": signal.tp1, "source": "ATR_FALLBACK", "tf": timeframe, "label": "TP1",
                 "rr": 1.5, "distance_pct": "N/A", "hit": False, "hit_at": None, "snapped": False},
                {"price": signal.tp2, "source": "ATR_FALLBACK", "tf": timeframe, "label": "TP2",
                 "rr": 2.5, "distance_pct": "N/A", "hit": False, "hit_at": None, "snapped": False},
                {"price": signal.tp3, "source": "ATR_FALLBACK", "tf": timeframe, "label": "TP3",
                 "rr": 4.0, "distance_pct": "N/A", "hit": False, "hit_at": None, "snapped": False},
            ]
            struct_stop = signal.stop_loss
        tp_matrix = {"tps": tp_list, "stop": struct_stop}

        # Entry distance from current price to entry zone midpoint
        entry_mid = (signal.entry_low + signal.entry_high) / 2
        entry_dist_pct = round(abs(current_price - entry_mid) / entry_mid * 100, 2) if entry_mid else 0

        top_enriched.append((score, signal, df, tp_matrix, entry_dist_pct))

    # ── Enrich top signals ────────────────────────────────────────────────────
    items_out = []
    for _, signal, df, tp_matrix, entry_dist_pct in top_enriched:
        key = f"{signal.exchange}:{signal.symbol}:{signal.side}"

        x_posts = x_adapter.texts_for_symbol(signal.symbol, limit=20) if x_adapter.is_enabled() else []
        square_posts = square_adapter.texts_for_symbol(signal.symbol, limit=20) if square_enabled else []

        social = social_engine_obj.analyze(
            symbol=signal.symbol, x_posts=x_posts, square_posts=square_posts,
            historical_x_count=max(10, len(x_posts)),
            historical_square_count=max(10, len(square_posts)),
        )

        ob_signal = None
        ob_engine = ob_engines.get(signal.exchange)
        if ob_engine is not None:
            ob_signal = ob_engine.analyze(signal.symbol, depth=20)

        plan = pm.build_plan(
            symbol=signal.symbol, side=signal.side,
            entry_low=signal.entry_low, entry_high=signal.entry_high,
            stop_loss=signal.stop_loss, tp1=signal.tp1, tp2=signal.tp2, tp3=signal.tp3,
        )
        portfolio_lines = pm.plan_to_lines(plan)

        payload_hash = hashlib.md5(
            (
                f"{signal.symbol}|{signal.side}|"
                f"{round(signal.entry_low, 4)}|{round(signal.entry_high, 4)}|"
                f"{round(signal.stop_loss, 4)}"
            ).encode("utf-8")
        ).hexdigest()

        dedup_minutes = 180
        if not store.allow(key, payload_hash, cooldown_minutes=dedup_minutes):
            print(f"[scanner] dedup skip: {signal.symbol} {signal.side}")
            continue

        caption = build_caption(signal, social=social,
                                portfolio_lines=portfolio_lines, orderbook=ob_signal)

        chart_path = None
        try:
            chart_path = render_signal_chart(
                df=df, signal=signal,
                title=f"{signal.symbol} | {signal.side} | {signal.opportunity_score:.1f}",
                bars=chart_bars,
            )
        except Exception as e:
            print(f"[chart] error: {e}")

        items_out.append({
            "key": key,
            "signal": signal,
            "social": social,
            "ob_signal": ob_signal,
            "caption": caption,
            "chart_path": chart_path,
            "tp_matrix": tp_matrix,
            "entry_dist_pct": entry_dist_pct,
        })

    return {
        "lifecycle_updates": lifecycle_updates,
        "items": items_out,
        "momentum_events": momentum_events,
        "scan_stats": {
            "scanned": len(universe_pre),
            "skipped_vol": skipped_vol,
            "no_signal": skipped_score,
            "below_min": below_min,
            "qualified": len(ranked),
            "sent": len(items_out),
            "momentum_spikes": len(momentum_events),
        },
    }


async def _do_scan(cfg: dict):
    global _signal_counter, _engine

    print(f"[scanner] scan started at {datetime.now().strftime('%H:%M:%S')}")
    t0 = time.time()

    # Run all blocking I/O in a thread — event loop stays free
    result = await asyncio.to_thread(_compute_signals_sync, cfg, _engine)

    elapsed = time.time() - t0
    print(f"[scanner] compute done in {elapsed:.1f}s — "
          f"{len(result['items'])} new signals, {len(result['lifecycle_updates'])} lifecycle updates")

    # ── Dispatch lifecycle updates ────────────────────────────────────────────
    for upd in result["lifecycle_updates"]:
        detail = "\n".join([f"• {x}" for x in upd["lines"]])
        msg = (
            f"📊 LIFECYCLE UPDATE | {upd['symbol']}\n\n"
            f"Status: {upd['status']}\n"
            f"Action: {upd['action']}\n\n"
            f"{detail}\n\n"
            f"Current Price: {upd['price']}"
        )
        await send_telegram_raw(cfg, msg)
        await manager.broadcast({
            "type": "lifecycle_update",
            "symbol": upd["symbol"], "status": upd["status"],
            "action": upd["action"], "price": upd["price"],
            "ts": int(time.time() * 1000),
        })

    # ── Dispatch momentum spikes (panel only, no Telegram) ───────────────────
    for spike in result.get("momentum_events", []):
        await manager.broadcast({
            "type": "momentum_spike",
            "ts": int(time.time() * 1000),
            **spike,
        })

    # ── Dispatch new signals ──────────────────────────────────────────────────
    for item in result["items"]:
        signal = item["signal"]
        social = item["social"]
        ob_signal = item["ob_signal"]
        caption = item["caption"]
        chart_path = item["chart_path"]
        tp_matrix = item.get("tp_matrix", {})
        entry_dist_pct = item.get("entry_dist_pct", 0)

        _engine["position_book"].open_from_signal(signal)
        _signal_counter += 1

        add_ref = should_add_referral(cfg)
        full_caption = caption + "\n" + build_referral_block(cfg) if add_ref else caption

        await send_telegram_raw(cfg, full_caption, photo_path=chart_path)
        await send_twitter(cfg, full_caption[:280])

        if chart_path and os.path.exists(chart_path):
            try:
                os.remove(chart_path)
            except Exception:
                pass

        signal_payload = {
            "type": "signal",
            "ts": int(time.time() * 1000),
            "time": datetime.now().strftime("%H:%M:%S"),
            "symbol": signal.symbol,
            "exchange": signal.exchange,
            "side": signal.side,
            "status": signal.status,
            "opportunity_score": round(signal.opportunity_score, 1),
            "why_enter_score": round(signal.why_enter_score, 1),
            "entry_low": signal.entry_low,
            "entry_high": signal.entry_high,
            "stop_loss": signal.stop_loss,
            "tp1": signal.tp1,
            "tp2": signal.tp2,
            "tp3": signal.tp3,
            "tp1_tf": signal.tp1_tf,
            "tp2_tf": signal.tp2_tf,
            "tp3_tf": signal.tp3_tf,
            "timeframe": signal.timeframe,
            "structure_bias": signal.structure_bias,
            "buyer_dominance": round(signal.buyer_dominance, 1),
            "seller_pressure": round(signal.seller_pressure, 1),
            "whale_strength": round(signal.whale_strength, 1),
            "entry_freshness": signal.entry_freshness,
            "expected_hold": signal.expected_hold,
            "social_conviction": round(social.social_conviction, 1) if social else 0,
            "x_sentiment": social.x_sentiment if social else "N/A",
            "ob_score": round(ob_signal.score, 1) if ob_signal else 0,
            "ob_dominant": ob_signal.dominant_side if ob_signal else "N/A",
            "why_lines": signal.why_lines,
            "invalidation_lines": signal.invalidation_lines,
            "signal_number": _signal_counter,
            "has_referral": add_ref,
            "phase": "SIGNAL",
            "tp_matrix": tp_matrix,
            "entry_dist_pct": entry_dist_pct,
            "accumulation_score": signal.meta.get("accumulation_score", 0),
            "whale_action": signal.meta.get("whale_action", "NONE"),
            "whale_mult": signal.meta.get("whale_mult", 0),
            "liquidity_event": signal.meta.get("liquidity_event", "NONE"),
            "momentum_bias": signal.meta.get("momentum_bias", "NEUTRAL"),
        }

        recent_signals.insert(0, signal_payload)
        if len(recent_signals) > 50:
            recent_signals.pop()

        await manager.broadcast(signal_payload)
        print(f"[signal #{_signal_counter}] {signal.symbol} {signal.side} "
              f"score={signal.opportunity_score:.1f} ref={add_ref}")

    # ── Scan summary (panel stats update) ────────────────────────────────────
    stats = result.get("scan_stats", {})
    await manager.broadcast({
        "type": "scan_summary",
        "ts": int(time.time() * 1000),
        "time": datetime.now().strftime("%H:%M:%S"),
        "scanned": stats.get("scanned", 0),
        "qualified": stats.get("qualified", 0),
        "sent": stats.get("sent", 0),
        "momentum_spikes": stats.get("momentum_spikes", 0),
        "total_signals": _signal_counter,
    })


# ── WebSocket ─────────────────────────────────────────────────────────────────

@app.websocket("/ws/events")
async def ws_events(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        # Send recent signals on connect
        await websocket.send_json({
            "type": "hello",
            "ts": int(time.time() * 1000),
            "recent_signals": recent_signals[:20],
        })
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)


# ── Startup ───────────────────────────────────────────────────────────────────

@app.on_event("startup")
async def startup_event():
    asyncio.create_task(scan_loop())


if __name__ == "__main__":
    uvicorn.run("bot_server:app", host="0.0.0.0", port=8000, reload=False)
