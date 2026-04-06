"""
╔══════════════════════════════════════════════════════════════════════╗
║          DELTA EXCHANGE — CRYPTO OPTIONS BOT v3.1                   ║
║          Fixed Signal Engine · Spot Fallback · Paper-First          ║
║                                                                      ║
║  Root cause fixed: candle resolution format ("15m" not "15")         ║
║  Fallback added: Signal from Spot price if candles are missing       ║
╚══════════════════════════════════════════════════════════════════════╝
"""

import requests, numpy as np, math, time, json, logging, os, hmac, hashlib, pytz
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any
from dotenv import load_dotenv
from pathlib import Path

load_dotenv()

# ── CONFIG ──
API_KEY        = os.getenv("DELTA_API_KEY", "")
API_SECRET     = os.getenv("DELTA_API_SECRET", "")
BASE_URL       = os.getenv("DELTA_BASE_URL", "https://cdn-ind.testnet.deltaex.org")
TG_TOKEN       = os.getenv("TG_TOKEN", "")
TG_CHAT_ID     = os.getenv("TG_CHAT_ID", "")

# ⚠️ SETTINGS FOR TESTNET VERIFICATION
FORCE_PAPER_TRADE = False   # Set to False to allow API orders on Testnet
PAPER_TRADE       = True if FORCE_PAPER_TRADE else os.getenv("PAPER_TRADE", "true").lower() == "true"
OPTIONS_MIN_SCORE = 10      # Lowered for immediate testing

LEVERAGE          = int(os.getenv("LEVERAGE", "100"))
OPTIONS_RISK_PCT  = 0.005
DAILY_LOSS_LIMIT  = 0.03
CLOSE_BEFORE_EXPIRY_MINS = 30
MAX_HOLD_HOURS    = 4
CYCLE_INTERVAL    = 300
BASE_UNDERLYING   = os.getenv("BASE_UNDERLYING", "BTC")

DATA_DIR = Path(os.getenv("BOT_DATA_DIR", "./bot_data"))
DATA_DIR.mkdir(exist_ok=True)
POSITIONS_FILE     = DATA_DIR / "positions.json"
TRADE_HISTORY_FILE = DATA_DIR / "trade_history.json"
PERFORMANCE_FILE   = DATA_DIR / "performance_report.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.FileHandler("options_bot_log.txt", encoding="utf-8"),
        logging.StreamHandler()
    ]
)
log = logging.getLogger(__name__)

# ══════════════════════════════════════════════════════════════
# DATA CLASSES & PERSISTENCE
# ══════════════════════════════════════════════════════════════
def _save_json(filepath: Path, data):
    try:
        with open(filepath, "w") as f:
            json.dump(data, f, indent=2, default=str)
    except: pass

def _load_json(filepath: Path, default=None):
    if default is None: default = []
    try:
        if filepath.exists():
            with open(filepath, "r") as f: return json.load(f)
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
    contract: OptionContract; side: str; quantity: int
    entry_premium: float; entry_time: str
    stop_premium: float; target_premium: float
    order_id: str = ""; exit_premium: float = 0.0
    exit_reason: str = ""; pnl: float = 0.0
    status: str = "open"; leverage: int = LEVERAGE
    peak_premium: float = 0.0

# ══════════════════════════════════════════════════════════════
# DELTA API
# ══════════════════════════════════════════════════════════════
class DeltaAPI:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json", "User-Agent": "bot-v3.1"})

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

    def get_candles(self, symbol="BTCUSD", resolution="15m", limit=30) -> list:
        try:
            r = self.session.get(f"{BASE_URL}/v2/history/candles", 
                                 params={"resolution": resolution, "symbol": symbol, "limit": limit},
                                 timeout=10).json()
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

    def get_options_chain(self, underlying="BTC") -> list:
        try:
            r = self.session.get(f"{BASE_URL}/v2/tickers", 
                                 params={"contract_types": "call_options,put_options", "underlying_asset_symbols": underlying},
                                 timeout=10).json()
            if not r.get("success"): return []
            options = []
            for t in r.get("result", []):
                q = t.get("quotes") or {}
                bid, ask = float(q.get("best_bid", 0)), float(q.get("best_ask", 0))
                options.append({
                    "symbol": t.get("symbol"), "product_id": t.get("product_id"),
                    "strike": float(t.get("strike_price", 0)),
                    "type": "call" if "call" in t.get("contract_type", "") else "put",
                    "mark_price": float(t.get("mark_price", 0)),
                    "bid": bid, "ask": ask, "spread_pct": (ask-bid)/ask if ask > 0 else 1.0,
                    "tradeable": bid > 0 and (ask-bid)/ask < 0.20
                })
            return options
        except: return []

    def place_order(self, product_id, side, size, symbol=""):
        if PAPER_TRADE:
            log.info(f"📝 PAPER: {side.upper()} {size}x {symbol}")
            return {"success": True, "result": {"id": f"paper_{int(time.time())}"}}
        try:
            path = "/v2/orders"
            body = json.dumps({"product_id": product_id, "side": side, "size": size, "order_type": "market_order"})
            headers = self._sign("POST", path, "", body)
            return self.session.post(f"{BASE_URL}{path}", data=body, headers=headers, timeout=10).json()
        except: return {}

# ══════════════════════════════════════════════════════════════
# SIGNAL ENGINE — With Spot Fallback
# ══════════════════════════════════════════════════════════════
class SignalEngine:
    def __init__(self, api: DeltaAPI):
        self.api = api
        self.last_spots = []

    def evaluate(self, symbol="BTCUSD"):
        spot = self.api.get_spot_price(symbol)
        if spot > 0: self.last_spots.append(spot)
        if len(self.last_spots) > 10: self.last_spots.pop(0)

        candles = self.api.get_candles(symbol, "15m", 30)
        
        if not candles:
            log.warning("⚠️ No candle data — Using Spot Price Fallback")
            if len(self.last_spots) < 2: return "NEUTRAL", 0, "no_data", False, spot
            # Simple momentum from spot price history
            change = (self.last_spots[-1] - self.last_spots[0]) / self.last_spots[0]
            score = 50 + (change * 1000) # exaggerate movement
            score = max(0, min(100, score))
            sig = "BUY" if score > 55 else "SELL" if score < 45 else "NEUTRAL"
            return sig, score, "spot_momentum", False, spot

        closes = [float(c['close']) for c in candles]
        rsi = self._rsi(closes)
        ema9, ema21 = self._ema(closes, 9), self._ema(closes, 21)
        
        score = 50
        # RSI component
        if rsi < 40: score += 15
        elif rsi > 60: score -= 15
        # EMA component
        if ema9[-1] > ema21[-1]: score += 15
        else: score -= 15
        # Momentum
        if closes[-1] > closes[-5]: score += 10
        else: score -= 10

        score = max(0, min(100, score))
        sig = "BUY" if score >= 60 else "SELL" if score <= 40 else "NEUTRAL"
        return sig, score, "candle_mixed", False, spot

    def _ema(self, data, p):
        k = 2/(p+1); ema = [data[0]]
        for v in data[1:]: ema.append(v*k + ema[-1]*(1-k))
        return ema

    def _rsi(self, closes, p=14):
        if len(closes) < p+1: return 50
        deltas = np.diff(closes)
        gains = np.where(deltas > 0, deltas, 0)
        losses = np.where(deltas < 0, -deltas, 0)
        avg_g = np.mean(gains[:p]); avg_l = np.mean(losses[:p])
        for i in range(p, len(gains)):
            avg_g = (avg_g * (p-1) + gains[i]) / p
            avg_l = (avg_l * (p-1) + losses[i]) / p
        if avg_l == 0: return 100
        return 100 - (100 / (1 + avg_g/avg_l))

# ══════════════════════════════════════════════════════════════
# MAIN BOT CLASS
# ══════════════════════════════════════════════════════════════
class OptionsTradingBot:
    def __init__(self, capital=15000):
        self.api = DeltaAPI()
        self.signals = SignalEngine(self.api)
        self.positions: List[OptionsPosition] = []
        
        log.info("🎯 BOT STARTING...")
        wallet = self.api.get_wallet_balance()
        self.capital = wallet.get("available", capital) if wallet.get("available", 0) > 0 else capital
        log.info(f"💰 Capital: ${self.capital:,.2f} | Mode: {'PAPER' if PAPER_TRADE else 'LIVE/TESTNET'}")
        self._load()

    def _load(self):
        data = _load_json(POSITIONS_FILE, [])
        for d in data:
            if d.get("status") == "open": 
                # Reconstruct stripped objects if needed
                self.positions.append(OptionsPosition(**d))

    def run(self):
        while True:
            try:
                self._cycle()
                log.info(f"⏳ Sleeping {CYCLE_INTERVAL//60}m...\n")
                time.sleep(CYCLE_INTERVAL)
            except KeyboardInterrupt: break
            except Exception as e: log.error(f"Error: {e}"); time.sleep(60)

    def _cycle(self):
        ts = datetime.now(pytz.UTC).strftime('%H:%M:%S UTC')
        log.info(f"── Cycle {ts} ──────────────")
        
        # Monitor
        for pos in [p for p in self.positions if p.status == "open"]:
            self._monitor(pos)

        # New trades
        if len([p for p in self.positions if p.status == "open"]) >= 2: return
        
        sig, score, cond, near_fib, spot = self.signals.evaluate()
        log.info(f"📊 Signal: {sig} | Score: {score} | Spot: ${spot:,.0f}")

        if score < OPTIONS_MIN_SCORE or sig == "NEUTRAL": return

        chain = self.api.get_options_chain(BASE_UNDERLYING)
        if not chain: return

        # Select strike (simplified)
        target = spot * 1.01 if sig == "BUY" else spot * 0.99
        best = None; min_diff = 999999
        for c in chain:
            if c["type"] == ("call" if sig == "BUY" else "put") and c["tradeable"]:
                diff = abs(c["strike"] - target)
                if diff < min_diff: min_diff = diff; best = c
        
        if best: self._open(best, sig)

    def _open(self, bc, sig):
        ep = bc["ask"] if bc["ask"] > 0 else bc["mark_price"]
        budget = self.capital * OPTIONS_RISK_PCT
        qty = max(1, int(budget / max(ep, 0.01)))
        
        order = self.api.place_order(bc["product_id"], "buy", qty, bc["symbol"])
        if order.get("success"):
            pos = OptionsPosition(
                contract=OptionContract(symbol=bc["symbol"], underlying=BASE_UNDERLYING, expiry="", strike=bc["strike"], 
                                        option_type=bc["type"], premium=ep, delta=0, implied_vol=0.5, open_interest=0, 
                                        bid=bc["bid"], ask=bc["ask"], spread_pct=bc["spread_pct"], product_id=bc["product_id"]),
                side="buy", quantity=qty, entry_premium=ep, entry_time=datetime.now(pytz.UTC).isoformat(),
                stop_premium=ep*0.5, target_premium=ep*2.0, order_id=str(order["result"]["id"]), peak_premium=ep)
            self.positions.append(pos)
            log.info(f"🎯 OPENED: {qty}x {bc['symbol']} @ ${ep:.4f}")
            _save_json(POSITIONS_FILE, [asdict(p) for p in self.positions])

    def _monitor(self, pos):
        curr = self.api.get_option_premium(pos.contract.symbol)
        if not curr: return
        p = curr.get("mark_price", pos.entry_premium)
        if p <= pos.stop_premium or p >= pos.target_premium:
            self._close(pos, p, "AUTO_EXIT")

    def _close(self, pos, p, reason):
        self.api.place_order(pos.contract.product_id, "sell", pos.quantity, pos.contract.symbol)
        pos.status = "closed"; pos.exit_premium = p; pos.exit_reason = reason
        log.info(f"✅ CLOSED: {pos.contract.symbol} @ ${p:.4f} ({reason})")
        _save_json(POSITIONS_FILE, [asdict(p) for p in self.positions])

from dataclasses import asdict
if __name__ == "__main__":
    bot = OptionsTradingBot()
    bot.run()
