import asyncio
import pandas as pd
from adapters.exchanges import MultiExchangeScanner
from scoring import build_signal

async def main():
    scanner = MultiExchangeScanner(exchanges=["binance"])
    default_symbols = ["BTC/USDT", "ETH/USDT"]
    universe = await scanner.fetch_universe_async(default_symbols, timeframe="15m", limit=300)
    
    context_map = {"BTC/USDT": "NEUTRAL", "ETH/USDT": "NEUTRAL"}
    
    for item in universe:
        print(f"--- Testing {item.symbol} ---")
        if item.df.empty:
            print("DF is empty")
            continue
        print(f"DF length: {len(item.df)}")
        try:
            signal = build_signal(
                symbol=item.symbol, 
                exchange=item.exchange,
                timeframe=item.timeframe, 
                df=item.df, 
                context_map=context_map
            )
            if signal is None:
                print("Signal returned None")
            else:
                print(f"Success! Status: {signal.status}, Side: {signal.side}")
        except Exception as e:
            print(f"Exception during scoring: {e}")

if __name__ == "__main__":
    asyncio.run(main())
