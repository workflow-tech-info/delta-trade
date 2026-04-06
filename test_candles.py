"""Quick diagnostic: verify candle API works with the correct resolution format."""
import requests, time, os
from dotenv import load_dotenv
load_dotenv()

BASE_URL = os.getenv("DELTA_BASE_URL", "https://cdn-ind.testnet.deltaex.org")
print(f"API: {BASE_URL}\n")

# Test spot price
print("── Spot Price ──")
try:
    r = requests.get(f"{BASE_URL}/v2/tickers/BTCUSD", timeout=10).json()
    spot = float(r["result"].get("spot_price", 0) or r["result"].get("mark_price", 0))
    print(f"✅ BTC Spot: ${spot:,.2f}\n")
except Exception as e:
    print(f"❌ Spot error: {e}\n")

# Test candles with CORRECT format
for res in ["1m", "5m", "15m", "1h"]:
    print(f"── Candles ({res}) ──")
    try:
        end_ts = int(time.time())
        mins = {"1m": 1, "5m": 5, "15m": 15, "1h": 60}[res]
        start_ts = end_ts - (50 * mins * 60)
        resp = requests.get(
            f"{BASE_URL}/v2/history/candles",
            params={"resolution": res, "symbol": "BTCUSD",
                    "start": start_ts, "end": end_ts},
            timeout=15).json()
        if resp.get("success"):
            candles = resp.get("result", [])
            print(f"✅ Got {len(candles)} candles")
            if candles:
                c = candles[-1]
                print(f"   Latest: O={c.get('open')} H={c.get('high')} L={c.get('low')} C={c.get('close')}")
        else:
            print(f"❌ API error: {resp.get('error')}")
    except Exception as e:
        print(f"❌ Error: {e}")
    print()

# Test with OLD broken format to demonstrate the bug
print("── BUG DEMO: Old format (resolution='15') ──")
try:
    resp = requests.get(
        f"{BASE_URL}/v2/history/candles",
        params={"resolution": "15", "symbol": "BTCUSD"},
        timeout=15).json()
    candles = resp.get("result", [])
    print(f"Result: {len(candles)} candles ← THIS IS WHY THE BOT NEVER TRADED!")
except Exception as e:
    print(f"Error: {e}")
