from __future__ import annotations

import argparse
import asyncio
import hashlib
import os
from typing import Dict

import yaml
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application

from adapters.exchanges import MultiExchangeScanner
from charting import render_signal_chart
from scoring import build_signal, compute_indicators, classify_structure
from social_engine import SocialEngine
from state_store import JsonStateStore
from redis_state import RedisStateStore
from portfolio_manager import PortfolioManager
from x_adapter import XAdapter
from square_adapter import BinanceSquareAdapter
from orderbook_engine import OrderbookEngine
from open_positions import OpenPositionBook
from signal_lifecycle import SignalLifecycle


def load_config(path: str) -> Dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def build_state_store(cfg):
    backend = cfg.get("state", {}).get("backend", "json")
    if backend == "redis":
        return RedisStateStore(url=cfg["state"]["redis_url"])
    return JsonStateStore(path=cfg.get("state", {}).get("json_path", "signal_state.json"))


def build_buttons(cfg: Dict, symbol: str) -> InlineKeyboardMarkup:
    pair_for_tv = symbol.replace("/", "")
    pair_for_gecko = symbol.split("/")[0].lower()

    referral = cfg["routing"]["referral_links"]
    website = cfg["routing"]["proptrex_url"]

    keyboard = [
        [
            InlineKeyboardButton("Binance", url=referral["binance"]),
            InlineKeyboardButton("MEXC", url=referral["mexc"]),
            InlineKeyboardButton("Gate.io", url=referral["gateio"]),
            InlineKeyboardButton("KuCoin", url=referral["kucoin"]),
        ],
        [
            InlineKeyboardButton("TradingView", url=f"https://www.tradingview.com/symbols/{pair_for_tv}/"),
            InlineKeyboardButton("CoinGecko", url=f"https://www.coingecko.com/en/coins/{pair_for_gecko}"),
            InlineKeyboardButton("Website", url=website),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)


def build_caption(signal, social=None, portfolio_lines=None, orderbook=None) -> str:
    why_lines = "\n".join([f"• {x}" for x in signal.why_lines])
    invalidation_lines = "\n".join([f"• {x}" for x in signal.invalidation_lines])

    # Social block
    social_block = ""
    if social is not None:
        social_why = "\n".join([f"• {x}" for x in social.why_lines])
        social_block = (
            f"SOCIAL\n"
            f"X: {social.x_sentiment} | Square: {social.square_bias}\n"
            f"Conviction: {social.social_conviction:.1f}/100 | Hype Risk: {social.hype_risk:.1f}/100\n"
            f"{social_why}\n\n"
        )

    # Orderbook block
    ob_block = ""
    if orderbook is not None:
        ob_block = (
            f"ORDERBOOK\n"
            f"Dominant: {orderbook.dominant_side} | Imbalance: {orderbook.bid_ask_imbalance:.1f}%\n"
            f"Score: {orderbook.score:.1f}/100 | Spread: {orderbook.spread_pct:.4f}%\n\n"
        )

    # Portfolio block
    pm_block = ""
    if portfolio_lines:
        pm_block = "POSITION PLAN\n" + "\n".join([f"• {x}" for x in portfolio_lines]) + "\n\n"

    # Critical trade info first so it never gets cut off
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
    return caption[:1024]


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


async def send_lifecycle_updates(
    app: Application,
    chat_id: str,
    position_book: OpenPositionBook,
    lifecycle: SignalLifecycle,
    scanner: MultiExchangeScanner,
    timeframe: str,
    candle_limit: int,
):
    """Check open positions and send lifecycle updates if a TP or stop is hit."""
    for symbol, pos in list(position_book.positions.items()):
        if pos.status in ("TP3_HIT", "INVALIDATED"):
            position_book.close(symbol)
            continue

        data = scanner.fetch_first_available(symbol, timeframe=timeframe, limit=candle_limit)
        if data is None or data.df.empty:
            continue

        current_price = float(data.df.iloc[-1]["close"])
        update = lifecycle.evaluate(
            side=pos.side,
            current_price=current_price,
            entry_low=pos.entry_low,
            entry_high=pos.entry_high,
            stop_loss=pos.stop_loss,
            tp1=pos.tp1,
            tp2=pos.tp2,
            tp3=pos.tp3,
        )

        if update.status != "ACTIVE" and update.status != pos.status:
            pos.status = update.status
            detail = "\n".join([f"• {x}" for x in update.lines])
            msg = (
                f"📊 LIFECYCLE UPDATE | {symbol}\n\n"
                f"Status: {update.status}\n"
                f"Action: {update.action}\n\n"
                f"{detail}\n\n"
                f"Current Price: {current_price}"
            )
            try:
                await app.bot.send_message(chat_id=chat_id, text=msg)
            except Exception:
                pass

            if update.status in ("TP3_HIT", "INVALIDATED"):
                position_book.close(symbol)


async def process_once(
    app: Application,
    cfg: Dict,
    scanner: MultiExchangeScanner,
    store,
    position_book: OpenPositionBook,
    lifecycle: SignalLifecycle,
    social_engine: SocialEngine,
    pm: PortfolioManager,
    x_adapter: XAdapter,
    square_adapter: BinanceSquareAdapter,
    ob_engines: Dict[str, OrderbookEngine],
):
    context_symbols = cfg["market"]["context_symbols"]
    timeframe = cfg["runtime"]["default_timeframe"]
    candle_limit = cfg["runtime"]["candle_limit"]
    chart_bars = cfg["runtime"]["chart_bars"]
    min_volume_usd = cfg["runtime"]["min_volume_usd"]
    chat_id = cfg["telegram"]["chat_id"]
    dynamic_scan = cfg["market"].get("dynamic_scan", False)
    top_n = cfg["market"].get("top_n_symbols", 100)
    dynamic_min_vol = cfg["market"].get("dynamic_min_volume_usd", 500000)

    # Lifecycle updates for open positions
    await send_lifecycle_updates(
        app, chat_id, position_book, lifecycle, scanner, timeframe, candle_limit
    )

    # Macro context map: BTC/ETH/SOL genel yönü (60% ağırlık)
    context_map: Dict[str, str] = {}
    for s in context_symbols:
        cdata = scanner.fetch_first_available(s, timeframe=timeframe, limit=candle_limit)
        if cdata is None:
            continue
        context_map[s] = classify_context(cdata)

    # Universe çekme:
    # dynamic_scan=true → tüm borsalardaki USDT paritelerini 24h hacme göre sırala
    # dynamic_scan=false → config'deki sabit symbol listesi
    if dynamic_scan:
        universe_pre = scanner.fetch_universe_dynamic(
            timeframe=timeframe,
            limit=candle_limit,
            min_volume_usd=dynamic_min_vol,
            top_n=top_n,
        )
    else:
        symbols = cfg["market"]["symbols"]
        universe_pre = scanner.fetch_universe(symbols, timeframe=timeframe, limit=candle_limit)

    # Her coin'in kendi yapısını context_map'e ekle (macro'yu ezmez)
    for item in universe_pre:
        if item.symbol not in context_map:
            context_map[item.symbol] = classify_context(item)

    ranked = []

    for item in universe_pre:
        if item.df.empty:
            continue
        # Use 10-candle rolling average for volume filter (not just last candle)
        vol_avg = float((item.df["close"] * item.df["volume"]).tail(10).mean())
        if vol_avg < min_volume_usd:
            continue

        signal = build_signal(
            symbol=item.symbol,
            exchange=item.exchange,
            timeframe=item.timeframe,
            df=item.df,
            context_map=context_map,
        )
        if signal is None:
            continue
        # Skip WATCHLIST entirely — only send TRADEABLE
        if signal.status != "TRADEABLE":
            continue
        ranked.append((signal.opportunity_score, signal, item.df))

    ranked.sort(key=lambda x: x[0], reverse=True)
    top = ranked[:3]

    x_limit = int(cfg.get("social", {}).get("x_limit", 20))
    square_limit = int(cfg.get("social", {}).get("square_limit", 20))
    square_enabled = cfg.get("social", {}).get("square_enabled", True)

    for _, signal, df in top:
        key = f"{signal.exchange}:{signal.symbol}:{signal.side}:{signal.status}"

        x_posts = x_adapter.texts_for_symbol(signal.symbol, limit=x_limit) if x_adapter.is_enabled() else []
        square_posts = square_adapter.texts_for_symbol(signal.symbol, limit=square_limit) if square_enabled else []

        social = social_engine.analyze(
            symbol=signal.symbol,
            x_posts=x_posts,
            square_posts=square_posts,
            historical_x_count=max(10, len(x_posts)),
            historical_square_count=max(10, len(square_posts)),
        )

        ob_signal = None
        ob_engine = ob_engines.get(signal.exchange)
        if ob_engine is not None:
            ob_signal = ob_engine.analyze(signal.symbol, depth=20)

        plan = pm.build_plan(
            symbol=signal.symbol,
            side=signal.side,
            entry_low=signal.entry_low,
            entry_high=signal.entry_high,
            stop_loss=signal.stop_loss,
            tp1=signal.tp1,
            tp2=signal.tp2,
            tp3=signal.tp3,
        )
        portfolio_lines = pm.plan_to_lines(plan)

        payload_hash = hashlib.md5(
            (
                f"{signal.symbol}|{signal.side}|{signal.status}|"
                f"{signal.entry_low}|{signal.entry_high}|{signal.stop_loss}|"
                f"{signal.tp1}|{signal.tp2}|{signal.tp3}|"
                f"{signal.opportunity_score:.2f}|{signal.why_enter_score:.2f}|"
                f"{social.social_conviction:.2f}|"
                f"{ob_signal.score if ob_signal else 0.0}"
            ).encode("utf-8")
        ).hexdigest()

        if not store.allow(key, payload_hash, cooldown_minutes=cfg["runtime"]["dedup_cooldown_minutes"]):
            continue

        if hasattr(store, "upsert_metadata"):
            store.upsert_metadata(
                key=key,
                symbol=signal.symbol,
                exchange=signal.exchange,
                side=signal.side,
                status=signal.status,
            )

        # Track in position book
        position_book.open_from_signal(signal)

        buttons = build_buttons(cfg, signal.symbol)

        if cfg["runtime"]["enable_chart"]:
            chart_path = render_signal_chart(
                df=df,
                signal=signal,
                title=f"{signal.symbol} | {signal.side} | {signal.opportunity_score:.1f}",
                bars=chart_bars,
            )
            try:
                with open(chart_path, "rb") as f:
                    await app.bot.send_photo(
                        chat_id=chat_id,
                        photo=f,
                        caption=build_caption(
                            signal,
                            social=social,
                            portfolio_lines=portfolio_lines,
                            orderbook=ob_signal,
                        ),
                        reply_markup=buttons,
                    )
            finally:
                if os.path.exists(chart_path):
                    os.remove(chart_path)
        else:
            await app.bot.send_message(
                chat_id=chat_id,
                text=build_caption(
                    signal,
                    social=social,
                    portfolio_lines=portfolio_lines,
                    orderbook=ob_signal,
                ),
                reply_markup=buttons,
            )


async def runner(cfg: Dict):
    exchanges = cfg["market"]["exchanges"]
    scanner = MultiExchangeScanner(exchanges=exchanges)
    store = build_state_store(cfg)
    position_book = OpenPositionBook()
    lifecycle = SignalLifecycle()
    social_engine = SocialEngine()
    pm = PortfolioManager(
        account_size=float(cfg.get("portfolio", {}).get("account_size", 10000)),
        risk_per_trade_pct=float(cfg.get("portfolio", {}).get("risk_per_trade_pct", 1.0)),
    )
    x_adapter = XAdapter(bearer_token=cfg.get("social", {}).get("x_bearer_token", ""))
    square_adapter = BinanceSquareAdapter()

    # Build one OrderbookEngine per exchange — not inside loop
    ob_engines: Dict[str, OrderbookEngine] = {}
    for ex in exchanges:
        try:
            ob_engines[ex] = OrderbookEngine(exchange_name=ex)
        except Exception:
            pass

    application = Application.builder().token(cfg["telegram"]["token"]).build()

    while True:
        try:
            await process_once(
                application, cfg, scanner, store,
                position_book, lifecycle,
                social_engine, pm, x_adapter, square_adapter, ob_engines,
            )
        except Exception as e:
            print(f"[scan error] {e}")
        await asyncio.sleep(cfg["runtime"]["scan_interval_seconds"])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True, help="Path to config.yaml")
    args = parser.parse_args()

    cfg = load_config(args.config)
    asyncio.run(runner(cfg))


if __name__ == "__main__":
    main()
