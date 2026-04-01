import os
import time
import logging
from options_bot import DeltaOptionsAPI, StrikePriceSelector, OptionsRiskManager

# Setup basic logging to see what's happening
logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger(__name__)

def run_manual_test():
    print("\n" + "═"*60)
    print(" 🧪 MANUAL TRADE TEST — checking API & execution")
    print("═"*60)

    api = DeltaOptionsAPI()
    risk = OptionsRiskManager(15000) # Mock capital

    # 1. Get Spot Price
    try:
        tickers = api._get("/v2/tickers/BTCUSD")
        spot = float(tickers["result"]["mark_price"])
        print(f"✅ Connection OK. BTC Spot: ${spot:,.2f}")
    except Exception as e:
        print(f"❌ Connection Failed: {e}")
        return

    # 2. Get Options Chain
    print("🔍 Fetching options chain...")
    chain = api.get_options_chain("BTC")
    if not chain:
        print("❌ Could not fetch options chain.")
        return

    # 3. Find a cheap Call option (slightly OTM)
    target_strike = round(spot * 1.01 / 100) * 100
    print(f"🎯 Target Strike: ${target_strike:,.0f} (Call)")
    
    best = api.find_best_contract(chain, target_strike, "call")
    if not best:
        print("❌ No suitable contract found.")
        return

    symbol = best["symbol"]
    product_id = best["product_id"]
    price = best["mark_price"]
    print(f"💎 Selected: {symbol} (Product ID: {product_id})")
    print(f"💰 Mark Price: ${price:.4f}")

    # 4. Place a small test order (1 unit)
    # This will be a PAPER order if PAPER_TRADE=true in .env
    print(f"🚀 Placing test BUY order for 1 unit...")
    order = api.place_options_order(product_id, "buy", 1, symbol)
    
    if order:
        print(f"✅ Order Placed Successfully! (ID: {order.get('result', {}).get('id', 'PAPER')})")
        
        print("\n⏳ Waiting 10 seconds before closing...")
        time.sleep(10)

        # 5. Close the position (Sell it back)
        print(f"🔄 Closing position (Selling {symbol})...")
        close_order = api.place_options_order(product_id, "sell", 1, symbol)
        
        if close_order:
            print(f"✅ Position Closed Successfully!")
        else:
            print("❌ Failed to close position manually.")
    else:
        print("❌ Failed to place order.")

    print("\n" + "═"*60)
    print(" 🎉 TEST COMPLETE")
    print("═"*60 + "\n")

if __name__ == "__main__":
    run_manual_test()
