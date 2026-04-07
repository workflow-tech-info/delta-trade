"""
╔══════════════════════════════════════════════════════════════════════╗
║          DELTA EXCHANGE — CRYPTO OPTIONS BOT v4.0                   ║
║          Multi-Timeframe · Greeks · Pattern Detection · Trailing Stop ║
║                                                                      ║
║  Features:                                                           ║
║  • Option Greeks analysis (Δ, Γ, Θ, V, IV) for contract selection   ║
║  • Multi-timeframe confirmation (5m, 10m, 15m, 1h)                  ║
║  • Candlestick pattern detection (Engulfing, Hammer, Doji, etc.)    ║
║  • Chart pattern detection (Double Top/Bottom, Flags, Wedges)       ║
║  • Dynamic trailing stop loss (50% from peak, no profit ceiling)    ║
║  • 50x leverage via API                                              ║
║  • Max 1 active position at a time                                   ║
║  • Hunter-style logging for easy debugging                           ║
╚══════════════════════════════════════════════════════════════════════╝
"""

# ── IMPORTS ──
import requests, numpy as np, math, time, json, logging, os, hmac, hashlib, pytz
from datetime import datetime, timedelta
from dataclasses import dataclass, field, asdict
from typing import Optional, List, Dict, Any
from dotenv import load_dotenv
from pathlib import Path

# Load secrets from .env file
load_dotenv()

# ══════════════════════════════════════════════════════════════
# CONFIGURATION — All tuneable settings in one place
# ══════════════════════════════════════════════════════════════

# API credentials from .env
API_KEY        = os.getenv("DELTA_API_KEY", "")
API_SECRET     = os.getenv("DELTA_API_SECRET", "")
BASE_URL       = os.getenv("DELTA_BASE_URL", "https://cdn-ind.testnet.deltaex.org")
TG_TOKEN       = os.getenv("TG_TOKEN", "")
TG_CHAT_ID     = os.getenv("TG_CHAT_ID", "")

# Paper trading toggle — set FORCE_PAPER_TRADE=True to simulate without API
FORCE_PAPER_TRADE = False
PAPER_TRADE       = True if FORCE_PAPER_TRADE else os.getenv("PAPER_TRADE", "true").lower() == "true"

# ── TRADING PARAMETERS ──
# The minimum score (0-100) needed to trigger a trade entry.
# Higher = fewer but higher quality trades. 55 is a good balanced default.
OPTIONS_MIN_SCORE = 55

# Leverage to set via the Delta API before each trade.
# Options have natural leverage (~70x), this controls margin allocation.
LEVERAGE          = int(os.getenv("LEVERAGE", "50"))

# Risk per trade: % of total capital allocated per trade.
# 0.5% is conservative. Increase to 1-2% for more aggressive sizing.
OPTIONS_RISK_PCT  = 0.005

# Maximum number of positions held at the same time. 
# Set to 1 = one trade at a time, full focus.
MAX_POSITIONS     = 1

# Trailing stop: when price rises, the stop follows.
# 0.50 = exit if price drops 50% from the highest point reached.
TRAILING_STOP_PCT = 0.50

# How often the bot runs a cycle (in seconds). 600 = 10 minutes.
CYCLE_INTERVAL    = 600

# The underlying asset we are trading options on.
BASE_UNDERLYING   = os.getenv("BASE_UNDERLYING", "BTC")

# Close positions 30 minutes before option expiry
CLOSE_BEFORE_EXPIRY_MINS = 30

# Maximum hours to hold any position (safety limit)
MAX_HOLD_HOURS    = 12

# ── MULTI-TIMEFRAME SETTINGS ──
# Primary timeframe for signal generation
PRIMARY_TIMEFRAME = "10m"
# Confirmation timeframes — at least 3 of 4 must agree
CONFIRM_TIMEFRAMES = ["5m", "10m", "15m", "1h"]
# Number of candles to fetch per timeframe
CANDLE_LIMIT = 30

# ── DATA STORAGE ──
DATA_DIR = Path(os.getenv("BOT_DATA_DIR", "./bot_data"))
DATA_DIR.mkdir(exist_ok=True)
POSITIONS_FILE     = DATA_DIR / "positions.json"
TRADE_HISTORY_FILE = DATA_DIR / "trade_history.json"
PERFORMANCE_FILE   = DATA_DIR / "performance_report.json"

# ── LOGGING SETUP ──
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
    """Save data to a JSON file. Silently ignores errors."""
    try:
        with open(filepath, "w") as f:
            json.dump(data, f, indent=2, default=str)
    except: pass

def _load_json(filepath: Path, default=None):
    """Load data from a JSON file. Returns default if file doesn't exist."""
    if default is None: default = []
    try:
        if filepath.exists():
            with open(filepath, "r") as f: return json.load(f)
    except: pass
    return default

@dataclass
class OptionContract:
    """Blueprint for an options contract's details."""
    symbol: str; underlying: str; expiry: str; strike: float
    option_type: str; premium: float; delta: float; implied_vol: float
    open_interest: int; bid: float; ask: float; spread_pct: float
    product_id: int = 0; expiry_datetime: str = ""

@dataclass
class OptionsPosition:
    """Blueprint for an active or closed trade position."""
    contract: Any  # OptionContract or dict (flexible for JSON loading)
    side: str; quantity: int
    entry_premium: float; entry_time: str
    stop_premium: float; target_premium: float
    order_id: str = ""; exit_premium: float = 0.0
    exit_reason: str = ""; pnl: float = 0.0
    status: str = "open"; leverage: int = LEVERAGE
    peak_premium: float = 0.0  # Tracks highest price for trailing stop

# ══════════════════════════════════════════════════════════════
# DELTA API HANDLER
# Handles all communication with the Delta Exchange servers.
# ══════════════════════════════════════════════════════════════
class DeltaAPI:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json", "User-Agent": "bot-v4.0"})

    def _sign(self, method, path, query_string="", payload=""):
        """Create cryptographic signature for authenticated API calls."""
        ts = str(int(time.time()))
        message = method + ts + path + query_string + payload
        sig = hmac.new(API_SECRET.encode(), message.encode(), hashlib.sha256).hexdigest()
        return {"api-key": API_KEY, "timestamp": ts, "signature": sig, "Content-Type": "application/json"}

    def get_spot_price(self, symbol="BTCUSD") -> float:
        """Fetch current BTC spot price."""
        try:
            r = self.session.get(f"{BASE_URL}/v2/tickers/{symbol}", timeout=10).json()
            if r.get("success"):
                return float(r["result"].get("spot_price") or r["result"].get("mark_price") or 0)
        except: pass
        return 0.0

    def get_candles(self, symbol="BTCUSD", resolution="10m", limit=30) -> list:
        """Fetch OHLCV candle data for a given timeframe."""
        try:
            r = self.session.get(f"{BASE_URL}/v2/history/candles", 
                                 params={"resolution": resolution, "symbol": symbol, "limit": limit},
                                 timeout=10).json()
            if r.get("success"): return r.get("result", [])
        except: pass
        return []

    def get_wallet_balance(self) -> dict:
        """Fetch wallet balance from the exchange."""
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

    def set_leverage(self, product_id, leverage=50):
        """Set leverage for a product via the Delta API.
        Endpoint: POST /v2/products/{product_id}/orders/leverage
        Body: {"leverage": 50}
        """
        if PAPER_TRADE:
            log.info(f"    📝 PAPER: Leverage set to {leverage}x for product {product_id}")
            return {"success": True, "result": {"leverage": leverage}}
        try:
            path = f"/v2/products/{product_id}/orders/leverage"
            body = json.dumps({"leverage": leverage})
            headers = self._sign("POST", path, "", body)
            r = self.session.post(f"{BASE_URL}{path}", data=body, headers=headers, timeout=10).json()
            if r.get("success"):
                log.info(f"    ⚙️  Leverage confirmed: {r['result'].get('leverage', leverage)}x | Margin: ${r['result'].get('order_margin', '?')}")
            else:
                log.warning(f"    ⚠️  Leverage API response: {r}")
            return r
        except Exception as e:
            log.warning(f"    ⚠️  Leverage API error: {e}")
            return {}

    def get_options_chain(self, underlying="BTC") -> list:
        """Fetch all available options contracts from the exchange."""
        try:
            log.info(f"    📡 Fetching options chain for {underlying}...")
            r = self.session.get(f"{BASE_URL}/v2/tickers", 
                                 params={"contract_types": "call_options,put_options", "underlying_asset_symbols": underlying},
                                 timeout=10).json()
            
            if r.get("success") and r.get("result"):
                options = []
                for t in r.get("result", []):
                    try:
                        q = t.get("quotes") or {}
                        g = t.get("greeks") or {}
                        bid = float(q.get("best_bid") or 0)
                        ask = float(q.get("best_ask") or 0)
                        strike = float(t.get("strike_price") or 0)
                        mark = float(t.get("mark_price") or 0)
                        spread_pct = (ask-bid)/ask if ask > 0 else 1.0
                        tradeable = bid > 0 and spread_pct < 0.20
                        
                        # ── PARSE GREEKS ──
                        delta = float(g.get("delta") or 0)
                        gamma = float(g.get("gamma") or 0)
                        theta = float(g.get("theta") or 0)
                        vega  = float(g.get("vega") or 0)
                        rho   = float(g.get("rho") or 0)
                        iv    = float(t.get("mark_vol") or 0)  # Implied volatility
                        
                        options.append({
                            "symbol": t.get("symbol"), "product_id": t.get("product_id"),
                            "strike": strike,
                            "type": "call" if "call" in t.get("contract_type", "") else "put",
                            "mark_price": mark, "bid": bid, "ask": ask,
                            "spread_pct": spread_pct, "tradeable": tradeable,
                            # Greeks — the prey's vital stats
                            "delta": delta, "gamma": gamma, "theta": theta,
                            "vega": vega, "rho": rho, "iv": iv
                        })
                    except:
                        continue
                log.info(f"    ✅ Parsed {len(options)} options (with Greeks) from {len(r.get('result',[]))} raw contracts")
                return options
            
            log.warning("    🏜️  No options contracts found on this exchange")
            return []
        except Exception as e:
            log.error(f"    💥 API error in get_options_chain: {e}")
            return []

    def place_order(self, product_id, side, size, symbol=""):
        """Place a market order on the exchange."""
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
            log.error(f"    💥 Order API error: {e}")
            return {}

    def get_option_premium(self, symbol):
        """Get the current mark price of a specific option contract."""
        try:
            r = self.session.get(f"{BASE_URL}/v2/tickers/{symbol}", timeout=10).json()
            if not r.get("success"): return {}
            res = r.get("result", {})
            return {"mark_price": float(res.get("mark_price") or 0)}
        except: return {}


# ══════════════════════════════════════════════════════════════
# CANDLESTICK PATTERN DETECTOR
# Analyzes OHLCV data to detect bullish/bearish patterns.
# Each pattern returns a score modifier.
# ══════════════════════════════════════════════════════════════
class PatternDetector:
    """Detects candlestick and chart patterns from OHLCV candle data.
    
    Returns a list of detected patterns with their bias (bullish/bearish)
    and a combined score modifier (+/- points).
    """
    
    def analyze(self, candles):
        """Main analysis function. Takes list of OHLCV candles.
        Returns: (score_modifier, patterns_found_list, bias_string)
        
        Each candle expected format: {"open": X, "high": X, "low": X, "close": X}
        """
        if not candles or len(candles) < 5:
            return 0, [], "insufficient_data"
        
        patterns = []
        
        # ── CANDLESTICK PATTERNS (last 3 candles) ──
        patterns += self._detect_engulfing(candles)
        patterns += self._detect_hammer(candles)
        patterns += self._detect_doji(candles)
        patterns += self._detect_morning_evening_star(candles)
        patterns += self._detect_three_soldiers_crows(candles)
        patterns += self._detect_harami(candles)
        
        # ── CHART PATTERNS (need more candles) ──
        if len(candles) >= 15:
            patterns += self._detect_double_top_bottom(candles)
        if len(candles) >= 10:
            patterns += self._detect_flag(candles)
        
        # Calculate combined score
        total_score = sum(p["score"] for p in patterns)
        
        # Determine overall bias
        bullish_count = sum(1 for p in patterns if p["bias"] == "bullish")
        bearish_count = sum(1 for p in patterns if p["bias"] == "bearish")
        
        if bullish_count > bearish_count:
            bias = "bullish"
        elif bearish_count > bullish_count:
            bias = "bearish"
        else:
            bias = "neutral"
        
        return total_score, patterns, bias
    
    def _body(self, c):
        """Returns the absolute body size of a candle."""
        return abs(float(c["close"]) - float(c["open"]))
    
    def _upper_wick(self, c):
        """Returns the upper shadow/wick length."""
        return float(c["high"]) - max(float(c["open"]), float(c["close"]))
    
    def _lower_wick(self, c):
        """Returns the lower shadow/wick length."""
        return min(float(c["open"]), float(c["close"])) - float(c["low"])
    
    def _is_bullish(self, c):
        """Is this candle green/bullish (close > open)?"""
        return float(c["close"]) > float(c["open"])
    
    def _range(self, c):
        """Total range of a candle (high - low)."""
        return float(c["high"]) - float(c["low"])
    
    # ── ENGULFING PATTERN ──
    # Bullish: Small red candle followed by large green candle that engulfs it
    # Bearish: Small green candle followed by large red candle that engulfs it
    def _detect_engulfing(self, candles):
        patterns = []
        c1, c2 = candles[-2], candles[-1]  # Previous and current candle
        
        # Bullish Engulfing
        if (not self._is_bullish(c1) and self._is_bullish(c2) and
            float(c2["close"]) > float(c1["open"]) and float(c2["open"]) < float(c1["close"])):
            patterns.append({"name": "🟢 Bullish Engulfing", "bias": "bullish", "score": 10})
        
        # Bearish Engulfing
        if (self._is_bullish(c1) and not self._is_bullish(c2) and
            float(c2["close"]) < float(c1["open"]) and float(c2["open"]) > float(c1["close"])):
            patterns.append({"name": "🔴 Bearish Engulfing", "bias": "bearish", "score": -10})
        
        return patterns
    
    # ── HAMMER / HANGING MAN ──
    # Hammer (bullish): Small body at top, long lower wick (2x+ body), after downtrend
    # Hanging Man (bearish): Same shape but after uptrend
    def _detect_hammer(self, candles):
        patterns = []
        c = candles[-1]
        body = self._body(c)
        lower = self._lower_wick(c)
        upper = self._upper_wick(c)
        rng = self._range(c)
        
        if rng == 0 or body == 0: return patterns
        
        # Hammer shape: long lower wick, small upper wick
        if lower >= body * 2 and upper < body * 0.5:
            # Check trend: are last 5 candles trending down? (hammer = bullish reversal)
            closes = [float(x["close"]) for x in candles[-6:-1]]
            if len(closes) >= 4 and closes[-1] < closes[0]:
                patterns.append({"name": "🟢 Hammer", "bias": "bullish", "score": 8})
            else:
                patterns.append({"name": "🔴 Hanging Man", "bias": "bearish", "score": -5})
        
        # Inverted hammer / shooting star: long upper wick, small lower wick
        if upper >= body * 2 and lower < body * 0.5:
            closes = [float(x["close"]) for x in candles[-6:-1]]
            if len(closes) >= 4 and closes[-1] < closes[0]:
                patterns.append({"name": "🟢 Inverted Hammer", "bias": "bullish", "score": 6})
            else:
                patterns.append({"name": "🔴 Shooting Star", "bias": "bearish", "score": -8})
        
        return patterns
    
    # ── DOJI ──
    # Body is very tiny compared to range. Signals indecision.
    def _detect_doji(self, candles):
        patterns = []
        c = candles[-1]
        body = self._body(c)
        rng = self._range(c)
        
        if rng == 0: return patterns
        
        # Doji: body < 10% of total range
        if body / rng < 0.10:
            upper = self._upper_wick(c)
            lower = self._lower_wick(c)
            
            if lower > upper * 2:
                patterns.append({"name": "🟢 Dragonfly Doji", "bias": "bullish", "score": 5})
            elif upper > lower * 2:
                patterns.append({"name": "🔴 Gravestone Doji", "bias": "bearish", "score": -5})
            else:
                patterns.append({"name": "⚪ Doji (Indecision)", "bias": "neutral", "score": 0})
        
        return patterns
    
    # ── MORNING STAR / EVENING STAR ──
    # 3-candle reversal patterns
    def _detect_morning_evening_star(self, candles):
        patterns = []
        if len(candles) < 3: return patterns
        
        c1, c2, c3 = candles[-3], candles[-2], candles[-1]
        body1, body2, body3 = self._body(c1), self._body(c2), self._body(c3)
        
        if body1 == 0: return patterns
        
        # Morning Star (bullish): Big red → Small body → Big green
        if (not self._is_bullish(c1) and body2 < body1 * 0.3 and 
            self._is_bullish(c3) and body3 > body1 * 0.5):
            patterns.append({"name": "🟢 Morning Star", "bias": "bullish", "score": 12})
        
        # Evening Star (bearish): Big green → Small body → Big red
        if (self._is_bullish(c1) and body2 < body1 * 0.3 and 
            not self._is_bullish(c3) and body3 > body1 * 0.5):
            patterns.append({"name": "🔴 Evening Star", "bias": "bearish", "score": -12})
        
        return patterns
    
    # ── THREE WHITE SOLDIERS / THREE BLACK CROWS ──
    # Three consecutive strong candles in the same direction
    def _detect_three_soldiers_crows(self, candles):
        patterns = []
        if len(candles) < 3: return patterns
        
        last3 = candles[-3:]
        
        # Three White Soldiers: 3 consecutive green candles, each closing higher
        if all(self._is_bullish(c) for c in last3):
            if (float(last3[1]["close"]) > float(last3[0]["close"]) and 
                float(last3[2]["close"]) > float(last3[1]["close"])):
                patterns.append({"name": "🟢 Three White Soldiers", "bias": "bullish", "score": 12})
        
        # Three Black Crows: 3 consecutive red candles, each closing lower
        if all(not self._is_bullish(c) for c in last3):
            if (float(last3[1]["close"]) < float(last3[0]["close"]) and 
                float(last3[2]["close"]) < float(last3[1]["close"])):
                patterns.append({"name": "🔴 Three Black Crows", "bias": "bearish", "score": -12})
        
        return patterns
    
    # ── HARAMI (inside bar) ──
    # Current candle body is completely within previous candle body
    def _detect_harami(self, candles):
        patterns = []
        c1, c2 = candles[-2], candles[-1]
        
        o1, c1c = float(c1["open"]), float(c1["close"])
        o2, c2c = float(c2["open"]), float(c2["close"])
        
        high1, low1 = max(o1, c1c), min(o1, c1c)
        high2, low2 = max(o2, c2c), min(o2, c2c)
        
        # Current candle completely inside previous
        if high2 < high1 and low2 > low1:
            if not self._is_bullish(c1) and self._is_bullish(c2):
                patterns.append({"name": "🟢 Bullish Harami", "bias": "bullish", "score": 6})
            elif self._is_bullish(c1) and not self._is_bullish(c2):
                patterns.append({"name": "🔴 Bearish Harami", "bias": "bearish", "score": -6})
        
        return patterns
    
    # ── DOUBLE TOP / DOUBLE BOTTOM ──
    # Two peaks/troughs at roughly the same level
    def _detect_double_top_bottom(self, candles):
        patterns = []
        highs = [float(c["high"]) for c in candles[-15:]]
        lows = [float(c["low"]) for c in candles[-15:]]
        
        if not highs or not lows: return patterns
        
        # Find two highest peaks
        h_sorted = sorted(enumerate(highs), key=lambda x: x[1], reverse=True)
        if len(h_sorted) >= 2:
            idx1, val1 = h_sorted[0]
            idx2, val2 = h_sorted[1]
            # Peaks must be at least 3 candles apart and within 1% of each other
            if abs(idx1 - idx2) >= 3 and abs(val1 - val2) / val1 < 0.01:
                # Current price near or below the valley between peaks = bearish
                patterns.append({"name": "🔴 Double Top", "bias": "bearish", "score": -10})
        
        # Find two lowest troughs
        l_sorted = sorted(enumerate(lows), key=lambda x: x[1])
        if len(l_sorted) >= 2:
            idx1, val1 = l_sorted[0]
            idx2, val2 = l_sorted[1]
            if abs(idx1 - idx2) >= 3 and abs(val1 - val2) / val1 < 0.01:
                patterns.append({"name": "🟢 Double Bottom", "bias": "bullish", "score": 10})
        
        return patterns
    
    # ── BULL/BEAR FLAG ──
    # Strong move followed by tight consolidation
    def _detect_flag(self, candles):
        patterns = []
        if len(candles) < 10: return patterns
        
        # Look at last 10 candles: first 5 = "pole", last 5 = "flag"
        pole = candles[-10:-5]
        flag = candles[-5:]
        
        pole_move = float(pole[-1]["close"]) - float(pole[0]["open"])
        flag_range = max(float(c["high"]) for c in flag) - min(float(c["low"]) for c in flag)
        pole_range = abs(pole_move)
        
        if pole_range == 0: return patterns
        
        # Flag should be tight (< 40% of pole range) = consolidation after move
        if flag_range < pole_range * 0.4:
            if pole_move > 0:
                patterns.append({"name": "🟢 Bullish Flag", "bias": "bullish", "score": 8})
            else:
                patterns.append({"name": "🔴 Bearish Flag", "bias": "bearish", "score": -8})
        
        return patterns


# ══════════════════════════════════════════════════════════════
# SIGNAL ENGINE v4.0
# Multi-timeframe analysis + pattern detection + technical indicators.
# ══════════════════════════════════════════════════════════════
class SignalEngine:
    def __init__(self, api: DeltaAPI):
        self.api = api
        self.patterns = PatternDetector()
        self.last_spots = []  # Spot price history for fallback

    def evaluate(self, symbol="BTCUSD"):
        """Primary evaluation function.
        Returns: (signal, score, method, near_fib, spot_price)
        
        Uses multi-timeframe confirmation:
        1. Get the primary signal from 10m candles
        2. Confirm with 5m, 15m, 1h timeframes
        3. Add pattern detection bonus/penalty
        4. Require at least 3 of 4 timeframes to agree
        """
        spot = self.api.get_spot_price(symbol)
        
        # Save spot for fallback
        if spot > 0: self.last_spots.append(spot)
        if len(self.last_spots) > 20: self.last_spots.pop(0)
        
        # ── MULTI-TIMEFRAME ANALYSIS ──
        log.info("    📊 MULTI-TIMEFRAME ANALYSIS:")
        tf_results = {}
        pattern_score = 0
        pattern_list = []
        
        for tf in CONFIRM_TIMEFRAMES:
            candles = self.api.get_candles(symbol, tf, CANDLE_LIMIT)
            
            if not candles:
                tf_results[tf] = {"direction": "neutral", "score": 50, "reason": "no_data"}
                log.info(f"        {tf:>4s}: ⚪ No data")
                continue
            
            # Calculate indicators for this timeframe
            closes = [float(c['close']) for c in candles]
            score = 50  # Start neutral
            
            # RSI
            rsi = self._rsi(closes)
            if rsi < 35: score += 15
            elif rsi < 45: score += 8
            elif rsi > 65: score -= 15
            elif rsi > 55: score -= 8
            
            # EMA crossover (9 vs 21)
            if len(closes) >= 21:
                ema9 = self._ema(closes, 9)
                ema21 = self._ema(closes, 21)
                if ema9[-1] > ema21[-1]: score += 10
                else: score -= 10
            
            # Momentum (current vs 5 periods ago)
            if len(closes) >= 5:
                if closes[-1] > closes[-5]: score += 5
                else: score -= 5
            
            # Pattern detection (only on primary timeframe)
            if tf == PRIMARY_TIMEFRAME:
                p_score, p_list, p_bias = self.patterns.analyze(candles)
                pattern_score = p_score
                pattern_list = p_list
                score += p_score
                
                if p_list:
                    log.info(f"        🕯️  PATTERNS on {tf}:")
                    for p in p_list:
                        log.info(f"            {p['name']} ({p['score']:+d} pts)")
            
            score = max(0, min(100, score))
            direction = "bullish" if score >= 55 else "bearish" if score <= 45 else "neutral"
            
            emoji = "🟢" if direction == "bullish" else "🔴" if direction == "bearish" else "⚪"
            log.info(f"        {tf:>4s}: {emoji} {direction:>8s} | Score: {score:.0f} | RSI: {rsi:.0f}")
            
            tf_results[tf] = {"direction": direction, "score": score, "reason": "candle_analysis"}
        
        # ── CONSENSUS CHECK ──
        # Count how many timeframes agree
        bullish_count = sum(1 for v in tf_results.values() if v["direction"] == "bullish")
        bearish_count = sum(1 for v in tf_results.values() if v["direction"] == "bearish")
        
        log.info(f"    📐 Consensus: {bullish_count} bullish | {bearish_count} bearish | {len(tf_results)-bullish_count-bearish_count} neutral")
        
        # Primary timeframe score (10m) is the base
        primary = tf_results.get(PRIMARY_TIMEFRAME, {"score": 50, "direction": "neutral"})
        final_score = primary["score"]
        
        # Need at least 3 of 4 to agree for a strong signal
        if bullish_count >= 3:
            sig = "BUY"
            # Boost score based on consensus strength
            final_score = min(100, final_score + (bullish_count - 2) * 5)
        elif bearish_count >= 3:
            sig = "SELL"
            final_score = max(0, final_score - (bearish_count - 2) * 5)
        else:
            sig = "NEUTRAL"
        
        # ── FALLBACK: No candle data at all ──
        if all(v["reason"] == "no_data" for v in tf_results.values()):
            log.warning("    ⚠️  All timeframes returned no data — Using Spot Fallback")
            if len(self.last_spots) < 2: 
                return "NEUTRAL", 0, "no_data", False, spot
            change = (self.last_spots[-1] - self.last_spots[0]) / self.last_spots[0]
            final_score = 50 + (change * 1000)
            final_score = max(0, min(100, final_score))
            sig = "BUY" if final_score >= 55 else "SELL" if final_score <= 45 else "NEUTRAL"
            return sig, final_score, "spot_fallback", False, spot
        
        return sig, final_score, "multi_tf_patterns", False, spot

    def _ema(self, data, p):
        """Exponential Moving Average."""
        k = 2/(p+1); ema = [data[0]]
        for v in data[1:]: ema.append(v*k + ema[-1]*(1-k))
        return ema

    def _rsi(self, closes, p=14):
        """Relative Strength Index."""
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
# Controls the loop, monitors positions with trailing stop,
# enforces max 1 position, sets leverage, and directs trading.
# ══════════════════════════════════════════════════════════════
class OptionsTradingBot:
    def __init__(self, capital=15000):
        self.api = DeltaAPI()
        self.signals = SignalEngine(self.api)
        self.positions: List[OptionsPosition] = []
        
        log.info("🎯 BOT v4.0 STARTING...")
        log.info(f"    ⚙️  Config: Cycle={CYCLE_INTERVAL}s | Score≥{OPTIONS_MIN_SCORE} | Leverage={LEVERAGE}x | MaxPos={MAX_POSITIONS}")
        log.info(f"    ⚙️  Trailing Stop: {TRAILING_STOP_PCT*100:.0f}% from peak | Max Hold: {MAX_HOLD_HOURS}h")
        log.info(f"    ⚙️  Timeframes: {', '.join(CONFIRM_TIMEFRAMES)} | Primary: {PRIMARY_TIMEFRAME}")
        
        wallet = self.api.get_wallet_balance()
        self.capital = wallet.get("available", capital) if wallet.get("available", 0) > 0 else capital
        log.info(f"    💰 Capital: ${self.capital:,.2f} | Mode: {'PAPER' if PAPER_TRADE else 'LIVE/TESTNET'}")
        
        self._load()

    def _load(self):
        """Load saved positions from disk, properly reconstructing OptionContract objects."""
        data = _load_json(POSITIONS_FILE, [])
        for d in data:
            if d.get("status") == "open":
                try:
                    # The 'contract' field is saved as a dict — reconstruct it
                    contract_data = d.pop("contract", {})
                    if isinstance(contract_data, dict):
                        contract = OptionContract(**contract_data)
                    else:
                        contract = contract_data
                    pos = OptionsPosition(contract=contract, **d)
                    self.positions.append(pos)
                    log.info(f"    📂 Loaded position: {contract.symbol} | Entry: ${pos.entry_premium:.4f}")
                except Exception as e:
                    log.warning(f"    ⚠️  Could not load position: {e}")

    def run(self):
        """Main infinite loop."""
        log.info("═══════════════════════════════════════════════════")
        log.info("🐺  THE HUNT BEGINS — v4.0 Multi-Timeframe Engine")
        log.info("═══════════════════════════════════════════════════")
        while True:
            try:
                self._cycle()
                mins = CYCLE_INTERVAL // 60
                secs = CYCLE_INTERVAL % 60
                log.info(f"💤 Resting {mins}m {secs}s before next hunt...\n")
                time.sleep(CYCLE_INTERVAL)
            except KeyboardInterrupt: 
                log.info("🛑 Hunter called off — shutting down gracefully.")
                break
            except Exception as e: 
                log.error(f"🩸 Wounded! Error: {e} — recovering in 60s...")
                time.sleep(60)

    def _cycle(self):
        """One cycle of the trading bot."""
        ts = datetime.now(pytz.UTC).strftime('%H:%M:%S UTC')
        log.info(f"══════════ 🔄 NEW HUNT CYCLE — {ts} ══════════")
        
        # ── STEP 1: Monitor existing positions with trailing stop ──
        open_positions = [p for p in self.positions if p.status == "open"]
        if open_positions:
            log.info(f"👁️  Watching {len(open_positions)} captured prey...")
            for pos in open_positions:
                self._monitor(pos)
        
        # ── STEP 2: Enforce max 1 position ──
        active_count = len([p for p in self.positions if p.status == "open"])
        if active_count >= MAX_POSITIONS:
            log.info(f"🎒 Max {MAX_POSITIONS} position(s) active — no new hunts.")
            return
        
        # ── STEP 3: Multi-timeframe signal analysis ──
        log.info("👃 Sniffing the market across timeframes...")
        sig, score, cond, near_fib, spot = self.signals.evaluate()
        
        score_bar = "█" * int(score / 5) + "░" * (20 - int(score / 5))
        direction = "🟢 BULLISH" if sig == "BUY" else "🔴 BEARISH" if sig == "SELL" else "⚪ NEUTRAL"
        log.info(f"📡 Final Verdict: {direction} | Score: [{score_bar}] {score:.1f}/100 | BTC: ${spot:,.2f}")
        
        # Check threshold
        if sig == "NEUTRAL":
            log.info("😴 No consensus across timeframes — standing down.")
            return
        if score < OPTIONS_MIN_SCORE and sig == "BUY":
            log.info(f"😴 Score {score:.1f} below threshold {OPTIONS_MIN_SCORE} for BUY — standing down.")
            return
        if (100 - score) < OPTIONS_MIN_SCORE and sig == "SELL":
            log.info(f"😴 Score {score:.1f} above threshold for SELL — standing down.")
            return

        log.info(f"🔥 SIGNAL LOCKED: {sig} — Score {score:.1f} passes. Moving in...")

        # ── STEP 4: Fetch options chain ──
        log.info(f"🔭 Scanning the options jungle for {BASE_UNDERLYING} contracts...")
        chain = self.api.get_options_chain(BASE_UNDERLYING)
        if not chain: 
            log.warning("🏜️  The jungle is EMPTY — no contracts found!")
            return

        # Filter by type needed
        option_type_needed = "call" if sig == "BUY" else "put"
        matching = [c for c in chain if c["type"] == option_type_needed]
        tradeable = [c for c in matching if c["tradeable"]]
        log.info(f"🗺️  Terrain: {len(chain)} total | {len(matching)} {option_type_needed.upper()}s | {len(tradeable)} tradeable")

        # ── STEP 5: Pick the target strike using GREEKS ──
        target = spot * 1.01 if sig == "BUY" else spot * 0.99
        log.info(f"🎯 Locking crosshairs on {option_type_needed.upper()} near strike ${target:,.0f}...")
        
        # Score each contract using Greeks
        # Ideal prey: |Delta| 0.25-0.50 (ATM/slight OTM), high Gamma, low Theta decay  
        scored_options = []
        for c in matching:
            if not c["tradeable"]: continue
            
            # GREEK-BASED SCORING (hunt for the best prey)
            g_score = 0
            d = abs(c.get("delta", 0))
            gamma = abs(c.get("gamma", 0))
            theta = c.get("theta", 0)  # Theta is negative (decay)
            vega = abs(c.get("vega", 0))
            iv = c.get("iv", 0)
            
            # Delta scoring: Sweet spot is 0.25-0.50 (good directional exposure)
            if 0.30 <= d <= 0.50: g_score += 20     # 🎯 Perfect range
            elif 0.20 <= d <= 0.60: g_score += 10   # ✅ Acceptable
            elif d > 0.70: g_score += 5              # Deep ITM, expensive
            else: g_score -= 5                        # Too far OTM
            
            # Gamma scoring: Higher gamma = more responsive to price moves
            if gamma > 0.005: g_score += 10
            elif gamma > 0.001: g_score += 5
            
            # Theta scoring: Less negative theta = less daily decay eating profits
            if theta > -50: g_score += 10    # Low decay
            elif theta > -100: g_score += 5  # Moderate decay
            else: g_score -= 5               # Heavy decay
            
            # Vega scoring: High vega profits from volatility expansion
            if vega > 10: g_score += 5
            
            # Strike proximity scoring
            strike_diff = abs(c["strike"] - target)
            proximity_score = max(0, 20 - (strike_diff / spot) * 100)
            g_score += proximity_score
            
            scored_options.append({**c, "greek_score": g_score})
        
        # Sort by greek_score (highest first)
        scored_options.sort(key=lambda x: x["greek_score"], reverse=True)
        
        # Log top 3 candidates
        if scored_options:
            log.info(f"🏹 TOP PREY CANDIDATES (ranked by Greeks):")
            for i, c in enumerate(scored_options[:3]):
                d = c.get('delta', 0)
                gamma = c.get('gamma', 0)
                theta = c.get('theta', 0)
                vega = c.get('vega', 0)
                iv = c.get('iv', 0)
                # Show delta strength bar
                d_bar = "█" * min(10, int(abs(d) * 20)) + "░" * (10 - min(10, int(abs(d) * 20)))
                rank = "👑" if i == 0 else "  " + str(i+1) + "."
                log.info(f"    {rank} {c['symbol']} | Score: {c['greek_score']:.0f}")
                log.info(f"       ⚔️  STRENGTHS & WEAKNESSES:")
                log.info(f"       └─ Δ Delta (Speed):     [{d_bar}] {d:+.4f} {'⚡ Fast' if abs(d) > 0.3 else '🐌 Slow'}")
                log.info(f"       └─ Γ Gamma (Reflexes):  {gamma:.6f} {'🎯 Sharp' if gamma > 0.003 else '😴 Dull'}")
                log.info(f"       └─ Θ Theta (Decay):     {theta:.4f}/day {'🟢 Low bleed' if theta > -50 else '🔴 Heavy bleed'}")
                log.info(f"       └─ V Vega (Vol. Sens.): {vega:.4f} {'💪 Strong' if vega > 10 else '😐 Weak'}")
                log.info(f"       └─ IV (Fear Level):     {iv:.1f}% {'🔥 High fear' if iv > 60 else '😎 Calm'}")
                log.info(f"       └─ 💰 Bid/Ask: ${c['bid']:.4f}/${c['ask']:.4f} | Spread: {c['spread_pct']*100:.1f}%")
        
        best = scored_options[0] if scored_options else None
        
        # Fallback: any option with a mark price if no scored options
        if not best:
            log.warning("⚠️  No tradeable options with Greeks — widening search...")
            min_diff = 999999
            for c in matching:
                if c["mark_price"] > 0:
                    diff = abs(c["strike"] - target)
                    if diff < min_diff:
                        min_diff = diff; best = c

        # ── STEP 6: Execute ──
        if best:
            log.info(f"══════════════════════════════════════════════")
            log.info(f"🎯 PREY SELECTED — {best['symbol']}")
            log.info(f"    └─ Strike:    ${best['strike']:,.0f}")
            log.info(f"    └─ Mark:      ${best['mark_price']:.4f}")
            log.info(f"    └─ Δ={best.get('delta',0):+.4f} Γ={best.get('gamma',0):.6f} Θ={best.get('theta',0):.2f} V={best.get('vega',0):.2f}")
            log.info(f"    └─ IV:        {best.get('iv',0):.1f}%")
            log.info(f"══════════════════════════════════════════════")
            log.info(f"🏹 ATTACKING — Setting leverage and placing order...")
            self._open(best, sig)
        else:
            log.warning(f"💀 HUNT FAILED — No {option_type_needed.upper()} options to attack!")

    def _open(self, bc, sig):
        """Open a new position with leverage and trailing stop."""
        # ── Set Leverage ──
        log.info(f"    ⚙️  Setting leverage to {LEVERAGE}x...")
        self.api.set_leverage(bc["product_id"], LEVERAGE)
        
        # ── Calculate entry price and quantity ──
        ep = bc["ask"] if bc["ask"] > 0 else bc["mark_price"]
        budget = self.capital * OPTIONS_RISK_PCT
        qty = max(1, int(budget / max(ep, 0.01)))
        
        log.info(f"    💰 Budget: ${budget:.2f} | Price: ${ep:.4f} | Qty: {qty} | Leverage: {LEVERAGE}x")
        log.info(f"    📤 Sending {'PAPER' if PAPER_TRADE else 'LIVE'} order to Delta Exchange...")
        
        order = self.api.place_order(bc["product_id"], "buy", qty, bc["symbol"])
        
        if order.get("success"):
            # Trailing stop: stop starts at entry * (1 - trail_pct)
            initial_stop = ep * (1 - TRAILING_STOP_PCT)
            
            pos = OptionsPosition(
                contract=OptionContract(
                    symbol=bc["symbol"], underlying=BASE_UNDERLYING, expiry="", 
                    strike=bc["strike"], option_type=bc["type"], premium=ep, 
                    delta=bc.get("delta", 0), implied_vol=bc.get("iv", 0) / 100 if bc.get("iv", 0) > 1 else bc.get("iv", 0.5), 
                    open_interest=0,
                    bid=bc["bid"], ask=bc["ask"], spread_pct=bc["spread_pct"], 
                    product_id=bc["product_id"]),
                side="buy", quantity=qty, entry_premium=ep, 
                entry_time=datetime.now(pytz.UTC).isoformat(),
                stop_premium=initial_stop,
                target_premium=0,  # No fixed target — ride the wave
                order_id=str(order["result"]["id"]), 
                peak_premium=ep,
                leverage=LEVERAGE)
            
            self.positions.append(pos)
            log.info(f"══════════════════════════════════════════════")
            log.info(f"🏆 PREY CAPTURED! Leverage: {LEVERAGE}x")
            log.info(f"    └─ {qty}x {bc['symbol']}")
            log.info(f"    └─ Entry Price:   ${ep:.4f}")
            log.info(f"    └─ Trailing Stop: ${initial_stop:.4f} ({TRAILING_STOP_PCT*100:.0f}% from peak)")
            log.info(f"    └─ Target:        ∞ (ride the wave, trailing protects)")
            log.info(f"    └─ Leverage:      {LEVERAGE}x")
            log.info(f"    └─ Order ID:      {order['result']['id']}")
            log.info(f"══════════════════════════════════════════════")
            _save_json(POSITIONS_FILE, [asdict(p) for p in self.positions])
        else:
            log.error(f"💥 ORDER REJECTED! Response: {order}")

    def _monitor(self, pos):
        """Monitor position with dynamic trailing stop loss.
        
        The stop only moves UP, never down:
        - If price reaches new peak → stop moves up to peak * (1 - trail_pct)
        - If price drops to stop → EXIT
        - Peak is tracked per position
        """
        # Get contract symbol (handle both OptionContract objects and dicts)
        if isinstance(pos.contract, dict):
            symbol = pos.contract.get("symbol", "")
            product_id = pos.contract.get("product_id", 0)
        else:
            symbol = pos.contract.symbol
            product_id = pos.contract.product_id
        
        curr = self.api.get_option_premium(symbol)
        if not curr: 
            log.warning(f"    🔇 Can't get price for {symbol} — prey went dark")
            return
        
        p = curr.get("mark_price", pos.entry_premium)
        if p <= 0: return
        
        # ── UPDATE PEAK ──
        if p > pos.peak_premium:
            old_peak = pos.peak_premium
            pos.peak_premium = p
            pos.stop_premium = p * (1 - TRAILING_STOP_PCT)
            if old_peak != p:
                log.info(f"    📈 NEW PEAK! {symbol}: ${p:.4f} → Stop raised to ${pos.stop_premium:.4f}")
        
        # ── CALCULATE P&L ──
        pnl_pct = ((p - pos.entry_premium) / pos.entry_premium) * 100
        leveraged_pnl = pnl_pct * LEVERAGE
        
        emoji = "📈" if pnl_pct > 0 else "📉"
        log.info(f"    {emoji} {symbol}: ${p:.4f} ({pnl_pct:+.1f}% | {leveraged_pnl:+.0f}% w/{LEVERAGE}x) | Peak: ${pos.peak_premium:.4f} | Stop: ${pos.stop_premium:.4f}")
        
        # ── CHECK EXIT CONDITIONS ──
        # 1. Trailing stop hit
        if p <= pos.stop_premium:
            final_pnl = pnl_pct
            if final_pnl > 0:
                log.info(f"    🎉 TRAILING STOP — Locking in {pnl_pct:+.1f}% profit!")
                self._close(pos, p, f"TRAILING_STOP_PROFIT ({pnl_pct:+.1f}%)")
            else:
                log.warning(f"    🩸 TRAILING STOP — Cut loss at {pnl_pct:+.1f}%")
                self._close(pos, p, f"TRAILING_STOP_LOSS ({pnl_pct:+.1f}%)")
            return
        
        # 2. Max hold time exceeded
        try:
            entry_dt = datetime.fromisoformat(pos.entry_time.replace("Z", "+00:00"))
            hours_held = (datetime.now(pytz.UTC) - entry_dt).total_seconds() / 3600
            if hours_held >= MAX_HOLD_HOURS:
                log.warning(f"    ⏰ MAX HOLD TIME ({MAX_HOLD_HOURS}h) exceeded — force closing")
                self._close(pos, p, f"MAX_HOLD_{MAX_HOLD_HOURS}H")
                return
        except:
            pass

    def _close(self, pos, p, reason):
        """Close a position and log the results."""
        if isinstance(pos.contract, dict):
            product_id = pos.contract.get("product_id", 0)
            symbol = pos.contract.get("symbol", "")
        else:
            product_id = pos.contract.product_id
            symbol = pos.contract.symbol
        
        self.api.place_order(product_id, "sell", pos.quantity, symbol)
        
        pnl = (p - pos.entry_premium) * pos.quantity
        pnl_pct = ((p - pos.entry_premium) / pos.entry_premium) * 100
        pos.status = "closed"
        pos.exit_premium = p
        pos.exit_reason = reason
        pos.pnl = pnl
        
        emoji = "💰" if pnl > 0 else "💸"
        log.info(f"══════════════════════════════════════════════")
        log.info(f"{emoji} PREY RELEASED — {reason}")
        log.info(f"    └─ Contract:  {symbol}")
        log.info(f"    └─ Entry:     ${pos.entry_premium:.4f}")
        log.info(f"    └─ Peak:      ${pos.peak_premium:.4f}")
        log.info(f"    └─ Exit:      ${p:.4f}")
        log.info(f"    └─ P&L:       ${pnl:.4f} ({pnl_pct:+.1f}%)")
        log.info(f"    └─ Leveraged: {pnl_pct * LEVERAGE:+.1f}% (at {LEVERAGE}x)")
        log.info(f"══════════════════════════════════════════════")
        
        _save_json(POSITIONS_FILE, [asdict(p) for p in self.positions])
        
        # Also save to trade history
        history = _load_json(TRADE_HISTORY_FILE, [])
        history.append({
            "symbol": symbol, "entry": pos.entry_premium, "exit": p,
            "peak": pos.peak_premium, "pnl": pnl, "pnl_pct": pnl_pct,
            "reason": reason, "leverage": LEVERAGE,
            "entry_time": pos.entry_time, 
            "exit_time": datetime.now(pytz.UTC).isoformat()
        })
        _save_json(TRADE_HISTORY_FILE, history)


# ── BOOT ──
if __name__ == "__main__":
    bot = OptionsTradingBot()
    bot.run()
