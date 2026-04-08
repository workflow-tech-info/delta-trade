"""
╔══════════════════════════════════════════════════════════════════════╗
║          DELTA EXCHANGE — CRYPTO OPTIONS BOT v5.0                   ║
║          "One Kill a Day" — Full Conviction Day Trading Engine       ║
║                                                                      ║
║  4-Layer Decision System:                                            ║
║  L1: Daily Bias (1W + 1D) → BULLISH / BEARISH / CHOPPY             ║
║  L2: Higher TF Confirmation (4h, 1h, 15m)                          ║
║  L3: Entry Trigger (5m) — 27 patterns + indicators                  ║
║  L4: Full Wallet Execution — 100% × 50x leverage                   ║
╚══════════════════════════════════════════════════════════════════════╝
"""

import requests, numpy as np, time, json, logging, os, hmac, hashlib, pytz
from datetime import datetime, timedelta
from dataclasses import dataclass, asdict
from typing import List, Any
from dotenv import load_dotenv
from pathlib import Path

load_dotenv()

# ══════════════════════════════════════════════════════════════
# CONFIGURATION
# ══════════════════════════════════════════════════════════════
API_KEY        = os.getenv("DELTA_API_KEY", "")
API_SECRET     = os.getenv("DELTA_API_SECRET", "")
BASE_URL       = os.getenv("DELTA_BASE_URL", "https://cdn-ind.testnet.deltaex.org")
FORCE_PAPER_TRADE = False
PAPER_TRADE    = True if FORCE_PAPER_TRADE else os.getenv("PAPER_TRADE", "true").lower() == "true"

# v5.0 — One Kill a Day settings
OPTIONS_MIN_SCORE = 70            # High bar for blockbuster entries
LEVERAGE          = int(os.getenv("LEVERAGE", "200"))  # 200x for SELLING options
OPTIONS_RISK_PCT  = 1.0           # 100% of wallet
MAX_POSITIONS     = 1
MAX_TRADES_PER_DAY = 1            # One kill a day
TRAILING_STOP_PCT = 0.50
CYCLE_INTERVAL    = 120           # 2 minutes for 5m precision
BASE_UNDERLYING   = os.getenv("BASE_UNDERLYING", "BTC")
MAX_HOLD_HOURS    = 8             # Day trading

# Timeframes
PRIMARY_TIMEFRAME  = "5m"
CONFIRM_TIMEFRAMES = ["15m", "1h", "4h"]
MACRO_TIMEFRAMES   = ["1d", "1w"]
CANDLE_LIMIT       = 50

# Storage
DATA_DIR = Path(os.getenv("BOT_DATA_DIR", "./bot_data"))
DATA_DIR.mkdir(exist_ok=True)
POSITIONS_FILE     = DATA_DIR / "positions.json"
TRADE_HISTORY_FILE = DATA_DIR / "trade_history.json"

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[logging.FileHandler("options_bot_log.txt", encoding="utf-8"), logging.StreamHandler()])
log = logging.getLogger(__name__)

def _save_json(fp, data):
    try:
        with open(fp, "w") as f: json.dump(data, f, indent=2, default=str)
    except: pass

def _load_json(fp, default=None):
    if default is None: default = []
    try:
        if fp.exists():
            with open(fp, "r") as f: return json.load(f)
    except: pass
    return default

@dataclass
class OptionContract:
    symbol: str; underlying: str; expiry: str; strike: float
    option_type: str; premium: float; delta: float; implied_vol: float
    open_interest: int; bid: float; ask: float; spread_pct: float
    product_id: int = 0; expiry_datetime: str = ""

@dataclass
class OptionsPosition:
    contract: Any; side: str; quantity: int
    entry_premium: float; entry_time: str
    stop_premium: float; target_premium: float
    order_id: str = ""; exit_premium: float = 0.0
    exit_reason: str = ""; pnl: float = 0.0
    status: str = "open"; leverage: int = LEVERAGE
    peak_premium: float = 0.0

# ══════════════════════════════════════════════════════════════
# DELTA API HANDLER
# ══════════════════════════════════════════════════════════════
class DeltaAPI:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json", "User-Agent": "bot-v5.0"})

    def _sign(self, method, path, query_string="", payload=""):
        ts = str(int(time.time()))
        message = method + ts + path + query_string + payload
        sig = hmac.new(API_SECRET.encode(), message.encode(), hashlib.sha256).hexdigest()
        return {"api-key": API_KEY, "timestamp": ts, "signature": sig, "Content-Type": "application/json"}

    def get_spot_price(self, symbol="BTCUSD") -> float:
        try:
            r = self.session.get(f"{BASE_URL}/v2/tickers/{symbol}", timeout=10).json()
            if r.get("success"):
                return float(r["result"].get("spot_price") or r["result"].get("mark_price") or 0)
        except: pass
        return 0.0

    def get_candles(self, symbol="BTCUSD", resolution="5m", limit=50) -> list:
        try:
            end = int(time.time())
            res_map = {'s': 1, 'm': 60, 'h': 3600, 'd': 86400, 'w': 604800, 'M': 2592000}  # M = ~30 days
            unit = resolution[-1]; val = int(resolution[:-1])
            start = end - (val * res_map.get(unit, 60) * limit)
            r = self.session.get(f"{BASE_URL}/v2/history/candles",
                params={"resolution": resolution, "symbol": symbol, "start": start, "end": end}, timeout=10).json()
            if r.get("success"): return r.get("result", [])
        except: pass
        return []

    def get_wallet_balance(self) -> dict:
        if not API_KEY: return {"available": 0}
        try:
            path = "/v2/wallet/balances"
            headers = self._sign("GET", path)
            r = self.session.get(f"{BASE_URL}{path}", headers=headers, timeout=10).json()
            if r.get("success"):
                for b in r.get("result", []):
                    if float(b.get("balance", 0)) > 0:
                        return {"available": float(b.get("available_balance", 0)), "asset": b.get("asset_symbol")}
        except: pass
        return {"available": 0}

    def set_leverage(self, product_id, leverage=20):
        """Set leverage, auto-retry with exchange max if rejected."""
        if PAPER_TRADE:
            log.info(f"    📝 PAPER: Leverage set to {leverage}x")
            return {"success": True, "result": {"leverage": leverage}, "actual_leverage": leverage}
        try:
            path = f"/v2/products/{product_id}/orders/leverage"
            body = json.dumps({"leverage": leverage})
            headers = self._sign("POST", path, "", body)
            r = self.session.post(f"{BASE_URL}{path}", data=body, headers=headers, timeout=10).json()
            if r.get("success"):
                actual = float(r['result'].get('leverage', leverage))
                log.info(f"    ⚙️  Leverage confirmed: {actual}x")
                r["actual_leverage"] = actual
                return r
            else:
                # Auto-retry with max leverage if exceeded
                err = r.get("error", {})
                if err.get("code") == "max_leverage_exceeded":
                    max_lev = float(err.get("context", {}).get("max_leverage", 1))
                    log.warning(f"    ⚠️  {leverage}x exceeds max. Retrying with {max_lev}x...")
                    body2 = json.dumps({"leverage": max_lev})
                    headers2 = self._sign("POST", path, "", body2)
                    r2 = self.session.post(f"{BASE_URL}{path}", data=body2, headers=headers2, timeout=10).json()
                    if r2.get("success"):
                        log.info(f"    ⚙️  Leverage confirmed: {max_lev}x (exchange max)")
                        r2["actual_leverage"] = max_lev
                        return r2
                log.warning(f"    ⚠️  Leverage failed: {r}")
                r["actual_leverage"] = 1
                return r
        except Exception as e:
            log.warning(f"    ⚠️  Leverage error: {e}")
            return {"actual_leverage": 1}

    def get_options_chain(self, underlying="BTC") -> list:
        try:
            r = self.session.get(f"{BASE_URL}/v2/tickers",
                params={"contract_types": "call_options,put_options", "underlying_asset_symbols": underlying}, timeout=10).json()
            if r.get("success") and r.get("result"):
                options = []
                for t in r.get("result", []):
                    try:
                        q = t.get("quotes") or {}; g = t.get("greeks") or {}
                        bid = float(q.get("best_bid") or 0); ask = float(q.get("best_ask") or 0)
                        strike = float(t.get("strike_price") or 0); mark = float(t.get("mark_price") or 0)
                        spread_pct = (ask-bid)/ask if ask > 0 else 1.0
                        options.append({
                            "symbol": t.get("symbol"), "product_id": t.get("product_id"),
                            "strike": strike, "type": "call" if "call" in t.get("contract_type","") else "put",
                            "mark_price": mark, "bid": bid, "ask": ask, "spread_pct": spread_pct,
                            "tradeable": bid > 0 and spread_pct < 0.20,
                            "delta": float(g.get("delta") or 0), "gamma": float(g.get("gamma") or 0),
                            "theta": float(g.get("theta") or 0), "vega": float(g.get("vega") or 0),
                            "iv": float(t.get("mark_vol") or 0)
                        })
                    except: continue
                return options
        except: pass
        return []

    def place_order(self, product_id, side, size, symbol=""):
        if PAPER_TRADE:
            log.info(f"    📝 PAPER: {side.upper()} {size}x {symbol}")
            return {"success": True, "result": {"id": f"paper_{int(time.time())}"}}
        try:
            path = "/v2/orders"
            body = json.dumps({"product_id": product_id, "side": side, "size": size, "order_type": "market_order"})
            headers = self._sign("POST", path, "", body)
            r = self.session.post(f"{BASE_URL}{path}", data=body, headers=headers, timeout=10).json()
            return r
        except Exception as e:
            log.error(f"    💥 Order error: {e}")
            return {}

    def get_option_premium(self, symbol):
        try:
            r = self.session.get(f"{BASE_URL}/v2/tickers/{symbol}", timeout=10).json()
            if r.get("success"): return {"mark_price": float(r["result"].get("mark_price") or 0)}
        except: pass
        return {}

# ══════════════════════════════════════════════════════════════
# PATTERN DETECTOR — 27 Patterns (22 Candlestick + 5 Chart)
# ══════════════════════════════════════════════════════════════
class PatternDetector:
    def analyze(self, candles):
        if not candles or len(candles) < 5: return 0, [], "insufficient_data"
        p = []
        # Single candle
        p += self._doji(candles); p += self._marubozu(candles)
        p += self._hammer(candles); p += self._spinning_top(candles); p += self._belt_hold(candles)
        # 2-candle
        p += self._engulfing(candles); p += self._harami(candles)
        p += self._piercing_dark_cloud(candles); p += self._tweezer(candles)
        # 3-candle
        p += self._morning_evening_star(candles); p += self._three_soldiers_crows(candles)
        p += self._three_inside(candles); p += self._abandoned_baby(candles)
        # Chart patterns (need more data)
        if len(candles) >= 15: p += self._double_top_bottom(candles)
        if len(candles) >= 10: p += self._flag(candles)
        if len(candles) >= 20: p += self._head_shoulders(candles)
        if len(candles) >= 15: p += self._triangle(candles)

        total = sum(x["score"] for x in p)
        bull = sum(1 for x in p if x["bias"]=="bullish")
        bear = sum(1 for x in p if x["bias"]=="bearish")
        bias = "bullish" if bull > bear else "bearish" if bear > bull else "neutral"
        return total, p, bias

    def _b(self,c): return abs(float(c["close"])-float(c["open"]))
    def _uw(self,c): return float(c["high"])-max(float(c["open"]),float(c["close"]))
    def _lw(self,c): return min(float(c["open"]),float(c["close"]))-float(c["low"])
    def _bull(self,c): return float(c["close"])>float(c["open"])
    def _rng(self,c): return float(c["high"])-float(c["low"])

    def _doji(self, candles):
        p=[]; c=candles[-1]; b=self._b(c); r=self._rng(c)
        if r==0: return p
        if b/r < 0.10:
            uw=self._uw(c); lw=self._lw(c)
            if lw > uw*2: p.append({"name":"🟢 Dragonfly Doji","bias":"bullish","score":5})
            elif uw > lw*2: p.append({"name":"🔴 Gravestone Doji","bias":"bearish","score":-5})
            elif abs(uw-lw) < r*0.1: p.append({"name":"⚪ Long-Legged Doji","bias":"neutral","score":0})
            else: p.append({"name":"⚪ Doji","bias":"neutral","score":0})
        return p

    def _marubozu(self, candles):
        p=[]; c=candles[-1]; b=self._b(c); r=self._rng(c)
        if r==0: return p
        if self._uw(c)/r < 0.02 and self._lw(c)/r < 0.02:
            if self._bull(c): p.append({"name":"🟢 Bullish Marubozu","bias":"bullish","score":8})
            else: p.append({"name":"🔴 Bearish Marubozu","bias":"bearish","score":-8})
        return p

    def _hammer(self, candles):
        p=[]; c=candles[-1]; b=self._b(c); lw=self._lw(c); uw=self._uw(c)
        if b==0 or self._rng(c)==0: return p
        closes=[float(x["close"]) for x in candles[-6:-1]]
        down = len(closes)>=4 and closes[-1]<closes[0]
        if lw>=b*2 and uw<b*0.5:
            if down: p.append({"name":"🟢 Hammer","bias":"bullish","score":8})
            else: p.append({"name":"🔴 Hanging Man","bias":"bearish","score":-5})
        if uw>=b*2 and lw<b*0.5:
            if down: p.append({"name":"🟢 Inverted Hammer","bias":"bullish","score":6})
            else: p.append({"name":"🔴 Shooting Star","bias":"bearish","score":-8})
        return p

    def _spinning_top(self, candles):
        p=[]; c=candles[-1]; b=self._b(c); r=self._rng(c)
        if r==0: return p
        if 0.10 < b/r < 0.35 and abs(self._uw(c)-self._lw(c))<r*0.3:
            p.append({"name":"⚪ Spinning Top","bias":"neutral","score":0})
        return p

    def _belt_hold(self, candles):
        p=[]; c=candles[-1]; b=self._b(c); r=self._rng(c)
        if r==0 or b==0: return p
        closes=[float(x["close"]) for x in candles[-6:-1]]
        down = len(closes)>=4 and closes[-1]<closes[0]
        if self._bull(c) and self._lw(c)/r<0.02 and b/r>0.6 and down:
            p.append({"name":"🟢 Bullish Belt Hold","bias":"bullish","score":5})
        if not self._bull(c) and self._uw(c)/r<0.02 and b/r>0.6 and not down:
            p.append({"name":"🔴 Bearish Belt Hold","bias":"bearish","score":-5})
        return p

    def _engulfing(self, candles):
        p=[]; c1,c2=candles[-2],candles[-1]
        if not self._bull(c1) and self._bull(c2) and float(c2["close"])>float(c1["open"]) and float(c2["open"])<float(c1["close"]):
            p.append({"name":"🟢 Bullish Engulfing","bias":"bullish","score":10})
        if self._bull(c1) and not self._bull(c2) and float(c2["close"])<float(c1["open"]) and float(c2["open"])>float(c1["close"]):
            p.append({"name":"🔴 Bearish Engulfing","bias":"bearish","score":-10})
        return p

    def _harami(self, candles):
        p=[]; c1,c2=candles[-2],candles[-1]
        h1,l1=max(float(c1["open"]),float(c1["close"])),min(float(c1["open"]),float(c1["close"]))
        h2,l2=max(float(c2["open"]),float(c2["close"])),min(float(c2["open"]),float(c2["close"]))
        if h2<h1 and l2>l1:
            if not self._bull(c1) and self._bull(c2): p.append({"name":"🟢 Bullish Harami","bias":"bullish","score":6})
            elif self._bull(c1) and not self._bull(c2): p.append({"name":"🔴 Bearish Harami","bias":"bearish","score":-6})
        return p

    def _piercing_dark_cloud(self, candles):
        p=[]; c1,c2=candles[-2],candles[-1]; b1=self._b(c1)
        if b1==0: return p
        mid1=(float(c1["open"])+float(c1["close"]))/2
        if not self._bull(c1) and self._bull(c2) and float(c2["open"])<float(c1["close"]) and float(c2["close"])>mid1:
            p.append({"name":"🟢 Piercing Line","bias":"bullish","score":10})
        if self._bull(c1) and not self._bull(c2) and float(c2["open"])>float(c1["close"]) and float(c2["close"])<mid1:
            p.append({"name":"🔴 Dark Cloud Cover","bias":"bearish","score":-10})
        return p

    def _tweezer(self, candles):
        p=[]; c1,c2=candles[-2],candles[-1]
        tol=self._rng(c1)*0.005 if self._rng(c1)>0 else 0.01
        if abs(float(c1["low"])-float(c2["low"]))<tol and not self._bull(c1) and self._bull(c2):
            p.append({"name":"🟢 Tweezer Bottom","bias":"bullish","score":8})
        if abs(float(c1["high"])-float(c2["high"]))<tol and self._bull(c1) and not self._bull(c2):
            p.append({"name":"🔴 Tweezer Top","bias":"bearish","score":-8})
        return p

    def _morning_evening_star(self, candles):
        p=[]
        if len(candles)<3: return p
        c1,c2,c3=candles[-3],candles[-2],candles[-1]
        b1,b2,b3=self._b(c1),self._b(c2),self._b(c3)
        if b1==0: return p
        if not self._bull(c1) and b2<b1*0.3 and self._bull(c3) and b3>b1*0.5:
            p.append({"name":"🟢 Morning Star","bias":"bullish","score":12})
        if self._bull(c1) and b2<b1*0.3 and not self._bull(c3) and b3>b1*0.5:
            p.append({"name":"🔴 Evening Star","bias":"bearish","score":-12})
        return p

    def _three_soldiers_crows(self, candles):
        p=[]
        if len(candles)<3: return p
        l3=candles[-3:]
        if all(self._bull(c) for c in l3) and float(l3[1]["close"])>float(l3[0]["close"]) and float(l3[2]["close"])>float(l3[1]["close"]):
            p.append({"name":"🟢 Three White Soldiers","bias":"bullish","score":12})
        if all(not self._bull(c) for c in l3) and float(l3[1]["close"])<float(l3[0]["close"]) and float(l3[2]["close"])<float(l3[1]["close"]):
            p.append({"name":"🔴 Three Black Crows","bias":"bearish","score":-12})
        return p

    def _three_inside(self, candles):
        p=[]
        if len(candles)<3: return p
        c1,c2,c3=candles[-3],candles[-2],candles[-1]
        h1,l1=max(float(c1["open"]),float(c1["close"])),min(float(c1["open"]),float(c1["close"]))
        h2,l2=max(float(c2["open"]),float(c2["close"])),min(float(c2["open"]),float(c2["close"]))
        if h2<h1 and l2>l1:  # c2 inside c1 (harami condition)
            if not self._bull(c1) and self._bull(c2) and float(c3["close"])>h1:
                p.append({"name":"🟢 Three Inside Up","bias":"bullish","score":10})
            if self._bull(c1) and not self._bull(c2) and float(c3["close"])<l1:
                p.append({"name":"🔴 Three Inside Down","bias":"bearish","score":-10})
        return p

    def _abandoned_baby(self, candles):
        p=[]
        if len(candles)<3: return p
        c1,c2,c3=candles[-3],candles[-2],candles[-1]
        b2=self._b(c2); r2=self._rng(c2)
        if r2==0 or b2/r2>0.10: return p  # c2 must be doji
        if not self._bull(c1) and self._bull(c3) and float(c2["high"])<float(c1["low"]) and float(c2["high"])<float(c3["low"]):
            p.append({"name":"🟢 Bullish Abandoned Baby","bias":"bullish","score":15})
        if self._bull(c1) and not self._bull(c3) and float(c2["low"])>float(c1["high"]) and float(c2["low"])>float(c3["high"]):
            p.append({"name":"🔴 Bearish Abandoned Baby","bias":"bearish","score":-15})
        return p

    def _double_top_bottom(self, candles):
        p=[]; highs=[float(c["high"]) for c in candles[-15:]]; lows=[float(c["low"]) for c in candles[-15:]]
        hs=sorted(enumerate(highs),key=lambda x:x[1],reverse=True)
        if len(hs)>=2 and abs(hs[0][0]-hs[1][0])>=3 and abs(hs[0][1]-hs[1][1])/hs[0][1]<0.01:
            p.append({"name":"🔴 Double Top","bias":"bearish","score":-10})
        ls=sorted(enumerate(lows),key=lambda x:x[1])
        if len(ls)>=2 and abs(ls[0][0]-ls[1][0])>=3 and abs(ls[0][1]-ls[1][1])/max(ls[0][1],1)<0.01:
            p.append({"name":"🟢 Double Bottom","bias":"bullish","score":10})
        return p

    def _flag(self, candles):
        p=[]
        if len(candles)<10: return p
        pole=candles[-10:-5]; flag=candles[-5:]
        pm=float(pole[-1]["close"])-float(pole[0]["open"])
        fr=max(float(c["high"]) for c in flag)-min(float(c["low"]) for c in flag)
        pr=abs(pm)
        if pr>0 and fr<pr*0.4:
            if pm>0: p.append({"name":"🟢 Bullish Flag","bias":"bullish","score":8})
            else: p.append({"name":"🔴 Bearish Flag","bias":"bearish","score":-8})
        return p

    def _head_shoulders(self, candles):
        p=[]
        if len(candles)<20: return p
        highs=[float(c["high"]) for c in candles[-20:]]
        # Find 3 peaks: left shoulder, head, right shoulder
        peaks=[]
        for i in range(2, len(highs)-2):
            if highs[i]>highs[i-1] and highs[i]>highs[i-2] and highs[i]>highs[i+1] and highs[i]>highs[i+2]:
                peaks.append((i, highs[i]))
        if len(peaks)>=3:
            ls,head,rs=peaks[-3],peaks[-2],peaks[-1]
            if head[1]>ls[1] and head[1]>rs[1] and abs(ls[1]-rs[1])/ls[1]<0.02:
                p.append({"name":"🔴 Head & Shoulders","bias":"bearish","score":-12})
        # Inverse
        lows=[float(c["low"]) for c in candles[-20:]]
        troughs=[]
        for i in range(2, len(lows)-2):
            if lows[i]<lows[i-1] and lows[i]<lows[i-2] and lows[i]<lows[i+1] and lows[i]<lows[i+2]:
                troughs.append((i, lows[i]))
        if len(troughs)>=3:
            ls,head,rs=troughs[-3],troughs[-2],troughs[-1]
            if head[1]<ls[1] and head[1]<rs[1] and abs(ls[1]-rs[1])/ls[1]<0.02:
                p.append({"name":"🟢 Inverse Head & Shoulders","bias":"bullish","score":12})
        return p

    def _triangle(self, candles):
        p=[]
        if len(candles)<15: return p
        highs=[float(c["high"]) for c in candles[-15:]]
        lows=[float(c["low"]) for c in candles[-15:]]
        h_slope=(highs[-1]-highs[0])/len(highs)
        l_slope=(lows[-1]-lows[0])/len(lows)
        last_close=float(candles[-1]["close"])
        if h_slope<0 and l_slope>0:
            # Check breakout direction
            upper_bound=highs[-1]; lower_bound=lows[-1]
            if last_close > upper_bound:
                p.append({"name":"🟢 Triangle Breakout UP","bias":"bullish","score":12})
            elif last_close < lower_bound:
                p.append({"name":"🔴 Triangle Breakout DOWN","bias":"bearish","score":-12})
            else:
                p.append({"name":"⚪ Symmetrical Triangle (pending breakout)","bias":"neutral","score":0})
        elif h_slope<0 and l_slope<=0:  # Descending triangle
            p.append({"name":"🔴 Descending Triangle","bias":"bearish","score":-8})
        elif h_slope>=0 and l_slope>0:  # Ascending triangle
            p.append({"name":"🟢 Ascending Triangle","bias":"bullish","score":8})
        return p

# ══════════════════════════════════════════════════════════════
# SIGNAL ENGINE v5.0 — 4-Layer Decision System
# ══════════════════════════════════════════════════════════════
class SignalEngine:
    def __init__(self, api: DeltaAPI):
        self.api = api
        self.patterns = PatternDetector()
        self.daily_bias = "UNKNOWN"
        self.bias_last_updated = None
        self.bias_date = None  # Track which date the bias is for
        self.key_levels = {}   # Support & resistance levels
        self.daily_report = [] # Full analysis report

    def _calc_fibonacci(self, candles):
        """Calculate Fibonacci retracement levels from swing high/low."""
        highs = [float(c["high"]) for c in candles]
        lows = [float(c["low"]) for c in candles]
        swing_high = max(highs); swing_low = min(lows)
        diff = swing_high - swing_low
        if diff == 0: return {}
        return {
            "swing_high": swing_high, "swing_low": swing_low,
            "0.0": swing_high, "0.236": swing_high - diff * 0.236,
            "0.382": swing_high - diff * 0.382, "0.500": swing_high - diff * 0.5,
            "0.618": swing_high - diff * 0.618, "0.786": swing_high - diff * 0.786,
            "1.0": swing_low
        }

    def _find_key_levels(self, candles):
        """Detect Support & Resistance levels from swing highs/lows."""
        if len(candles) < 10: return [], []
        highs = [float(c["high"]) for c in candles]
        lows = [float(c["low"]) for c in candles]
        supports = []; resistances = []
        for i in range(2, len(highs)-2):
            if highs[i] > highs[i-1] and highs[i] > highs[i-2] and highs[i] > highs[i+1] and highs[i] > highs[i+2]:
                resistances.append(highs[i])
            if lows[i] < lows[i-1] and lows[i] < lows[i-2] and lows[i] < lows[i+1] and lows[i] < lows[i+2]:
                supports.append(lows[i])
        def cluster(levels):
            if not levels: return []
            levels.sort(); result = [levels[0]]
            for l in levels[1:]:
                if abs(l - result[-1]) / result[-1] > 0.005: result.append(l)
            return result[-5:]
        return cluster(supports), cluster(resistances)

    def _deep_tf_analysis(self, candles, tf_label):
        """Run FULL analysis: indicators + ALL patterns + key levels + Fibonacci + breakouts."""
        if not candles or len(candles) < 5:
            return {"direction": "NEUTRAL", "score": 50, "details": [], "patterns": [],
                    "single_patterns": [], "combo_patterns": [], "chart_patterns": [],
                    "supports": [], "resistances": [], "fib": {}, "pattern_score": 0,
                    "near_support": False, "near_resistance": False, "near_fib": None}

        closes = [float(c["close"]) for c in candles]
        current = closes[-1]
        score = 50; details = []

        # ── INDICATORS ──
        rsi = self._rsi(closes)
        if rsi < 30: score += 18; details.append(f"⚡ RSI {rsi:.0f} — Prey is EXHAUSTED, oversold! (+18)")
        elif rsi < 40: score += 10; details.append(f"📉 RSI {rsi:.0f} — Prey weakening, buyers gathering (+10)")
        elif rsi > 70: score -= 18; details.append(f"🔥 RSI {rsi:.0f} — Prey OVERHEATED, overbought! (-18)")
        elif rsi > 60: score -= 10; details.append(f"📈 RSI {rsi:.0f} — Prey tiring at the top (-10)")
        else: details.append(f"⚖️ RSI {rsi:.0f} — Balanced territory")

        if len(closes) >= 21:
            e9 = self._ema(closes, 9); e21 = self._ema(closes, 21)
            if e9[-1] > e21[-1]:
                score += 12; details.append("🐂 EMA9 > EMA21 — Short-term bulls LEADING (+12)")
            else:
                score -= 12; details.append("🐻 EMA9 < EMA21 — Bears seized short-term control (-12)")

        if len(closes) >= 50:
            e20 = self._ema(closes, 20); e50 = self._ema(closes, 50)
            if e20[-1] > e50[-1]:
                score += 8; details.append("🏔️ EMA20 > EMA50 — Herd migrates UPHILL (+8)")
            else:
                score -= 8; details.append("🕳️ EMA20 < EMA50 — Herd heads DOWNHILL (-8)")

        if len(closes) >= 26:
            macd = [a-b for a,b in zip(self._ema(closes,12), self._ema(closes,26))]
            sig_line = self._ema(macd, 9)
            if macd[-1] > sig_line[-1]:
                score += 5; details.append("📊 MACD bullish — Momentum favors the HUNTER (+5)")
            else:
                score -= 5; details.append("📊 MACD bearish — Momentum fading (-5)")

        if len(closes) >= 10:
            m5 = (closes[-1] - closes[-5]) / closes[-5] * 100
            m10 = (closes[-1] - closes[-10]) / closes[-10] * 100
            if m5 > 0: score += 3
            else: score -= 3
            details.append(f"🏃 Momentum: 5-bar {'↑' if m5>0 else '↓'}{m5:+.1f}% | 10-bar {'↑' if m10>0 else '↓'}{m10:+.1f}%")

        # ── ALL PATTERNS (categorized) ──
        p_score, p_list, p_bias = self.patterns.analyze(candles)
        score += p_score

        single_kw = ["Doji","Marubozu","Hammer","Star","Hanging","Spinning","Belt","Inverted"]
        combo_kw = ["Engulfing","Harami","Piercing","Dark Cloud","Tweezer","Morning","Evening","Soldiers","Crows","Inside","Abandoned"]
        chart_kw = ["Flag","Double","Head","Triangle","Breakout","Ascending","Descending"]

        single_patterns = [p for p in p_list if any(k in p["name"] for k in single_kw)]
        combo_patterns = [p for p in p_list if any(k in p["name"] for k in combo_kw)]
        chart_patterns = [p for p in p_list if any(k in p["name"] for k in chart_kw)]

        # ── FIBONACCI LEVELS ──
        fib = self._calc_fibonacci(candles)
        near_fib = None
        if fib:
            for lvl in ["0.236", "0.382", "0.500", "0.618", "0.786"]:
                if abs(current - fib[lvl]) / current < 0.008:
                    near_fib = lvl; break

        # ── KEY LEVELS ──
        supports, resistances = self._find_key_levels(candles)
        near_support = any(abs(current - s) / current < 0.01 for s in supports)
        near_resistance = any(abs(current - r) / current < 0.01 for r in resistances)
        if near_support: details.append("⚡ Prey stands at SUPPORT — watch for bounce!")
        if near_resistance: details.append("⚡ Prey hits RESISTANCE wall — watch for rejection!")

        score = max(0, min(100, score))
        direction = "BULLISH" if score >= 55 else "BEARISH" if score <= 45 else "NEUTRAL"

        return {
            "direction": direction, "score": score, "rsi": rsi, "details": details,
            "single_patterns": single_patterns, "combo_patterns": combo_patterns,
            "chart_patterns": chart_patterns, "patterns": [p["name"] for p in p_list],
            "pattern_score": p_score,
            "supports": supports, "resistances": resistances, "fib": fib,
            "near_support": near_support, "near_resistance": near_resistance, "near_fib": near_fib
        }

    def update_daily_bias(self, symbol="BTCUSD"):
        """LAYER 1: The wolf surveys the entire landscape before the hunt.
        Reads 4 months of history, runs all indicators, all 27+ patterns,
        Fibonacci retracements, support/resistance. Refreshes every 4 hours."""
        now = datetime.now(pytz.UTC)
        # Refresh every 4 hours instead of daily
        if (self.bias_date == now.date() and self.daily_bias != "UNKNOWN"
                and hasattr(self, '_last_bias_hour')
                and (now.hour - self._last_bias_hour) < 4):
            return

        spot = self.api.get_spot_price(symbol)

        log.info("")
        log.info("╔══════════════════════════════════════════════════════════════════╗")
        log.info("║  🐺 THE WOLF SURVEYS THE LANDSCAPE — Deep Macro Reconnaissance  ║")
        log.info("║  Scanning months of terrain before choosing the hunting ground   ║")
        log.info("╚══════════════════════════════════════════════════════════════════╝")
        self.daily_report = []; bias_votes = []; bias_scores = []

        # Build monthly candles from daily data (Delta API doesn't support 1M)
        daily_candles = self.api.get_candles(symbol, "1d", 365)  # 1 year of daily
        monthly_candles = self._build_monthly_candles(daily_candles) if daily_candles else []

        tf_configs = [
            (monthly_candles, "MONTHLY", "🌍 THE HORIZON"),
            (None, "WEEKLY", "🗻 HIGH GROUND"),     # fetched live
            (None, "DAILY", "🌲 FOREST FLOOR"),     # fetched live
        ]

        for data_or_none, label, terrain in tf_configs:
            if data_or_none is not None:
                candles = data_or_none
            else:
                tf_res = "1w" if label == "WEEKLY" else "1d"
                tf_count = 52 if label == "WEEKLY" else 120
                candles = self.api.get_candles(symbol, tf_res, tf_count)

            if not candles or len(candles) < 5:
                log.info(f"    📅 {label}: ⚪ Fog covers {terrain} — no visibility ({len(candles) if candles else 0} candles)")
                continue

            log.info(f"")
            log.info(f"    {'━'*62}")
            log.info(f"    {terrain} — {label} RECONNAISSANCE ({len(candles)} candles)")
            log.info(f"    {'━'*62}")

            result = self._deep_tf_analysis(candles, label)

            # Verdict with hunter narrative
            if result['direction'] == "BULLISH":
                log.info(f"    🟢 The {terrain.split(' ')[-1]} favors the BULLS — prey moves UPHILL | Score: {result['score']}/100")
            elif result['direction'] == "BEARISH":
                log.info(f"    🔴 The BEARS dominate {terrain.split(' ')[-1]} — prey stampedes DOWN | Score: {result['score']}/100")
            else:
                log.info(f"    ⚪ Territory is CONTESTED — neither side wins | Score: {result['score']}/100")

            # Indicators
            log.info(f"    📡 TERRAIN SIGNALS:")
            for d in result['details']:
                log.info(f"        {d}")

            # ── Single Candlestick Formations ──
            if result.get('single_patterns'):
                log.info(f"    🕯️ SINGLE TRACKS ({len(result['single_patterns'])} footprints):")
                for p in result['single_patterns']:
                    power = "⚡ STRONG" if abs(p['score']) >= 8 else "💨 Moderate" if abs(p['score']) >= 5 else "🌫️ Faint"
                    log.info(f"        {p['name']} ({p['score']:+d}) — {power}")

            # ── Combined Candlestick Formations ──
            if result.get('combo_patterns'):
                log.info(f"    🕯️🕯️ COMBINED TRACKS ({len(result['combo_patterns'])} formations):")
                for p in result['combo_patterns']:
                    power = "⚡⚡ POWERFUL" if abs(p['score']) >= 12 else "⚡ STRONG" if abs(p['score']) >= 8 else "💨 Moderate"
                    log.info(f"        {p['name']} ({p['score']:+d}) — {power}")

            # ── Chart Pattern Structures ──
            if result.get('chart_patterns'):
                log.info(f"    📐 TERRAIN STRUCTURES ({len(result['chart_patterns'])} found):")
                for p in result['chart_patterns']:
                    if "Breakout" in p['name']:
                        log.info(f"        🚀 BREAKOUT: {p['name']} ({p['score']:+d}) — The prey BREAKS FREE!")
                    elif "pending" in p['name']:
                        log.info(f"        ⏳ FORMING: {p['name']} — Coiling... breakout imminent!")
                    else:
                        log.info(f"        📊 DETECTED: {p['name']} ({p['score']:+d})")

            # Pattern score summary
            ps = result.get('pattern_score', 0)
            total_p = len(result.get('patterns', []))
            if total_p > 0:
                e = "🟢" if ps > 0 else "🔴" if ps < 0 else "⚪"
                log.info(f"    {e} Pattern Verdict: {ps:+d} points from {total_p} formations")

            # ── Fibonacci Map ──
            fib = result.get('fib', {})
            if fib:
                log.info(f"    📏 FIBONACCI MAP (Swing ${fib.get('swing_low',0):,.0f} → ${fib.get('swing_high',0):,.0f}):")
                for lvl in ["0.236", "0.382", "0.500", "0.618", "0.786"]:
                    fv = fib.get(lvl, 0)
                    tags = ""
                    if lvl == "0.618": tags += " 🏆 GOLDEN RATIO"
                    if lvl == "0.500": tags += " ⚖️ HALF"
                    if result.get('near_fib') == lvl: tags += " ◄━━ 🐺 PREY IS HERE"
                    log.info(f"        Fib {lvl}: ${fv:,.0f}{tags}")
                if result.get('near_fib'):
                    log.info(f"    ⚡ PRICE AT FIB {result['near_fib']} — Critical decision zone!")

            # ── Support & Resistance ──
            if result['supports']:
                log.info(f"    🟢 SUPPORT FLOORS: {' | '.join(f'${s:,.0f}' for s in result['supports'])}")
            if result['resistances']:
                log.info(f"    🔴 RESISTANCE CEILINGS: {' | '.join(f'${r:,.0f}' for r in result['resistances'])}")
            if result.get('near_support'):
                log.info(f"    ⚡🟢 The prey stands on SUPPORT ground — potential bounce!")
            if result.get('near_resistance'):
                log.info(f"    ⚡🔴 The prey presses against RESISTANCE — potential rejection!")

            bias_votes.append(result['direction'])
            bias_scores.append(result['score'])
            self.daily_report.append({"tf": label, **result})
            self.key_levels[label] = {"supports": result['supports'], "resistances": result['resistances']}

        # ══ THE WOLF'S FINAL VERDICT ══
        log.info(f"")
        log.info(f"    {'━'*62}")
        log.info(f"    🏷️  THE WOLF'S VERDICT — Today's Hunting Strategy")
        log.info(f"    {'━'*62}")
        bull_count = bias_votes.count("BULLISH")
        bear_count = bias_votes.count("BEARISH")
        neutral_count = bias_votes.count("NEUTRAL")
        total = len(bias_votes)

        log.info(f"    📊 Votes: {bull_count} BULLISH | {bear_count} BEARISH | {neutral_count} NEUTRAL (out of {total})")
        if bias_scores:
            avg_score = sum(bias_scores) / len(bias_scores)
            log.info(f"    📊 Average score: {avg_score:.1f}/100")

        # Majority vote: 2 of 3
        if bull_count >= 2:
            self.daily_bias = "BULLISH"
        elif bear_count >= 2:
            self.daily_bias = "BEARISH"
        elif bull_count == 1 and bear_count == 0:
            self.daily_bias = "BULLISH"  # 1 bullish + rest neutral = lean bullish
        elif bear_count == 1 and bull_count == 0:
            self.daily_bias = "BEARISH"  # 1 bearish + rest neutral = lean bearish
        elif neutral_count >= 2 and bias_scores:
            # All neutral — use average score to tiebreak
            avg = sum(bias_scores) / len(bias_scores)
            if avg >= 52:
                self.daily_bias = "BULLISH"
                log.info(f"    🟢 Neutrals lean BULLISH (avg score: {avg:.1f})")
            elif avg <= 48:
                self.daily_bias = "BEARISH"
                log.info(f"    🔴 Neutrals lean BEARISH (avg score: {avg:.1f})")
            else:
                self.daily_bias = "CHOPPY"
        else:
            self.daily_bias = "CHOPPY"   # True conflict: bull vs bear

        if self.daily_bias == "BULLISH":
            log.info(f"    🟢🐂 THE TERRAIN FAVORS THE BULLS")
            log.info(f"    📋 Strategy: SELL PUT options to collect premium.")
            log.info(f"    🎯 Look for: Pullbacks to support / Fib 0.382-0.618 for entries")
        elif self.daily_bias == "BEARISH":
            log.info(f"    🔴🐻 THE BEARS RULE THIS TERRITORY")
            log.info(f"    📋 Strategy: SELL CALL options to collect premium.")
            log.info(f"    🎯 Look for: Rallies to resistance / Fib 0.382-0.618 for entries")
        else:
            log.info(f"    🟡⚔️ THE BATTLEFIELD IS CONTESTED — CHOPPY TERRITORY")
            log.info(f"    📋 Strategy: STRANGLE — sell BOTH call + put.")
            log.info(f"    🎯 Profit from theta decay while price ranges")

        # Macro pattern alerts
        for r in self.daily_report:
            for pn in r.get('patterns', []):
                if any(kw in pn for kw in ['Breakout', 'Flag', 'Triangle', 'Head', 'Double', 'Soldiers', 'Crows']):
                    log.info(f"    💥 MACRO SIGNAL: {pn} on {r['tf']} — act accordingly!")

        log.info(f"")
        log.info(f"    ⏰ Bias refreshes every 4 hours (next: {(now.hour // 4 + 1) * 4 % 24}:00 UTC).")
        log.info(f"    🐺 The wolf has surveyed. Now we wait for the perfect moment.")
        log.info("╔══════════════════════════════════════════════════════════════════╗")
        log.info(f"║  BIAS: {self.daily_bias:8s} | BTC: ${spot:>10,.2f} | Refresh: 4h       ║")
        log.info("╚══════════════════════════════════════════════════════════════════╝")

        self.bias_date = now.date()
        self._last_bias_hour = now.hour
        self.bias_last_updated = now

    def _build_monthly_candles(self, daily_candles):
        """Build monthly OHLCV candles from daily candles.
        Delta API doesn't support 1M resolution, so we aggregate manually."""
        if not daily_candles or len(daily_candles) < 30:
            return []

        from collections import defaultdict
        months = defaultdict(list)
        for c in daily_candles:
            # Group by year-month
            try:
                ts = c.get("time", c.get("t", 0))
                if isinstance(ts, (int, float)):
                    dt = datetime.fromtimestamp(ts, tz=pytz.UTC)
                else:
                    dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
                key = f"{dt.year}-{dt.month:02d}"
                months[key].append(c)
            except:
                continue

        monthly = []
        for key in sorted(months.keys()):
            candles = months[key]
            if len(candles) < 5:  # Skip incomplete months
                continue
            monthly.append({
                "open": float(candles[0].get("open", 0)),
                "high": max(float(c.get("high", 0)) for c in candles),
                "low": min(float(c.get("low", float('inf'))) for c in candles),
                "close": float(candles[-1].get("close", 0)),
                "volume": sum(float(c.get("volume", 0)) for c in candles),
                "time": candles[0].get("time", candles[0].get("t", 0))
            })

        log.info(f"    🌍 Built {len(monthly)} monthly candles from {len(daily_candles)} daily")
        return monthly

    def evaluate(self, symbol="BTCUSD"):
        """Full 4-layer evaluation. Returns (signal, score, method, bias, spot)."""
        spot = self.api.get_spot_price(symbol)

        # Refresh bias (4-hour check happens inside update_daily_bias)
        self.update_daily_bias(symbol)

        # LAYER 1 CHECK: Choppy = STRANGLE mode (handled in _cycle)
        if self.daily_bias == "CHOPPY":
            log.info("    🟡 Market is CHOPPY — STRANGLE MODE active.")
            return "HEDGE", 50, "choppy_hedge", self.daily_bias, spot

        # LAYER 2: Higher TF confirmation (15m, 1h, 4h)
        log.info("    📊 HIGHER TF CONFIRMATION:")
        confirm_count = 0
        for tf in CONFIRM_TIMEFRAMES:
            candles = self.api.get_candles(symbol, tf, CANDLE_LIMIT)
            if not candles:
                log.info(f"        {tf:>4s}: ⚪ No data")
                continue
            closes = [float(c["close"]) for c in candles]
            rsi = self._rsi(closes); score = 50
            if len(closes)>=21:
                e9=self._ema(closes,9); e21=self._ema(closes,21)
                if e9[-1]>e21[-1]: score+=12
                else: score-=12
            if rsi<40: score+=10
            elif rsi>60: score-=10
            if len(closes)>=5:
                if closes[-1]>closes[-5]: score+=5
                else: score-=5

            direction = "BULLISH" if score>=55 else "BEARISH" if score<=45 else "NEUTRAL"
            emoji = "🟢" if direction=="BULLISH" else "🔴" if direction=="BEARISH" else "⚪"
            log.info(f"        {tf:>4s}: {emoji} {direction:8s} | RSI: {rsi:.0f} | Score: {score}")

            if (self.daily_bias=="BULLISH" and direction=="BULLISH") or (self.daily_bias=="BEARISH" and direction=="BEARISH"):
                confirm_count += 1

        log.info(f"    📐 Confirmation: {confirm_count}/{len(CONFIRM_TIMEFRAMES)} align with {self.daily_bias} bias")
        if confirm_count < 2:
            log.info("    ⚠️  Higher TFs don't confirm daily bias — standing down.")
            return "NEUTRAL", 0, "no_confirmation", self.daily_bias, spot

        # LAYER 3: Entry trigger on 5m
        log.info(f"    📊 ENTRY ANALYSIS ({PRIMARY_TIMEFRAME} base):")
        candles = self.api.get_candles(symbol, PRIMARY_TIMEFRAME, CANDLE_LIMIT)
        if not candles:
            log.info(f"        ⚪ No {PRIMARY_TIMEFRAME} data")
            return "NEUTRAL", 0, "no_5m_data", self.daily_bias, spot

        closes = [float(c["close"]) for c in candles]
        entry_score = 50

        # RSI
        rsi = self._rsi(closes)
        if rsi < 30: entry_score += 18
        elif rsi < 40: entry_score += 12
        elif rsi < 45: entry_score += 6
        elif rsi > 70: entry_score -= 18
        elif rsi > 60: entry_score -= 12
        elif rsi > 55: entry_score -= 6

        # EMA crossover
        if len(closes) >= 21:
            e9=self._ema(closes,9); e21=self._ema(closes,21)
            if e9[-1]>e21[-1]: entry_score+=10
            else: entry_score-=10

        # MACD
        if len(closes) >= 26:
            macd_line = [a-b for a,b in zip(self._ema(closes,12), self._ema(closes,26))]
            signal_line = self._ema(macd_line, 9)
            if macd_line[-1] > signal_line[-1] and macd_line[-2] <= signal_line[-2]:
                entry_score += 8; log.info("        MACD: 🟢 Bullish cross (+8)")
            elif macd_line[-1] < signal_line[-1] and macd_line[-2] >= signal_line[-2]:
                entry_score -= 8; log.info("        MACD: 🔴 Bearish cross (-8)")

        # Momentum
        if len(closes)>=5:
            if closes[-1]>closes[-5]: entry_score+=5
            else: entry_score-=5

        # Pattern detection on 5m
        p_score, p_list, p_bias = self.patterns.analyze(candles)
        entry_score += p_score
        if p_list:
            log.info(f"        🕯️  PATTERNS ({len(p_list)} found):")
            for p in p_list[:8]:
                log.info(f"            {p['name']} ({p['score']:+d})")

        entry_score = max(0, min(100, entry_score))
        log.info(f"        RSI: {rsi:.0f} | EMA: {'↑' if entry_score>50 else '↓'} | Patterns: {p_score:+d} | TOTAL: {entry_score}")

        # Direction must match daily bias
        if self.daily_bias == "BULLISH":
            sig = "BUY" if entry_score >= OPTIONS_MIN_SCORE else "NEUTRAL"
        elif self.daily_bias == "BEARISH":
            sig = "SELL" if (100-entry_score) >= OPTIONS_MIN_SCORE else "NEUTRAL"
        else:
            sig = "NEUTRAL"

        return sig, entry_score, "full_analysis", self.daily_bias, spot

    def _ema(self, data, p):
        k=2/(p+1); ema=[data[0]]
        for v in data[1:]: ema.append(v*k+ema[-1]*(1-k))
        return ema

    def _rsi(self, closes, p=14):
        if len(closes)<p+1: return 50
        d=np.diff(closes); g=np.where(d>0,d,0); l=np.where(d<0,-d,0)
        ag=np.mean(g[:p]); al=np.mean(l[:p])
        for i in range(p,len(g)): ag=(ag*(p-1)+g[i])/p; al=(al*(p-1)+l[i])/p
        if al==0: return 100
        return 100-(100/(1+ag/al))

# ══════════════════════════════════════════════════════════════
# MAIN BOT — v5.0 "One Kill a Day"
# ══════════════════════════════════════════════════════════════
class OptionsTradingBot:
    def __init__(self):
        self.api = DeltaAPI()
        self.signals = SignalEngine(self.api)
        self.positions: List[OptionsPosition] = []
        self.last_trade_date = None

        log.info("🎯 BOT v5.0 — 'One Kill a Day' Engine STARTING")
        log.info(f"    ⚙️  Cycle: {CYCLE_INTERVAL}s | Score≥{OPTIONS_MIN_SCORE} | Leverage: {LEVERAGE}x | MaxTrades: {MAX_TRADES_PER_DAY}/day")
        log.info(f"    ⚙️  Base TF: {PRIMARY_TIMEFRAME} | Confirm: {', '.join(CONFIRM_TIMEFRAMES)} | Macro: {', '.join(MACRO_TIMEFRAMES)}")
        log.info(f"    ⚙️  Strategy: SELL options (collect premium, 200x leverage)")
        log.info(f"    ⚙️  Stop: premium rises 100% | Take Profit: premium drops 80% | Max Hold: {MAX_HOLD_HOURS}h")

        wallet = self.api.get_wallet_balance()
        self.wallet_balance = wallet.get("available", 0)
        if self.wallet_balance > 0:
            log.info(f"    💰 Wallet: ${self.wallet_balance:,.2f} | Notional: ${self.wallet_balance*LEVERAGE:,.2f} | Mode: {'PAPER' if PAPER_TRADE else 'LIVE'}")
        else:
            log.info(f"    💰 Wallet: unavailable | Mode: {'PAPER' if PAPER_TRADE else 'LIVE'}")

        self._load()
        self.signals.update_daily_bias()

    def _load(self):
        data = _load_json(POSITIONS_FILE, [])
        for d in data:
            if d.get("status") == "open":
                try:
                    cd = d.pop("contract", {})
                    contract = OptionContract(**cd) if isinstance(cd, dict) else cd
                    pos = OptionsPosition(contract=contract, **d)
                    self.positions.append(pos)
                    log.info(f"    📂 Loaded: {contract.symbol} | Entry: ${pos.entry_premium:.4f}")
                except Exception as e:
                    log.warning(f"    ⚠️  Load error: {e}")

    def run(self):
        log.info("═══════════════════════════════════════════════════")
        log.info("🐺  THE HUNT BEGINS — v5.0 One Kill a Day")
        log.info("═══════════════════════════════════════════════════")
        while True:
            try:
                self._cycle()
                log.info(f"💤 Resting {CYCLE_INTERVAL}s before next scan...\n")
                time.sleep(CYCLE_INTERVAL)
            except KeyboardInterrupt:
                log.info("🛑 Shutting down."); break
            except Exception as e:
                log.error(f"🩸 Error: {e} — recovering in 60s..."); time.sleep(60)

    def _cycle(self):
        ts = datetime.now(pytz.UTC).strftime('%H:%M:%S UTC')
        log.info(f"══════════ 🔄 HUNT CYCLE — {ts} ══════════")

        # Monitor existing positions
        open_pos = [p for p in self.positions if p.status == "open"]
        if open_pos:
            log.info(f"👁️  Monitoring {len(open_pos)} active position(s)...")
            for pos in open_pos: self._monitor(pos)

        # Check daily trade limit (allow 2 on CHOPPY for hedging)
        today = datetime.now(pytz.UTC).date()
        max_today = 2 if self.signals.daily_bias == "CHOPPY" else MAX_TRADES_PER_DAY
        trades_today = len([p for p in self.positions if p.entry_time and p.entry_time[:10] == str(today)])
        if trades_today >= max_today:
            msg = "🏆 Hedge pair placed" if max_today == 2 else "🏆 Already made our kill today"
            log.info(f"{msg} — monitoring only.")
            return

        # Check max positions
        active = len([p for p in self.positions if p.status == "open"])
        max_pos = 2 if self.signals.daily_bias == "CHOPPY" else MAX_POSITIONS
        if active >= max_pos:
            log.info("🎒 Position(s) active — monitoring only.")
            return

        # Run 4-layer analysis
        log.info("👃 Running 4-layer analysis...")
        sig, score, method, bias, spot = self.signals.evaluate()

        bar = "█" * int(score/5) + "░" * (20-int(score/5))
        if sig == "HEDGE":
            log.info(f"📡 Verdict: 🟡 HEDGE MODE | Bias: CHOPPY | BTC: ${spot:,.2f}")
            log.info("    🛡️ Placing hedge — buying BOTH call + put to capture volatility")
            self._execute_hedge(spot)
            return

        d = "🟢 BULLISH" if sig=="BUY" else "🔴 BEARISH" if sig=="SELL" else "⚪ NEUTRAL"
        log.info(f"📡 Verdict: {d} | Score: [{bar}] {score:.0f}/100 | Bias: {bias} | BTC: ${spot:,.2f}")

        if sig == "NEUTRAL":
            log.info("😴 Criteria not met — standing down.")
            return

        # SELL STRATEGY: Bullish = SELL PUT, Bearish = SELL CALL
        # We SELL options to collect premium. Premium decays in our favor.
        log.info(f"🔭 Scanning options to SELL for {BASE_UNDERLYING}...")
        chain = self.api.get_options_chain(BASE_UNDERLYING)
        if not chain:
            log.warning("🏜️  No contracts found!"); return

        # INVERTED: Bullish → sell PUT (profit if BTC stays above strike)
        #           Bearish → sell CALL (profit if BTC stays below strike)
        opt_type = "put" if sig == "BUY" else "call"
        matching = [c for c in chain if c["type"] == opt_type and c["tradeable"]]
        log.info(f"🗺️  Found {len(matching)} tradeable {opt_type.upper()}s to SELL")

        if not matching:
            log.warning(f"💀 No tradeable {opt_type.upper()} options!"); return

        # Greeks-based selection for SELLING
        # For selling: we want HIGH theta (time decay = our income)
        #              LOW delta (OTM = less risk of assignment)
        #              HIGH IV (more premium to collect)
        for c in matching:
            gs = 0; d_abs = abs(c.get("delta", 0))
            # Sweet spot: delta 0.20-0.40 (slight OTM for selling)
            if 0.20 <= d_abs <= 0.40: gs += 25
            elif 0.15 <= d_abs <= 0.50: gs += 15
            elif d_abs < 0.15: gs += 5   # Too far OTM = low premium
            # High theta = we earn more per day
            theta = abs(c.get("theta", 0))
            if theta > 80: gs += 20
            elif theta > 50: gs += 15
            elif theta > 30: gs += 10
            # High IV = fat premium to collect
            iv = c.get("iv", 0)
            if iv > 80: gs += 15
            elif iv > 50: gs += 10
            elif iv > 30: gs += 5
            # OI — prefer liquid contracts
            oi = c.get("oi", 0) or c.get("open_interest", 0)
            if oi > 100: gs += 10
            elif oi > 50: gs += 5
            # Proximity to spot — OTM preferred for selling
            otm_pct = abs(c["strike"] - spot) / spot * 100
            if 1 <= otm_pct <= 5: gs += 15   # 1-5% OTM sweet spot
            elif otm_pct < 1: gs -= 5        # Too close = risky
            c["greek_score"] = gs

        matching.sort(key=lambda x: x["greek_score"], reverse=True)
        best = matching[0]

        # Log top candidates
        action = "SELL PUT" if opt_type == "put" else "SELL CALL"
        log.info(f"🏹 TOP TARGETS TO {action} (by Greeks):")
        for i, c in enumerate(matching[:3]):
            rank = "👑" if i==0 else f"  {i+1}."
            oi = c.get('oi', 0) or c.get('open_interest', 0)
            otm = abs(c['strike'] - spot) / spot * 100
            log.info(f"    {rank} {c['symbol']} | GScore: {c['greek_score']:.0f} | OTM: {otm:.1f}%")
            log.info(f"       Δ={c.get('delta',0):+.4f} Θ={c.get('theta',0):.2f} IV={c.get('iv',0):.1f}% OI={oi}")

        log.info(f"🔥 SELLING THIS ONE — {best['symbol']}")
        self._open(best, sig)

    def _execute_hedge(self, spot):
        """CHOPPY MODE: SELL both a CALL and PUT (short strangle) to collect premium."""
        chain = self.api.get_options_chain(BASE_UNDERLYING)
        if not chain:
            log.warning("🏜️  No contracts for hedge!"); return

        log.info("    🛡️ STRANGLE: Selling BOTH call + put to collect premium from both sides")

        for opt_type in ["call", "put"]:
            matching = [c for c in chain if c["type"] == opt_type and c["tradeable"]]
            if not matching:
                log.warning(f"    ⚠️  No tradeable {opt_type.upper()}s"); continue

            # Score for SELLING: prefer OTM, high theta, high IV
            for c in matching:
                gs = 0; d_abs = abs(c.get("delta", 0))
                if 0.15 <= d_abs <= 0.35: gs += 25  # OTM sweet spot for selling
                elif 0.10 <= d_abs <= 0.45: gs += 15
                theta = abs(c.get("theta", 0))
                if theta > 50: gs += 15
                elif theta > 30: gs += 10
                iv = c.get("iv", 0)
                if iv > 50: gs += 10
                otm_pct = abs(c["strike"] - spot) / spot * 100
                if 2 <= otm_pct <= 6: gs += 15  # 2-6% OTM for strangle
                c["greek_score"] = gs

            matching.sort(key=lambda x: x["greek_score"], reverse=True)
            best = matching[0]

            emoji = "📕" if opt_type == "call" else "📗"
            otm = abs(best['strike'] - spot) / spot * 100
            log.info(f"    {emoji} SELL {opt_type.upper()} — {best['symbol']} | OTM: {otm:.1f}%")
            log.info(f"       Δ={best.get('delta',0):+.4f} Θ={best.get('theta',0):.2f} IV={best.get('iv',0):.1f}%")

            # Half wallet per leg
            self._open(best, "BUY" if opt_type=="put" else "SELL", wallet_fraction=0.5)

        log.info("    🛡️ Strangle placed — collecting premium from both sides!")

    def _open(self, bc, sig, wallet_fraction=1.0):
        """SELL an option to collect premium. Leverage (200x) applies for selling."""
        wallet = self.api.get_wallet_balance()
        available = wallet.get("available", self.wallet_balance) if wallet.get("available", 0) > 0 else self.wallet_balance

        lev_result = self.api.set_leverage(bc["product_id"], LEVERAGE)
        actual_lev = lev_result.get("actual_leverage", LEVERAGE)

        # SELLING: Leverage DOES apply — margin = (spot × lot_size) / leverage
        LOT_SIZE = 0.001  # 0.001 BTC per contract on Delta Exchange
        spot = self.api.get_spot_price()
        allocation = available * wallet_fraction
        margin_per_lot = (spot * LOT_SIZE) / actual_lev
        qty = max(1, int(allocation * 0.85 / max(margin_per_lot, 0.01)))  # 85% safety

        ep = bc["bid"] if bc["bid"] > 0 else bc["mark_price"]  # SELL at bid

        frac_label = f" ({wallet_fraction*100:.0f}% of wallet)" if wallet_fraction < 1.0 else ""
        log.info(f"    💰 Wallet: ${available:,.2f} | Allocation: ${allocation:,.2f}{frac_label}")
        log.info(f"    💰 SELLING options — {actual_lev}x leverage applies!")
        log.info(f"    💰 Margin/lot: ${margin_per_lot:.4f} | Premium: ${ep:.2f}/BTC")
        log.info(f"    💰 Qty: {qty} lots | Premium collected: ${ep * LOT_SIZE * qty:,.2f}")
        log.info(f"    💰 SELL {qty}x @ ${ep:.4f}")
        log.info(f"    📤 {'PAPER' if PAPER_TRADE else 'LIVE'} SELL order...")

        order = self.api.place_order(bc["product_id"], "sell", qty, bc["symbol"])

        if order.get("success"):
            # For SELLING: stop when premium DOUBLES (loss), target when drops 80% (profit)
            stop = ep * 2.0     # Stop loss: premium doubles against us
            target = ep * 0.20  # Take profit: premium drops to 20% (80% captured)

            pos = OptionsPosition(
                contract=OptionContract(symbol=bc["symbol"], underlying=BASE_UNDERLYING, expiry="",
                    strike=bc["strike"], option_type=bc["type"], premium=ep,
                    delta=bc.get("delta",0), implied_vol=bc.get("iv",0)/100 if bc.get("iv",0)>1 else 0.5,
                    open_interest=0, bid=bc["bid"], ask=bc["ask"], spread_pct=bc["spread_pct"],
                    product_id=bc["product_id"]),
                side="sell", quantity=qty, entry_premium=ep,
                entry_time=datetime.now(pytz.UTC).isoformat(),
                stop_premium=stop, target_premium=target,
                order_id=str(order["result"]["id"]), peak_premium=ep, leverage=actual_lev)

            self.positions.append(pos)
            self.last_trade_date = datetime.now(pytz.UTC).date()

            log.info(f"══════════════════════════════════════════════")
            log.info(f"🏆 PREMIUM COLLECTED — TRAP IS SET!")
            log.info(f"    └─ SOLD {qty}x {bc['symbol']}")
            log.info(f"    └─ Premium: ${ep:.2f}/BTC | Collected: ${ep * LOT_SIZE * qty:,.2f}")
            log.info(f"    └─ Stop Loss: ${stop:.2f} (premium doubles = CUT)")
            log.info(f"    └─ Take Profit: ${target:.2f} (80% decay = BANK IT)")
            log.info(f"    └─ Leverage: {actual_lev}x")
            log.info(f"══════════════════════════════════════════════")
            log.info("🏆 Premium trap set. Monitoring until decay or stop.")
            _save_json(POSITIONS_FILE, [asdict(p) for p in self.positions])
        else:
            log.error(f"💥 ORDER REJECTED: {order}")
            # Retry with fewer lots if margin insufficient
            err_code = order.get('error', {}).get('code', '')
            if err_code == 'insufficient_margin' and qty > 10:
                retry_qty = int(qty * 0.6)
                log.info(f"    🔄 Retrying with {retry_qty} lots (60%)...")
                order2 = self.api.place_order(bc["product_id"], "sell", retry_qty, bc["symbol"])
                if order2.get("success"):
                    stop = ep * 2.0; target = ep * 0.20
                    pos = OptionsPosition(
                        contract=OptionContract(symbol=bc["symbol"], underlying=BASE_UNDERLYING, expiry="",
                            strike=bc["strike"], option_type=bc["type"], premium=ep,
                            delta=bc.get("delta",0), implied_vol=bc.get("iv",0)/100 if bc.get("iv",0)>1 else 0.5,
                            open_interest=0, bid=bc["bid"], ask=bc["ask"], spread_pct=bc["spread_pct"],
                            product_id=bc["product_id"]),
                        side="sell", quantity=retry_qty, entry_premium=ep,
                        entry_time=datetime.now(pytz.UTC).isoformat(),
                        stop_premium=stop, target_premium=target,
                        order_id=str(order2["result"]["id"]), peak_premium=ep, leverage=actual_lev)
                    self.positions.append(pos)
                    self.last_trade_date = datetime.now(pytz.UTC).date()
                    log.info(f"    ✅ Retry succeeded: SOLD {retry_qty}x {bc['symbol']}")
                    _save_json(POSITIONS_FILE, [asdict(p) for p in self.positions])
                else:
                    log.error(f"    💥 Retry also rejected: {order2}")

    def _monitor(self, pos):
        """Monitor a SOLD option. We PROFIT when premium DROPS (decay)."""
        sym = pos.contract.symbol if not isinstance(pos.contract, dict) else pos.contract.get("symbol","")
        pid = pos.contract.product_id if not isinstance(pos.contract, dict) else pos.contract.get("product_id",0)

        curr = self.api.get_option_premium(sym)
        if not curr: return

        p = curr.get("mark_price", pos.entry_premium)
        if p <= 0: return

        # For SELLING: profit = entry - current (we want premium to DROP)
        # Track LOWEST premium (that's our best profit point)
        LOT_SIZE = 0.001
        if not hasattr(pos, 'trough_premium') or pos.trough_premium is None:
            pos.trough_premium = p
        if p < pos.trough_premium:
            pos.trough_premium = p
            log.info(f"    📉 NEW LOW (good!): ${p:.2f} — premium decaying in our favor!")

        # P&L for selling: we SOLD at entry, current price is what we'd buy back at
        pnl_per_lot = (pos.entry_premium - p) * LOT_SIZE
        pnl_total = pnl_per_lot * pos.quantity
        decay_pct = ((pos.entry_premium - p) / pos.entry_premium) * 100  # Positive = profit

        emoji = "📉✅" if decay_pct > 0 else "📈❌"
        log.info(f"    {emoji} {sym}: Sold@${pos.entry_premium:.2f} → Now@${p:.2f}")
        log.info(f"        Decay: {decay_pct:+.1f}% | P&L: ${pnl_total:,.2f} | Low: ${pos.trough_premium:.2f}")

        # EXIT 1: STOP LOSS — premium RISES above 2× entry (moved against us)
        if p >= pos.stop_premium:
            reason = f"STOP_LOSS (premium rose to ${p:.2f}, 2× entry)"
            log.info(f"    🩸 {reason}")
            self._close(pos, p, reason); return

        # EXIT 2: TAKE PROFIT — premium dropped to 20% of entry (80% captured)
        if p <= pos.target_premium:
            reason = f"TAKE_PROFIT (premium decayed {decay_pct:.0f}%, target hit)"
            log.info(f"    🎉 {reason}")
            self._close(pos, p, reason); return

        # EXIT 3: TRAILING PROFIT LOCK — if premium bounced 50% from the low
        if pos.trough_premium > 0 and pos.trough_premium < pos.entry_premium * 0.8:
            bounce_from_low = (p - pos.trough_premium) / max(pos.trough_premium, 0.01)
            if bounce_from_low > 0.50:  # Premium bounced 50% from trough
                reason = f"TRAILING_LOCK (bounced {bounce_from_low*100:.0f}% from low ${pos.trough_premium:.2f})"
                log.info(f"    ⚡ {reason}")
                self._close(pos, p, reason); return

        # EXIT 4: DELTA WARNING — if delta gets too high (deep ITM danger)
        if curr.get("delta"):
            d = abs(curr["delta"])
            if d > 0.75:
                log.info(f"    ⚠️  DELTA WARNING: {d:.2f} — option going deep ITM!")
            if d > 0.85:
                reason = f"DELTA_EXIT (Δ={d:.2f}, too deep ITM)"
                log.info(f"    🩸 {reason}")
                self._close(pos, p, reason); return

        # EXIT 5: MAX HOLD TIME
        try:
            entry_dt = datetime.fromisoformat(pos.entry_time.replace("Z","+00:00"))
            hold_hrs = (datetime.now(pytz.UTC)-entry_dt).total_seconds()/3600
            if hold_hrs >= MAX_HOLD_HOURS:
                self._close(pos, p, f"MAX_HOLD_{MAX_HOLD_HOURS}H (decay: {decay_pct:+.0f}%)"); return
        except: pass

    def _close(self, pos, p, reason):
        """Close a SOLD option by BUYING it back."""
        sym = pos.contract.symbol if not isinstance(pos.contract, dict) else pos.contract.get("symbol","")
        pid = pos.contract.product_id if not isinstance(pos.contract, dict) else pos.contract.get("product_id",0)
        # BUY back to close the short
        self.api.place_order(pid, "buy", pos.quantity, sym)

        LOT_SIZE = 0.001
        pnl_per_lot = (pos.entry_premium - p) * LOT_SIZE  # Positive if premium dropped
        pnl = pnl_per_lot * pos.quantity
        pnl_pct = ((pos.entry_premium - p) / pos.entry_premium) * 100
        pos.status="closed"; pos.exit_premium=p; pos.exit_reason=reason; pos.pnl=pnl

        e = "💰" if pnl>0 else "💸"
        log.info(f"══════════════════════════════════════════════")
        log.info(f"{e} HUNT COMPLETE — {reason}")
        log.info(f"    └─ SOLD @ ${pos.entry_premium:.2f} → Bought back @ ${p:.2f}")
        log.info(f"    └─ P&L: ${pnl:,.4f} ({pnl_pct:+.1f}%) | Qty: {pos.quantity} lots")
        log.info(f"══════════════════════════════════════════════")

        _save_json(POSITIONS_FILE, [asdict(p) for p in self.positions])
        history = _load_json(TRADE_HISTORY_FILE, [])
        history.append({"symbol":sym,"side":"sell","entry":pos.entry_premium,"exit":p,
            "pnl":pnl,"pnl_pct":pnl_pct,"reason":reason,"leverage":pos.leverage,
            "entry_time":pos.entry_time,"exit_time":datetime.now(pytz.UTC).isoformat()})
        _save_json(TRADE_HISTORY_FILE, history)

if __name__ == "__main__":
    bot = OptionsTradingBot()
    bot.run()
