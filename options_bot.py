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
LEVERAGE          = int(os.getenv("LEVERAGE", "20"))  # Delta max for options = 20x
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
            res_map = {'s': 1, 'm': 60, 'h': 3600, 'd': 86400, 'w': 604800}
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
        Fibonacci retracements, support/resistance. Locked for the day."""
        today = datetime.now(pytz.UTC).date()
        if self.bias_date == today and self.daily_bias != "UNKNOWN":
            return

        spot = self.api.get_spot_price(symbol)

        log.info("")
        log.info("╔══════════════════════════════════════════════════════════════════╗")
        log.info("║  🐺 THE WOLF SURVEYS THE LANDSCAPE — Deep Macro Reconnaissance  ║")
        log.info("║  Scanning months of terrain before choosing the hunting ground   ║")
        log.info("╚══════════════════════════════════════════════════════════════════╝")
        self.daily_report = []; bias_votes = []

        tf_configs = [("1w", "WEEKLY", 20, "🗻 HIGH GROUND"), ("1d", "DAILY", 120, "🌲 FOREST FLOOR")]

        for tf, label, count, terrain in tf_configs:
            candles = self.api.get_candles(symbol, tf, count)
            if not candles:
                log.info(f"    📅 {label}: ⚪ Fog covers {terrain} — no visibility")
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
            self.daily_report.append({"tf": label, **result})
            self.key_levels[label] = {"supports": result['supports'], "resistances": result['resistances']}

        # ══ THE WOLF'S FINAL VERDICT ══
        log.info(f"")
        log.info(f"    {'━'*62}")
        log.info(f"    🏷️  THE WOLF'S VERDICT — Today's Hunting Strategy")
        log.info(f"    {'━'*62}")
        bull_count = bias_votes.count("BULLISH")
        bear_count = bias_votes.count("BEARISH")

        if bull_count >= 1 and bear_count == 0:
            self.daily_bias = "BULLISH"
        elif bear_count >= 1 and bull_count == 0:
            self.daily_bias = "BEARISH"
        else:
            self.daily_bias = "CHOPPY"

        if self.daily_bias == "BULLISH":
            log.info(f"    🟢🐂 THE TERRAIN FAVORS THE BULLS")
            log.info(f"    📋 Strategy: Hunt CALL options only. The herd moves uphill.")
            log.info(f"    🎯 Look for: Pullbacks to support / Fib 0.382-0.618 for entries")
        elif self.daily_bias == "BEARISH":
            log.info(f"    🔴🐻 THE BEARS RULE THIS TERRITORY")
            log.info(f"    📋 Strategy: Hunt PUT options only. The herd stampedes down.")
            log.info(f"    🎯 Look for: Rallies to resistance / Fib 0.382-0.618 for entries")
        else:
            log.info(f"    🟡⚔️ THE BATTLEFIELD IS CONTESTED — CHOPPY TERRITORY")
            log.info(f"    📋 Strategy: HEDGE MODE — Buy BOTH call + put.")
            log.info(f"    🎯 Profit from volatility spikes in either direction")

        # Macro pattern alerts
        for r in self.daily_report:
            for pn in r.get('patterns', []):
                if any(kw in pn for kw in ['Breakout', 'Flag', 'Triangle', 'Head', 'Double', 'Soldiers', 'Crows']):
                    log.info(f"    💥 MACRO SIGNAL: {pn} on {r['tf']} — act accordingly!")

        log.info(f"")
        log.info(f"    ⏰ This terrain map is LOCKED until midnight UTC.")
        log.info(f"    🐺 The wolf has surveyed. Now we wait for the perfect moment.")
        log.info("╔══════════════════════════════════════════════════════════════════╗")
        log.info(f"║  BIAS: {self.daily_bias:8s} | BTC: ${spot:>10,.2f} | Refresh: midnight UTC  ║")
        log.info("╚══════════════════════════════════════════════════════════════════╝")

        self.bias_date = today
        self.bias_last_updated = datetime.now(pytz.UTC)

    def evaluate(self, symbol="BTCUSD"):
        """Full 4-layer evaluation. Returns (signal, score, method, bias, spot)."""
        spot = self.api.get_spot_price(symbol)

        # Refresh bias at midnight UTC (new day)
        today = datetime.now(pytz.UTC).date()
        if self.bias_date != today:
            self.update_daily_bias(symbol)

        # LAYER 1 CHECK: Choppy = HEDGE mode (handled in _cycle)
        if self.daily_bias == "CHOPPY":
            log.info("    🟡 Market is CHOPPY — HEDGE MODE active.")
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
        log.info(f"    ⚙️  Trailing Stop: {TRAILING_STOP_PCT*100:.0f}% from peak | Max Hold: {MAX_HOLD_HOURS}h")
        log.info(f"    ⚙️  Position Sizing: {OPTIONS_RISK_PCT*100:.0f}% of wallet × {LEVERAGE}x leverage")

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

        # Fetch options chain
        log.info(f"🔭 Scanning options for {BASE_UNDERLYING}...")
        chain = self.api.get_options_chain(BASE_UNDERLYING)
        if not chain:
            log.warning("🏜️  No contracts found!"); return

        opt_type = "call" if sig == "BUY" else "put"
        matching = [c for c in chain if c["type"] == opt_type and c["tradeable"]]
        log.info(f"🗺️  Found {len(matching)} tradeable {opt_type.upper()}s")

        if not matching:
            log.warning(f"💀 No tradeable {opt_type.upper()} options!"); return

        # Greeks-based selection
        target = spot * 1.01 if sig == "BUY" else spot * 0.99
        for c in matching:
            d_abs = abs(c.get("delta", 0)); gs = 0
            if 0.30 <= d_abs <= 0.50: gs += 20
            elif 0.20 <= d_abs <= 0.60: gs += 10
            if abs(c.get("gamma",0)) > 0.003: gs += 10
            if c.get("theta",0) > -50: gs += 10
            gs += max(0, 20 - (abs(c["strike"]-target)/spot)*100)
            c["greek_score"] = gs

        matching.sort(key=lambda x: x["greek_score"], reverse=True)
        best = matching[0]

        # Log top candidates
        log.info(f"🏹 TOP PREY (by Greeks):")
        for i, c in enumerate(matching[:3]):
            rank = "👑" if i==0 else f"  {i+1}."
            log.info(f"    {rank} {c['symbol']} | GScore: {c['greek_score']:.0f}")
            log.info(f"       Δ={c.get('delta',0):+.4f} Γ={c.get('gamma',0):.6f} Θ={c.get('theta',0):.2f} IV={c.get('iv',0):.1f}%")

        log.info(f"🔥 THIS IS THE ONE — {best['symbol']}")
        self._open(best, sig)

    def _execute_hedge(self, spot):
        """CHOPPY MODE: Buy both a CALL and PUT to profit from volatility."""
        chain = self.api.get_options_chain(BASE_UNDERLYING)
        if not chain:
            log.warning("🏜️  No contracts for hedge!"); return

        target = spot * 1.005  # Near ATM

        for opt_type in ["call", "put"]:
            matching = [c for c in chain if c["type"] == opt_type and c["tradeable"]]
            if not matching:
                log.warning(f"    ⚠️  No tradeable {opt_type.upper()}s for hedge"); continue

            # Score by Greeks + proximity
            for c in matching:
                gs = 0; d_abs = abs(c.get("delta", 0))
                if 0.30 <= d_abs <= 0.50: gs += 20
                elif 0.20 <= d_abs <= 0.60: gs += 10
                if abs(c.get("gamma",0)) > 0.003: gs += 10
                if c.get("theta",0) > -50: gs += 10
                gs += max(0, 20 - (abs(c["strike"]-target)/spot)*100)
                c["greek_score"] = gs

            matching.sort(key=lambda x: x["greek_score"], reverse=True)
            best = matching[0]

            emoji = "📗" if opt_type == "call" else "📕"
            log.info(f"    {emoji} HEDGE LEG: {opt_type.upper()} — {best['symbol']}")
            log.info(f"       Δ={best.get('delta',0):+.4f} Θ={best.get('theta',0):.2f} | Strike: ${best['strike']:,.0f}")

            # Half wallet per leg
            self._open(best, "BUY", wallet_fraction=0.5)

        log.info("    🛡️ Hedge pair placed — profiting from volatility either direction!")

    def _open(self, bc, sig, wallet_fraction=1.0):
        # Refresh wallet for full sizing
        wallet = self.api.get_wallet_balance()
        available = wallet.get("available", self.wallet_balance) if wallet.get("available", 0) > 0 else self.wallet_balance

        lev_result = self.api.set_leverage(bc["product_id"], LEVERAGE)
        actual_leverage = lev_result.get("actual_leverage", LEVERAGE)

        # Delta Exchange: each option lot = 0.001 BTC
        # Premium is quoted per 1 BTC, so cost_per_lot = premium × 0.001
        LOT_SIZE = 0.001  # 0.001 BTC per contract on Delta Exchange
        notional = available * wallet_fraction * actual_leverage
        ep = bc["ask"] if bc["ask"] > 0 else bc["mark_price"]
        cost_per_lot = ep * LOT_SIZE  # Actual USD cost per lot
        qty = max(1, int(notional / max(cost_per_lot, 0.01)))

        frac_label = f" ({wallet_fraction*100:.0f}% of wallet)" if wallet_fraction < 1.0 else ""
        log.info(f"    💰 Wallet: ${available:,.2f} | Allocation: ${available*wallet_fraction:,.2f}{frac_label}")
        log.info(f"    💰 Notional ({actual_leverage}x leverage): ${notional:,.2f}")
        log.info(f"    💰 Premium: ${ep:.2f}/BTC | Cost/lot: ${cost_per_lot:.4f} | Qty: {qty} lots")
        log.info(f"    💰 Buying {qty}x @ ${ep:.4f}")
        log.info(f"    📤 {'PAPER' if PAPER_TRADE else 'LIVE'} order...")

        order = self.api.place_order(bc["product_id"], "buy", qty, bc["symbol"])

        if order.get("success"):
            stop = ep * (1-TRAILING_STOP_PCT)
            pos = OptionsPosition(
                contract=OptionContract(symbol=bc["symbol"], underlying=BASE_UNDERLYING, expiry="",
                    strike=bc["strike"], option_type=bc["type"], premium=ep,
                    delta=bc.get("delta",0), implied_vol=bc.get("iv",0)/100 if bc.get("iv",0)>1 else 0.5,
                    open_interest=0, bid=bc["bid"], ask=bc["ask"], spread_pct=bc["spread_pct"],
                    product_id=bc["product_id"]),
                side="buy", quantity=qty, entry_premium=ep,
                entry_time=datetime.now(pytz.UTC).isoformat(),
                stop_premium=stop, target_premium=0,
                order_id=str(order["result"]["id"]), peak_premium=ep, leverage=LEVERAGE)

            self.positions.append(pos)
            self.last_trade_date = datetime.now(pytz.UTC).date()

            log.info(f"══════════════════════════════════════════════")
            log.info(f"🏆 TODAY'S KILL — PREY CAPTURED!")
            log.info(f"    └─ {qty}x {bc['symbol']}")
            log.info(f"    └─ Entry: ${ep:.4f} | Notional: ${ep*qty:,.2f}")
            log.info(f"    └─ Stop: ${stop:.4f} ({TRAILING_STOP_PCT*100:.0f}% trail)")
            log.info(f"    └─ Leverage: {LEVERAGE}x")
            log.info(f"══════════════════════════════════════════════")
            log.info("🏆 Done for today. Monitoring until exit.")
            _save_json(POSITIONS_FILE, [asdict(p) for p in self.positions])
        else:
            log.error(f"💥 ORDER REJECTED: {order}")

    def _monitor(self, pos):
        sym = pos.contract.symbol if not isinstance(pos.contract, dict) else pos.contract.get("symbol","")
        pid = pos.contract.product_id if not isinstance(pos.contract, dict) else pos.contract.get("product_id",0)

        curr = self.api.get_option_premium(sym)
        if not curr: return

        p = curr.get("mark_price", pos.entry_premium)
        if p <= 0: return

        if p > pos.peak_premium:
            pos.peak_premium = p
            pos.stop_premium = p * (1-TRAILING_STOP_PCT)
            log.info(f"    📈 NEW PEAK: ${p:.4f} → Stop: ${pos.stop_premium:.4f}")

        pnl_pct = ((p-pos.entry_premium)/pos.entry_premium)*100
        emoji = "📈" if pnl_pct > 0 else "📉"
        log.info(f"    {emoji} {sym}: ${p:.4f} ({pnl_pct:+.1f}% | {pnl_pct*LEVERAGE:+.0f}% lev) | Peak: ${pos.peak_premium:.4f} | Stop: ${pos.stop_premium:.4f}")

        if p <= pos.stop_premium:
            reason = f"TRAILING_STOP ({pnl_pct:+.1f}%)"
            log.info(f"    {'🎉' if pnl_pct>0 else '🩸'} {reason}")
            self._close(pos, p, reason); return

        try:
            entry_dt = datetime.fromisoformat(pos.entry_time.replace("Z","+00:00"))
            if (datetime.now(pytz.UTC)-entry_dt).total_seconds()/3600 >= MAX_HOLD_HOURS:
                self._close(pos, p, f"MAX_HOLD_{MAX_HOLD_HOURS}H"); return
        except: pass

    def _close(self, pos, p, reason):
        sym = pos.contract.symbol if not isinstance(pos.contract, dict) else pos.contract.get("symbol","")
        pid = pos.contract.product_id if not isinstance(pos.contract, dict) else pos.contract.get("product_id",0)
        self.api.place_order(pid, "sell", pos.quantity, sym)

        pnl = (p-pos.entry_premium)*pos.quantity
        pnl_pct = ((p-pos.entry_premium)/pos.entry_premium)*100
        pos.status="closed"; pos.exit_premium=p; pos.exit_reason=reason; pos.pnl=pnl

        e = "💰" if pnl>0 else "💸"
        log.info(f"══════════════════════════════════════════════")
        log.info(f"{e} HUNT COMPLETE — {reason}")
        log.info(f"    └─ Entry: ${pos.entry_premium:.4f} → Exit: ${p:.4f}")
        log.info(f"    └─ Peak: ${pos.peak_premium:.4f}")
        log.info(f"    └─ P&L: ${pnl:.4f} ({pnl_pct:+.1f}% | {pnl_pct*LEVERAGE:+.1f}% leveraged)")
        log.info(f"══════════════════════════════════════════════")

        _save_json(POSITIONS_FILE, [asdict(p) for p in self.positions])
        history = _load_json(TRADE_HISTORY_FILE, [])
        history.append({"symbol":sym,"entry":pos.entry_premium,"exit":p,"peak":pos.peak_premium,
            "pnl":pnl,"pnl_pct":pnl_pct,"reason":reason,"leverage":LEVERAGE,
            "entry_time":pos.entry_time,"exit_time":datetime.now(pytz.UTC).isoformat()})
        _save_json(TRADE_HISTORY_FILE, history)

if __name__ == "__main__":
    bot = OptionsTradingBot()
    bot.run()
