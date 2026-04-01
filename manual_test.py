"""
🧪 REAL TRADE TEST — Places an actual order on Delta Exchange testnet
   so you can verify it on https://demo.delta.exchange dashboard.

   This script BYPASSES the PAPER_TRADE flag intentionally.
"""
import os
import sys
import time
import json
import hmac
import hashlib
import logging
import requests
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger(__name__)

API_KEY    = os.getenv("DELTA_API_KEY", "")
API_SECRET = os.getenv("DELTA_API_SECRET", "")
BASE_URL   = os.getenv("DELTA_BASE_URL", "https://cdn-ind.testnet.deltaex.org")

session = requests.Session()
session.headers.update({
    "Content-Type": "application/json",
    "User-Agent": "python-options-bot-test",
})


def sign_request(method, path, query_string="", payload=""):
    ts = str(int(time.time()))
    message = method + ts + path + query_string + payload
    sig = hmac.new(
        API_SECRET.encode(), message.encode(), hashlib.sha256
    ).hexdigest()
    return {
        "api-key": API_KEY,
        "timestamp": ts,
        "signature": sig,
        "Content-Type": "application/json",
        "User-Agent": "python-options-bot-test",
    }


def run_real_test():
    print("\n" + "█" * 60)
    print("  🧪 REAL TRADE TEST — Delta Exchange Testnet")
    print(f"  API: {BASE_URL}")
    print("█" * 60)

    if not API_KEY or not API_SECRET:
        print("❌ No API_KEY or API_SECRET in .env — cannot proceed.")
        return

    if "testnet" not in BASE_URL and "demo" not in BASE_URL:
        print("🔴 WARNING: This does NOT look like a testnet URL!")
        print(f"   URL: {BASE_URL}")
        confirm = input("   Type 'YES' to continue with LIVE trading: ")
        if confirm != "YES":
            print("Aborted.")
            return

    # ── Step 1: Check connection + spot price
    print("\n── Step 1: Checking API connection...")
    try:
        resp = session.get(f"{BASE_URL}/v2/tickers/BTCUSD", timeout=10)
        data = resp.json()
        if not data.get("success"):
            print(f"❌ Ticker API failed: {data}")
            return
        spot = float(data["result"].get("spot_price", 0) or
                     data["result"].get("mark_price", 0))
        print(f"✅ Connected. BTC Spot: ${spot:,.2f}")
    except Exception as e:
        print(f"❌ Connection error: {e}")
        return

    # ── Step 2: Check wallet balance
    print("\n── Step 2: Checking wallet balance...")
    try:
        path = "/v2/wallet/balances"
        headers = sign_request("GET", path)
        resp = session.get(f"{BASE_URL}{path}", headers=headers, timeout=10)
        data = resp.json()
        if data.get("success"):
            balances = data.get("result", [])
            for b in balances:
                bal = float(b.get("balance", 0) or 0)
                if bal > 0:
                    print(f"   💰 {b.get('asset_symbol', '?')}: {bal:.4f} "
                          f"(available: {float(b.get('available_balance', 0) or 0):.4f})")
        else:
            print(f"   ⚠️ Wallet API response: {data.get('error', data)}")
    except Exception as e:
        print(f"   ⚠️ Could not fetch wallet: {e}")

    # ── Step 3: Fetch options chain
    print("\n── Step 3: Fetching options chain...")
    try:
        params = {
            "contract_types": "call_options,put_options",
            "underlying_asset_symbols": "BTC",
        }
        resp = session.get(f"{BASE_URL}/v2/tickers", params=params, timeout=15)
        data = resp.json()
        if not data.get("success"):
            print(f"❌ Options chain failed: {data}")
            return

        options = []
        for t in data.get("result", []):
            ct = t.get("contract_type", "")
            if ct not in ("call_options", "put_options"):
                continue
            quotes = t.get("quotes") or {}
            bid = float(quotes.get("best_bid", 0) or 0)
            ask = float(quotes.get("best_ask", 0) or 0)
            mark = float(t.get("mark_price", 0) or 0)
            strike = float(t.get("strike_price", 0) or 0)
            options.append({
                "symbol": t.get("symbol", ""),
                "product_id": t.get("product_id", 0),
                "strike": strike,
                "type": "call" if "call" in ct else "put",
                "mark_price": mark,
                "bid": bid,
                "ask": ask,
                "oi": int(float(t.get("oi", 0) or 0)),
            })

        print(f"   Found {len(options)} option contracts")
        if not options:
            print("❌ No options available on testnet right now.")
            return
    except Exception as e:
        print(f"❌ Chain error: {e}")
        return

    # ── Step 4: Pick a contract (cheapest call near ATM)
    print("\n── Step 4: Selecting a contract...")
    calls = [o for o in options if o["type"] == "call" and o["mark_price"] > 0]
    if not calls:
        print("❌ No tradeable call options found.")
        # Try puts
        calls = [o for o in options if o["type"] == "put" and o["mark_price"] > 0]
        if not calls:
            print("❌ No tradeable options at all.")
            return

    # Sort by distance from spot
    calls.sort(key=lambda c: abs(c["strike"] - spot))
    chosen = calls[0]

    print(f"   📋 Symbol:     {chosen['symbol']}")
    print(f"   📋 Product ID: {chosen['product_id']}")
    print(f"   📋 Strike:     ${chosen['strike']:,.0f}")
    print(f"   📋 Type:       {chosen['type']}")
    print(f"   📋 Mark Price: ${chosen['mark_price']:.4f}")
    print(f"   📋 Bid/Ask:    ${chosen['bid']:.4f} / ${chosen['ask']:.4f}")

    # ── Step 5: Place REAL buy order
    print(f"\n── Step 5: Placing REAL BUY order (1 contract)...")
    try:
        path = "/v2/orders"
        body = {
            "product_id": chosen["product_id"],
            "side": "buy",
            "size": 1,
            "order_type": "market_order",
        }
        payload = json.dumps(body)
        headers = sign_request("POST", path, "", payload)
        resp = session.post(f"{BASE_URL}{path}", data=payload, headers=headers, timeout=15)
        result = resp.json()

        if result.get("success"):
            order_id = result.get("result", {}).get("id", "?")
            print(f"   ✅ ORDER PLACED! Order ID: {order_id}")
            print(f"   👉 Check it on your Delta demo dashboard!")
            print(f"      https://demo.delta.exchange")
        else:
            error = result.get("error", result)
            print(f"   ❌ Order REJECTED: {error}")
            print(f"   Full response: {json.dumps(result, indent=2)}")
            return
    except Exception as e:
        print(f"   ❌ Order error: {e}")
        return

    # ── Step 6: Wait and close
    print(f"\n── Step 6: Waiting 15 seconds before closing...")
    for i in range(15, 0, -1):
        print(f"   ⏳ {i}s...", end="\r")
        time.sleep(1)

    print(f"\n── Step 7: Closing position (SELL 1 contract)...")
    try:
        body = {
            "product_id": chosen["product_id"],
            "side": "sell",
            "size": 1,
            "order_type": "market_order",
        }
        payload = json.dumps(body)
        headers = sign_request("POST", path, "", payload)
        resp = session.post(f"{BASE_URL}{path}", data=payload, headers=headers, timeout=15)
        result = resp.json()

        if result.get("success"):
            print(f"   ✅ POSITION CLOSED! Order ID: {result.get('result', {}).get('id', '?')}")
        else:
            print(f"   ❌ Close rejected: {result.get('error', result)}")
            print(f"   (You may need to close it manually on the dashboard)")
    except Exception as e:
        print(f"   ❌ Close error: {e}")

    print("\n" + "█" * 60)
    print("  🎉 TEST COMPLETE — Check your Delta demo dashboard")
    print(f"     https://demo.delta.exchange")
    print("█" * 60 + "\n")


if __name__ == "__main__":
    run_real_test()
