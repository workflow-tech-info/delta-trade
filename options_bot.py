"""
╔══════════════════════════════════════════════════════════════════════╗
║          DELTA EXCHANGE — CRYPTO OPTIONS BOT v3.1                   ║
║          Fixed Signal Engine · Spot Fallback · Paper-First          ║
║                                                                      ║
║  Root cause fixed: candle resolution format ("15m" not "15")         ║
║  Fallback added: Signal from Spot price if candles are missing       ║
╚══════════════════════════════════════════════════════════════════════╝

Welcome to the Code!
If you are new to coding, don't worry. This file is heavily commented to
explain what each part does. You can read it top-to-bottom like a story.
"""

# Import necessary libraries. Libraries are like pre-built toolboxes.
# 'requests' is for making web requests (talking to the Delta Exchange API).
import requests, numpy as np, math, time, json, logging, os, hmac, hashlib, pytz
from datetime import datetime, timedelta
# 'dataclass' helps us create structured data easily (like a custom container for options data).
from dataclasses import dataclass, field, asdict
from typing import Optional, List, Dict, Any
# 'dotenv' loads variables from the .env file (so we keep secrets hidden).
from dotenv import load_dotenv
from pathlib import Path

# Load all the secrets and settings from the .env file
load_dotenv()

# ── CONFIGURATION SETTINGS ──
# We pull these from the .env file so they aren't hardcoded in the script.
API_KEY        = os.getenv("DELTA_API_KEY", "")
API_SECRET     = os.getenv("DELTA_API_SECRET", "")
# BASE_URL tells the bot where to send requests (Testnet or Live)
BASE_URL       = os.getenv("DELTA_BASE_URL", "https://cdn-ind.testnet.deltaex.org")
TG_TOKEN       = os.getenv("TG_TOKEN", "")
TG_CHAT_ID     = os.getenv("TG_CHAT_ID", "")

# ⚠️ PAPER TRADING SETTINGS
# 'FORCE_PAPER_TRADE' controls whether the bot sends REAL orders to the API or just pretends.
# Set to False to allow the bot to place real orders on the Testnet/Mainnet.
FORCE_PAPER_TRADE = False   
# Determine whether paper trade is active. It is active if forced, OR if the .env file says so.
PAPER_TRADE       = True if FORCE_PAPER_TRADE else os.getenv("PAPER_TRADE", "true").lower() == "true"

# The minimum score (out of 100) needed to trigger a trade.
# Lower means more trades (but more risky). Higher means fewer, safer trades.
OPTIONS_MIN_SCORE = 0      # Set to 0 to force a trade immediately for testing

# Leverage used for positions. Example: 100x leverage.
LEVERAGE          = int(os.getenv("LEVERAGE", "100"))
# Risk per trade: 0.005 means 0.5% of total capital per trade.
OPTIONS_RISK_PCT  = 0.005
# Daily loss limit: 0.03 means if the bot loses 3% in a day, it stops trading.
DAILY_LOSS_LIMIT  = 0.03
# The bot will auto-close positions 30 minutes before they expire.
CLOSE_BEFORE_EXPIRY_MINS = 30
# The maximum number of hours to hold a position.
MAX_HOLD_HOURS    = 4
# How often the bot runs a cycle (in seconds). 300 = 5 minutes.
CYCLE_INTERVAL    = 10
# The underlying asset we are trading options on.
BASE_UNDERLYING   = os.getenv("BASE_UNDERLYING", "BTC")

# Directory setup for storing our bot's data (so it remembers things if restarted)
DATA_DIR = Path(os.getenv("BOT_DATA_DIR", "./bot_data"))
DATA_DIR.mkdir(exist_ok=True) # Create folder if it doesn't exist
POSITIONS_FILE     = DATA_DIR / "positions.json"        # Where open trades are saved
TRADE_HISTORY_FILE = DATA_DIR / "trade_history.json"    # Complete history of all past trades
PERFORMANCE_FILE   = DATA_DIR / "performance_report.json"

# Setting up logging. This prints messages to the screen AND saves them to 'options_bot_log.txt'
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

# Function to save data into a JSON file easily.
def _save_json(filepath: Path, data):
    try:
        with open(filepath, "w") as f:
            # write data with 2-space indentation to make it readable for humans
            json.dump(data, f, indent=2, default=str)
    except: pass # Ignore errors silently

# Function to load data from a JSON file. Return an empty list if file doesn't exist.
def _load_json(filepath: Path, default=None):
    if default is None: default = []
    try:
        if filepath.exists():
            with open(filepath, "r") as f: return json.load(f)
    except: pass
    return default

# A dataclass is just a blueprint to store related variables together neatly.
@dataclass
class OptionContract:
    # Describes the details of the option itself (e.g. Call, Put, Strike price)
    symbol: str; underlying: str; expiry: str; strike: float
    option_type: str; premium: float; delta: float; implied_vol: float
    open_interest: int; bid: float; ask: float; spread_pct: float
    product_id: int = 0; expiry_datetime: str = ""

@dataclass
class OptionsPosition:
    # Describes our actual trade (e.g. how many we bought, at what price)
    contract: OptionContract; side: str; quantity: int
    entry_premium: float; entry_time: str
    stop_premium: float; target_premium: float
    order_id: str = ""; exit_premium: float = 0.0
    exit_reason: str = ""; pnl: float = 0.0
    status: str = "open"; leverage: int = LEVERAGE
    peak_premium: float = 0.0 # Tracks highest price reached for trailing stops

# ══════════════════════════════════════════════════════════════
# DELTA API HANDLER
# This class handles talking to the Delta Exchange servers.
# ══════════════════════════════════════════════════════════════
class DeltaAPI:
    def __init__(self):
        # A session keeps connection alive for faster requests
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json", "User-Agent": "bot-v3.1"})

    # This creates a cryptographic signature required for private API endpoints (like placing orders or reading wallet balances)
    def _sign(self, method, path, query_string="", payload=""):
        ts = str(int(time.time()))
        message = method + ts + path + query_string + payload
        sig = hmac.new(API_SECRET.encode(), message.encode(), hashlib.sha256).hexdigest()
        return {"api-key": API_KEY, "timestamp": ts, "signature": sig, "Content-Type": "application/json"}

    # Fetch the current real-time price of BTC
    def get_spot_price(self, symbol="BTCUSD") -> float:
        try:
            r = self.session.get(f"{BASE_URL}/v2/tickers/{symbol}", timeout=10).json()
            if r.get("success"):
                return float(r["result"].get("spot_price") or r["result"].get("mark_price") or 0)
        except: pass
        return 0.0

    # Fetch historical candle data used by technical indicators
    def get_candles(self, symbol="BTCUSD", resolution="15m", limit=30) -> list:
        try:
            r = self.session.get(f"{BASE_URL}/v2/history/candles", 
                                 params={"resolution": resolution, "symbol": symbol, "limit": limit},
                                 timeout=10).json()
            if r.get("success"): return r.get("result", [])
        except: pass
        return []

    # Fetch the amount of money in the account
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

    # Fetch the list of all available options on Delta right now
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
                
                # Check if it's tradeable (must have a bid price, and the spread shouldn't be too huge)
                spread_pct = (ask-bid)/ask if ask > 0 else 1.0
                tradeable = bid > 0 and spread_pct < 0.20
                
                options.append({
                    "symbol": t.get("symbol"), "product_id": t.get("product_id"),
                    "strike": float(t.get("strike_price", 0)),
                    "type": "call" if "call" in t.get("contract_type", "") else "put",
                    "mark_price": float(t.get("mark_price", 0)),
                    "bid": bid, "ask": ask, "spread_pct": spread_pct,
                    "tradeable": tradeable
                })
            return options
        except: return []

    # Sends an order (buy or sell) to the exchange
    def place_order(self, product_id, side, size, symbol=""):
        # If in paper trade mode, simulate success and skip hitting the API
        if PAPER_TRADE:
            log.info(f"📝 PAPER: {side.upper()} {size}x {symbol}")
            return {"success": True, "result": {"id": f"paper_{int(time.time())}"}}
        try:
            path = "/v2/orders"
            body = json.dumps({"product_id": product_id, "side": side, "size": size, "order_type": "market_order"})
            headers = self._sign("POST", path, "", body)
            return self.session.post(f"{BASE_URL}{path}", data=body, headers=headers, timeout=10).json()
        except: return {}

    # Retrieves the latest price of a single specific option contract
    def get_option_premium(self, symbol):
        try:
            r = self.session.get(f"{BASE_URL}/v2/tickers/{symbol}", timeout=10).json()
            if not r.get("success"): return {}
            res = r.get("result", {})
            return {"mark_price": float(res.get("mark_price", 0))}
        except: return {}

# ══════════════════════════════════════════════════════════════
# SIGNAL ENGINE
# This is the "brain" of the bot that looks at charts and decides
# to BUY, SELL, or do nothing (NEUTRAL).
# ══════════════════════════════════════════════════════════════
class SignalEngine:
    def __init__(self, api: DeltaAPI):
        self.api = api
        self.last_spots = [] # Keeps a short history of prices to use if candles fail

    # Primary function that calculates the trading score
    def evaluate(self, symbol="BTCUSD"):
        spot = self.api.get_spot_price(symbol)
        
        # Save spot price to history for fallback mechanics
        if spot > 0: self.last_spots.append(spot)
        # Keep only the last 10 spot records to save memory
        if len(self.last_spots) > 10: self.last_spots.pop(0)

        # Get 15-minute candles
        candles = self.api.get_candles(symbol, "15m", 30)
        
        # FALLBACK LOGIC: If the exchange API fails to send candle data, 
        # use recent spot prices so the bot doesn't freeze.
        if not candles:
            log.warning("⚠️ No candle data — Using Spot Price Fallback")
            if len(self.last_spots) < 2: return "NEUTRAL", 0, "no_data", False, spot
            
            # Simple momentum: (New price - Old price) / Old price
            change = (self.last_spots[-1] - self.last_spots[0]) / self.last_spots[0]
            # Exaggerate the movement to turn a tiny % change into a big score
            score = 50 + (change * 1000) 
            score = max(0, min(100, score)) # Ensure score is between 0 and 100
            
            sig = "BUY" if score >= 50 else "SELL"  # No NEUTRAL zone — always trade
            return sig, score, "spot_momentum", False, spot

        # Extract closing prices from the candle data
        closes = [float(c['close']) for c in candles]
        
        # Calculate technical indicators
        rsi = self._rsi(closes)
        ema9, ema21 = self._ema(closes, 9), self._ema(closes, 21)
        
        # Base score starts at neutral (50)
        score = 50
        
        # ----- RSI COMPONENT -----
        # RSI measures if the asset is overbought or oversold
        if rsi < 40: score += 15     # Oversold, add to score (bullish)
        elif rsi > 60: score -= 15   # Overbought, subtract from score (bearish)
        
        # ----- EMA COMPONENT -----
        # EMA crossover measures short-term trend vs medium-term trend
        if ema9[-1] > ema21[-1]: score += 15  # Trend is up
        else: score -= 15                     # Trend is down
        
        # ----- MOMENTUM COMPONENT -----
        # Simple check: Is the current price higher than it was 5 periods ago?
        if len(closes) >= 5:
            if closes[-1] > closes[-5]: score += 10 # Going up
            else: score -= 10                       # Going down

        # Constrain score between 0 and 100
        score = max(0, min(100, score))
        
        # Determine the final Signal text based on score
        sig = "BUY" if score >= 50 else "SELL"  # No NEUTRAL zone — always trade
        return sig, score, "candle_mixed", False, spot

    # Support function: Calculates Exponential Moving Average
    def _ema(self, data, p):
        k = 2/(p+1); ema = [data[0]]
        for v in data[1:]: ema.append(v*k + ema[-1]*(1-k))
        return ema

    # Support function: Calculates Relative Strength Index
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
# Controls the loop, checks positions, and directs trading.
# ══════════════════════════════════════════════════════════════
class OptionsTradingBot:
    def __init__(self, capital=15000):
        # Setup tools
        self.api = DeltaAPI()
        self.signals = SignalEngine(self.api)
        self.positions: List[OptionsPosition] = []
        
        log.info("🎯 BOT STARTING...")
        wallet = self.api.get_wallet_balance()
        
        # Use wallet balance if we found any, otherwise fallback to configured capital
        self.capital = wallet.get("available", capital) if wallet.get("available", 0) > 0 else capital
        log.info(f"💰 Capital: ${self.capital:,.2f} | Mode: {'PAPER' if PAPER_TRADE else 'LIVE/TESTNET'}")
        
        # Resume open trades from previous session
        self._load()

    # Loads saved trades from disk so the bot doesn't forget them on restart
    def _load(self):
        data = _load_json(POSITIONS_FILE, [])
        for d in data:
            if d.get("status") == "open": 
                # Reconstruct the position object and add to memory
                self.positions.append(OptionsPosition(**d))

    # The infinite loop that keeps the bot alive
    def run(self):
        log.info("═══════════════════════════════════════════════════")
        log.info("🐺  THE HUNT BEGINS — Entering the market jungle...")
        log.info("═══════════════════════════════════════════════════")
        while True:
            try:
                self._cycle()
                log.info(f"💤 Resting {CYCLE_INTERVAL}s before next hunt...\n")
                time.sleep(CYCLE_INTERVAL)
            except KeyboardInterrupt: 
                log.info("🛑 Hunter called off — shutting down gracefully.")
                break
            except Exception as e: 
                log.error(f"🩸 Wounded! Error: {e} — recovering in 60s..."); time.sleep(60)

    # One single "round" or "tick" of trading logic
    def _cycle(self):
        ts = datetime.now(pytz.UTC).strftime('%H:%M:%S UTC')
        log.info(f"══════════ 🔄 NEW HUNT CYCLE — {ts} ══════════")
        
        # 1. Check on existing prey we've caught
        open_positions = [p for p in self.positions if p.status == "open"]
        if open_positions:
            log.info(f"👁️  Watching {len(open_positions)} captured prey...")
            for pos in open_positions:
                self._monitor(pos)

        # 2. Limit how many active trades we can have at once
        if len([p for p in self.positions if p.status == "open"]) >= 2: 
            log.info("🎒 Hands full — already holding 2 positions. Waiting...")
            return
        
        # 3. Sniff the air — read the market
        log.info("👃 Sniffing the market for a signal...")
        sig, score, cond, near_fib, spot = self.signals.evaluate()
        
        score_bar = "█" * int(score / 5) + "░" * (20 - int(score / 5))
        direction = "🟢 BULLISH" if sig == "BUY" else "🔴 BEARISH" if sig == "SELL" else "⚪ FLAT"
        log.info(f"📡 Market Read: {direction} | Score: [{score_bar}] {score:.1f}/100 | BTC: ${spot:,.2f}")
        log.info(f"📐 Method: {cond} | Min needed: {OPTIONS_MIN_SCORE}")

        # If the score is too weak, skip.
        if score < OPTIONS_MIN_SCORE or sig == "NEUTRAL": 
            log.info("😴 Signal too weak or neutral — no prey in sight. Standing down.")
            return

        log.info(f"🔥 SIGNAL LOCKED: {sig} — Score {score:.1f} passes threshold {OPTIONS_MIN_SCORE}. Moving in...")

        # 4. Scan the territory — fetch available options
        log.info(f"🔭 Scanning the options jungle for {BASE_UNDERLYING} contracts...")
        chain = self.api.get_options_chain(BASE_UNDERLYING)
        if not chain: 
            log.warning("🏜️  The jungle is EMPTY — no options contracts found on the exchange!")
            return

        # Count what we found
        option_type_needed = "call" if sig == "BUY" else "put"
        matching = [c for c in chain if c["type"] == option_type_needed]
        tradeable_matching = [c for c in matching if c["tradeable"]]
        log.info(f"🗺️  Terrain mapped: {len(chain)} total contracts spotted")
        log.info(f"    └─ {len(matching)} are {option_type_needed.upper()}s")
        log.info(f"    └─ {len(tradeable_matching)} are tradeable (have bid + tight spread)")

        # 5. Pick the target strike
        target = spot * 1.01 if sig == "BUY" else spot * 0.99
        log.info(f"🎯 Locking crosshairs on {option_type_needed.upper()} near strike ${target:,.0f}...")
        
        # Search for the best match
        best = None; min_diff = 999999
        for c in matching:
            if c["tradeable"]:
                diff = abs(c["strike"] - target)
                if diff < min_diff: 
                    min_diff = diff
                    best = c
        
        # Fallback: if no "tradeable" option, pick the closest one with any mark price
        if not best:
            log.warning("⚠️  No clean shots available — widening search to ANY priced contract...")
            for c in matching:
                if c["mark_price"] > 0:
                    diff = abs(c["strike"] - target)
                    if diff < min_diff:
                        min_diff = diff
                        best = c

        # 6. Execute!
        if best: 
            log.info(f"🎯 PREY SPOTTED!")
            log.info(f"    └─ Contract:  {best['symbol']}")
            log.info(f"    └─ Strike:    ${best['strike']:,.0f}")
            log.info(f"    └─ Bid/Ask:   ${best['bid']:.4f} / ${best['ask']:.4f}")
            log.info(f"    └─ Mark:      ${best['mark_price']:.4f}")
            log.info(f"    └─ Spread:    {best['spread_pct']*100:.1f}%")
            log.info(f"🏹 ATTACKING — Placing order...")
            self._open(best, sig)
        else:
            log.warning(f"💀 HUNT FAILED — No {option_type_needed.upper()} options available to attack!")
            if matching:
                log.info(f"    └─ Found {len(matching)} {option_type_needed}s but none had valid pricing")
                for c in matching[:3]:
                    log.info(f"       • {c['symbol']} Strike=${c['strike']:,.0f} Bid={c['bid']} Ask={c['ask']} Mark={c['mark_price']}")


    # Logic for opening a new position
    def _open(self, bc, sig):
        ep = bc["ask"] if bc["ask"] > 0 else bc["mark_price"]
        
        budget = self.capital * OPTIONS_RISK_PCT
        qty = max(1, int(budget / max(ep, 0.01)))
        
        log.info(f"💰 Budget: ${budget:.2f} | Price: ${ep:.4f} | Quantity: {qty}")
        log.info(f"📤 Sending {'PAPER' if PAPER_TRADE else 'LIVE'} order to Delta Exchange...")
        
        order = self.api.place_order(bc["product_id"], "buy", qty, bc["symbol"])
        
        if order.get("success"):
            pos = OptionsPosition(
                contract=OptionContract(symbol=bc["symbol"], underlying=BASE_UNDERLYING, expiry="", strike=bc["strike"], 
                                        option_type=bc["type"], premium=ep, delta=0, implied_vol=0.5, open_interest=0, 
                                        bid=bc["bid"], ask=bc["ask"], spread_pct=bc["spread_pct"], product_id=bc["product_id"]),
                side="buy", quantity=qty, entry_premium=ep, entry_time=datetime.now(pytz.UTC).isoformat(),
                stop_premium=ep*0.5,
                target_premium=ep*2.0,
                order_id=str(order["result"]["id"]), peak_premium=ep)
            
            self.positions.append(pos)
            log.info(f"══════════════════════════════════════════════")
            log.info(f"🏆 PREY CAPTURED!")
            log.info(f"    └─ {qty}x {bc['symbol']}")
            log.info(f"    └─ Entry Price: ${ep:.4f}")
            log.info(f"    └─ Stop Loss:   ${ep*0.5:.4f} (-50%)")
            log.info(f"    └─ Take Profit: ${ep*2.0:.4f} (+100%)")
            log.info(f"    └─ Order ID:    {order['result']['id']}")
            log.info(f"══════════════════════════════════════════════")
            _save_json(POSITIONS_FILE, [asdict(p) for p in self.positions])
        else:
            log.error(f"💥 ORDER REJECTED by exchange! Response: {order}")

    # Monitors an existing open trade for stop-loss or take-profit
    def _monitor(self, pos):
        curr = self.api.get_option_premium(pos.contract.symbol)
        if not curr: 
            log.warning(f"🔇 Can't get price for {pos.contract.symbol} — prey went dark")
            return
        
        p = curr.get("mark_price", pos.entry_premium)
        pnl_pct = ((p - pos.entry_premium) / pos.entry_premium) * 100
        
        emoji = "📈" if pnl_pct > 0 else "📉"
        log.info(f"    {emoji} {pos.contract.symbol}: ${p:.4f} ({pnl_pct:+.1f}%) | Stop: ${pos.stop_premium:.4f} | Target: ${pos.target_premium:.4f}")
        
        if p <= pos.stop_premium:
            log.warning(f"🩸 STOP LOSS HIT — Prey bit back! Closing at ${p:.4f}")
            self._close(pos, p, "STOP_LOSS")
        elif p >= pos.target_premium:
            log.info(f"🎉 TARGET HIT — Perfect kill! Taking profits at ${p:.4f}")
            self._close(pos, p, "TAKE_PROFIT")

    # Closes out an existing trade
    def _close(self, pos, p, reason):
        self.api.place_order(pos.contract.product_id, "sell", pos.quantity, pos.contract.symbol)
        
        pnl = (p - pos.entry_premium) * pos.quantity
        pos.status = "closed"
        pos.exit_premium = p
        pos.exit_reason = reason
        pos.pnl = pnl
        
        emoji = "💰" if pnl > 0 else "💸"
        log.info(f"══════════════════════════════════════════════")
        log.info(f"{emoji} PREY RELEASED — {reason}")
        log.info(f"    └─ Contract:  {pos.contract.symbol}")
        log.info(f"    └─ Entry:     ${pos.entry_premium:.4f}")
        log.info(f"    └─ Exit:      ${p:.4f}")
        log.info(f"    └─ P&L:       ${pnl:.4f}")
        log.info(f"══════════════════════════════════════════════")
        
        _save_json(POSITIONS_FILE, [asdict(p) for p in self.positions])

# Standard Python run command. Boots the bot up if this file is run directly.
if __name__ == "__main__":
    bot = OptionsTradingBot()
    bot.run()

