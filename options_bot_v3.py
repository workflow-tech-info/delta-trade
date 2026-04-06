"""
╔══════════════════════════════════════════════════════════════════════╗
║          DELTA EXCHANGE — CRYPTO OPTIONS BOT v3.0                   ║
║          Fixed Signal Engine · Multi-Indicator · Paper-First        ║
║                                                                      ║
║  Root cause fixed: candle resolution format ("15m" not "15")         ║
║  6-indicator scoring · Multi-timeframe · Trailing stops              ║
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

# ⚠️ FORCED PAPER MODE — change this constant ONLY after verifying profitability
FORCE_PAPER_TRADE = False
PAPER_TRADE       = True if FORCE_PAPER_TRADE else os.getenv("PAPER_TRADE", "true").lower() == "true"

LEVERAGE          = int(os.getenv("LEVERAGE", "100"))
OPTIONS_RISK_PCT  = 0.005
OPTIONS_MIN_SCORE = 10          # Lowered to 10 for immediate testing
DAILY_LOSS_LIMIT  = 0.03
CLOSE_BEFORE_EXPIRY_MINS = 30
MAX_HOLD_HOURS    = 4
CYCLE_INTERVAL    = 300         # 5 minutes
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
# PERSISTENCE
# ══════════════════════════════════════════════════════════════
def _save_json(filepath: Path, data):
    try:
        with open(filepath, "w") as f:
            json.dump(data, f, indent=2, default=str)
    except Exception as e:
        log.error(f"Save error {filepath}: {e}")

def _load_json(filepath: Path, default=None):
    if default is None:
        default = []
    try:
        if filepath.exists():
            with open(filepath, "r") as f:
                return json.load(f)
    except Exception as e:
        log.error(f"Load error {filepath}: {e}")
    return default


# ══════════════════════════════════════════════════════════════
# DATA CLASSES
# ══════════════════════════════════════════════════════════════
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
    peak_premium: float = 0.0   # for trailing stop


# ══════════════════════════════════════════════════════════════
# TELEGRAM
# ══════════════════════════════════════════════════════════════
class TelegramNotifier:
    def __init__(self):
        self.enabled = bool(TG_TOKEN and TG_CHAT_ID)
    def send(self, msg: str):
        if not self.enabled: return
        try:
            requests.post(
                f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                json={"chat_id": TG_CHAT_ID, "text": msg, "parse_mode": "HTML"},
                timeout=10)
        except: pass


# ══════════════════════════════════════════════════════════════
# DELTA API — Fixed candle resolution format
# ══════════════════════════════════════════════════════════════
class DeltaAPI:

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "Content-Type": "application/json",
            "User-Agent": "options-bot-v3",
        })

    def _sign(self, method, path, query_string="", payload=""):
        ts = str(int(time.time()))
        message = method + ts + path + query_string + payload
        sig = hmac.new(API_SECRET.encode(), message.encode(), hashlib.sha256).hexdigest()
        return {"api-key": API_KEY, "timestamp": ts, "signature": sig,
                "Content-Type": "application/json", "User-Agent": "options-bot-v3"}

    def get_spot_price(self, symbol="BTCUSD") -> float:
        try:
            r = self.session.get(f"{BASE_URL}/v2/tickers/{symbol}", timeout=10).json()
            if r.get("success"):
                return float(r["result"].get("spot_price") or r["result"].get("mark_price") or 0)
        except Exception as e:
            log.error(f"Spot error: {e}")
        return 0.0

    def get_candles(self, symbol="BTCUSD", resolution="15m", limit=50) -> list:
        """
        CRITICAL FIX: pass resolution as-is ("1m","5m","15m","1h","4h","1d")
        The old code mapped "15m" -> "15" which broke everything.
        """
        for attempt in range(3):
            try:
                end_ts = int(time.time())
                # Calculate start from resolution
                mins = {"1m": 1, "5m": 5, "15m": 15, "30m": 30,
                        "1h": 60, "2h": 120, "4h": 240, "1d": 1440}
                interval_mins = mins.get(resolution, 15)
                start_ts = end_ts - (limit * interval_mins * 60)

                resp = self.session.get(
                    f"{BASE_URL}/v2/history/candles",
                    params={"resolution": resolution, "symbol": symbol,
                            "start": start_ts, "end": end_ts},
                    timeout=15)
                data = resp.json()
                if data.get("success"):
                    candles = data.get("result", [])
                    if candles:
                        log.info(f"📊 Fetched {len(candles)} candles ({resolution} {symbol})")
                        return candles
                    else:
                        log.warning(f"Candle API returned empty (attempt {attempt+1}/3)")
                else:
                    log.warning(f"Candle API error: {data.get('error', 'unknown')}")
            except Exception as e:
                log.error(f"Candle fetch error (attempt {attempt+1}): {e}")
            time.sleep(2)
        return []

    def get_wallet_balance(self) -> dict:
        if not API_KEY or not API_SECRET:
            return {"total": 0, "available": 0, "asset": "USD"}
        try:
            path = "/v2/wallet/balances"
            headers = self._sign("GET", path)
            resp = self.session.get(f"{BASE_URL}{path}", headers=headers, timeout=10).json()
            if not resp.get("success"):
                return {"total": 0, "available": 0, "asset": "USD"}
            best = {"total": 0, "available": 0, "asset": "USD"}
            for b in resp.get("result", []):
                bal = float(b.get("balance", 0) or 0)
                avail = float(b.get("available_balance", 0) or 0)
                sym = b.get("asset_symbol", "")
                if bal > best["total"]:
                    best = {"total": bal, "available": avail, "asset": sym}
                if bal > 0:
                    log.info(f"   💰 {sym}: {bal:.4f} (avail: {avail:.4f})")
            return best
        except Exception as e:
            log.error(f"Wallet error: {e}")
            return {"total": 0, "available": 0, "asset": "USD"}

    def get_options_chain(self, underlying="BTC") -> list:
        try:
            resp = self.session.get(
                f"{BASE_URL}/v2/tickers",
                params={"contract_types": "call_options,put_options",
                        "underlying_asset_symbols": underlying},
                timeout=15).json()
            if not resp.get("success"):
                return []
            options = []
            for t in resp.get("result", []):
                ct = t.get("contract_type", "")
                if ct not in ("call_options", "put_options"):
                    continue
                greeks = t.get("greeks") or {}
                quotes = t.get("quotes") or {}
                mark_vol = t.get("mark_vol")
                iv = 0.50
                if mark_vol:
                    try:
                        iv = float(mark_vol) / 100.0 if float(mark_vol) > 5 else float(mark_vol)
                    except: pass
                bid = float(quotes.get("best_bid", 0) or 0)
                ask = float(quotes.get("best_ask", 0) or 0)
                spread = (ask - bid) / ask if ask > 0 else 1.0
                options.append({
                    "symbol": t.get("symbol", ""), "product_id": t.get("product_id", 0),
                    "strike": float(t.get("strike_price", 0) or 0),
                    "type": "call" if "call" in ct else "put",
                    "mark_price": float(t.get("mark_price", 0) or 0),
                    "spot_price": float(t.get("spot_price", 0) or 0),
                    "iv": iv,
                    "delta": float(greeks.get("delta", 0) or 0),
                    "bid": bid, "ask": ask, "spread_pct": spread,
                    "oi": int(float(t.get("oi", 0) or 0)),
                    "tradeable": spread < 0.10 and bid > 0,
                })
            log.info(f"Options chain: {len(options)} contracts for {underlying}")
            return options
        except Exception as e:
            log.error(f"Chain error: {e}")
            return []

    def find_best_contract(self, chain, target_strike, option_type):
        best, best_diff = None, float("inf")
        for c in chain:
            if c["type"] != option_type or not c.get("tradeable"):
                continue
            diff = abs(c["strike"] - target_strike)
            if diff < best_diff:
                best_diff, best = diff, c
        return best

    def get_option_premium(self, symbol):
        try:
            r = self.session.get(f"{BASE_URL}/v2/tickers/{symbol}", timeout=10).json()
            if not r.get("success"):
                return {}
            res = r["result"]
            quotes = res.get("quotes") or {}
            mark = float(res.get("mark_price", 0) or 0)
            bid = float(quotes.get("best_bid", 0) or 0)
            ask = float(quotes.get("best_ask", 0) or 0)
            return {"mark_price": mark, "bid": bid, "ask": ask,
                    "spread_pct": (ask-bid)/ask if ask > 0 else 1.0}
        except:
            return {}

    def place_order(self, product_id, side, size, symbol=""):
        if PAPER_TRADE:
            oid = f"paper_{int(time.time())}_{side}"
            log.info(f"📝 PAPER ORDER: {side.upper()} {size}x {symbol or product_id}")
            return {"success": True, "result": {"id": oid}}
        if not API_KEY or not API_SECRET:
            return {}
        try:
            path = "/v2/orders"
            body = json.dumps({"product_id": product_id, "side": side,
                               "size": size, "order_type": "market_order"})
            headers = self._sign("POST", path, "", body)
            return self.session.post(f"{BASE_URL}{path}", data=body,
                                     headers=headers, timeout=15).json()
        except Exception as e:
            log.error(f"Order error: {e}")
            return {}


# ══════════════════════════════════════════════════════════════
# SIGNAL ENGINE v3 — 6 Indicators, Multi-Timeframe
# ══════════════════════════════════════════════════════════════
class SignalEngine:

    def __init__(self, api: DeltaAPI):
        self.api = api

    @staticmethod
    def _ema(data, period):
        if len(data) < period: return data[:]
        k = 2 / (period + 1)
        ema = [sum(data[:period]) / period]
        for v in data[period:]:
            ema.append(v * k + ema[-1] * (1 - k))
        return ema

    @staticmethod
    def _rsi(closes, period=14):
        if len(closes) < period + 1: return 50.0
        deltas = [closes[i] - closes[i-1] for i in range(1, len(closes))]
        gains = [max(d, 0) for d in deltas]
        losses = [max(-d, 0) for d in deltas]
        avg_g = sum(gains[:period]) / period
        avg_l = sum(losses[:period]) / period
        for i in range(period, len(gains)):
            avg_g = (avg_g * (period - 1) + gains[i]) / period
            avg_l = (avg_l * (period - 1) + losses[i]) / period
        if avg_l == 0: return 100.0
        return 100 - (100 / (1 + avg_g / avg_l))

    @staticmethod
    def _macd(closes, fast=12, slow=26, signal=9):
        if len(closes) < slow + signal: return 0, 0, 0
        k_fast, k_slow = 2/(fast+1), 2/(slow+1)
        ema_f = [sum(closes[:fast])/fast]
        for v in closes[fast:]: ema_f.append(v*k_fast + ema_f[-1]*(1-k_fast))
        ema_s = [sum(closes[:slow])/slow]
        for v in closes[slow:]: ema_s.append(v*k_slow + ema_s[-1]*(1-k_slow))
        min_len = min(len(ema_f), len(ema_s))
        macd_line = [ema_f[-(min_len-i)] - ema_s[-(min_len-i)] for i in range(min_len)]
        if len(macd_line) < signal: return macd_line[-1] if macd_line else 0, 0, 0
        k_sig = 2/(signal+1)
        sig_line = [sum(macd_line[:signal])/signal]
        for v in macd_line[signal:]: sig_line.append(v*k_sig + sig_line[-1]*(1-k_sig))
        histogram = macd_line[-1] - sig_line[-1]
        return macd_line[-1], sig_line[-1], histogram

    @staticmethod
    def _bollinger(closes, period=20, std_dev=2):
        if len(closes) < period: return closes[-1], closes[-1], closes[-1]
        window = closes[-period:]
        middle = sum(window) / period
        variance = sum((x - middle)**2 for x in window) / period
        std = math.sqrt(variance)
        return middle - std_dev*std, middle, middle + std_dev*std

    def evaluate(self, symbol="BTCUSD"):
        """Evaluate all 6 indicators and return composite signal."""
        candles_15m = self.api.get_candles(symbol, "15m", 60)
        candles_1h = self.api.get_candles(symbol, "1h", 30)
        spot = self.api.get_spot_price(symbol)

        if not candles_15m or spot == 0:
            log.warning("❌ No 15m candle data — cannot generate signal")
            return "NEUTRAL", 0, "no_data", False, spot, 0.50

        closes = [float(c.get("close", 0)) for c in candles_15m if c.get("close")]
        volumes = [float(c.get("volume", 0)) for c in candles_15m if c.get("volume")]
        if len(closes) < 26:
            log.warning(f"Only {len(closes)} closes — need 26+")
            return "NEUTRAL", 0, "insufficient_data", False, spot, 0.50

        # ── Individual Indicators ──
        score = 50  # start neutral
        breakdown = {}

        # 1. RSI (weight: 20)
        rsi = self._rsi(closes)
        if rsi < 35:
            rsi_pts = 20    # oversold → buy
        elif rsi < 45:
            rsi_pts = 10
        elif rsi > 65:
            rsi_pts = -20   # overbought → sell
        elif rsi > 55:
            rsi_pts = -10
        else:
            rsi_pts = 0
        score += rsi_pts
        breakdown["RSI"] = f"{rsi:.1f} → {rsi_pts:+d}"

        # 2. EMA 9/21 Cross (weight: 20)
        ema9 = self._ema(closes, 9)
        ema21 = self._ema(closes, 21)
        if ema9 and ema21:
            if ema9[-1] > ema21[-1]:
                ema_pts = 15
                if len(ema9) > 1 and len(ema21) > 1:
                    if ema9[-2] <= ema21[-2]:  # fresh cross
                        ema_pts = 20
            elif ema9[-1] < ema21[-1]:
                ema_pts = -15
                if len(ema9) > 1 and len(ema21) > 1:
                    if ema9[-2] >= ema21[-2]:
                        ema_pts = -20
            else:
                ema_pts = 0
        else:
            ema_pts = 0
        score += ema_pts
        breakdown["EMA9/21"] = f"{'bull' if ema_pts>0 else 'bear' if ema_pts<0 else 'flat'} → {ema_pts:+d}"

        # 3. MACD (weight: 20)
        macd_val, macd_sig, macd_hist = self._macd(closes)
        if macd_hist > 0:
            macd_pts = 10 if macd_val > 0 else 5
            if macd_hist > abs(macd_sig) * 0.1:
                macd_pts = 15
        elif macd_hist < 0:
            macd_pts = -10 if macd_val < 0 else -5
            if abs(macd_hist) > abs(macd_sig) * 0.1:
                macd_pts = -15
        else:
            macd_pts = 0
        score += macd_pts
        breakdown["MACD"] = f"H={macd_hist:.2f} → {macd_pts:+d}"

        # 4. Bollinger Bands (weight: 15)
        bb_lower, bb_mid, bb_upper = self._bollinger(closes)
        bb_range = bb_upper - bb_lower if bb_upper != bb_lower else 1
        bb_pos = (closes[-1] - bb_lower) / bb_range  # 0=lower, 1=upper
        if bb_pos < 0.15:
            bb_pts = 12   # near lower → buy
        elif bb_pos < 0.30:
            bb_pts = 6
        elif bb_pos > 0.85:
            bb_pts = -12  # near upper → sell
        elif bb_pos > 0.70:
            bb_pts = -6
        else:
            bb_pts = 0
        score += bb_pts
        breakdown["BB"] = f"pos={bb_pos:.2f} → {bb_pts:+d}"

        # 5. Volume Spike (weight: 15)
        vol_pts = 0
        if volumes and len(volumes) > 10:
            avg_vol = sum(volumes[-20:]) / min(len(volumes), 20)
            cur_vol = volumes[-1]
            if avg_vol > 0 and cur_vol > avg_vol * 1.5:
                # Volume spike — direction follows price
                if closes[-1] > closes[-2]:
                    vol_pts = 10
                elif closes[-1] < closes[-2]:
                    vol_pts = -10
        score += vol_pts
        breakdown["Volume"] = f"{vol_pts:+d}"

        # 6. Price Momentum — 3 bar (weight: 10)
        mom_pts = 0
        if len(closes) >= 4:
            if closes[-1] > closes[-2] > closes[-3]:
                mom_pts = 8
                if closes[-2] > closes[-4]:
                    mom_pts = 10
            elif closes[-1] < closes[-2] < closes[-3]:
                mom_pts = -8
                if closes[-2] < closes[-4]:
                    mom_pts = -10
        score += mom_pts
        breakdown["Momentum"] = f"{mom_pts:+d}"

        # ── Multi-Timeframe Confirmation (1h) ──
        tf_adjustment = 0
        if candles_1h:
            h_closes = [float(c.get("close", 0)) for c in candles_1h if c.get("close")]
            if len(h_closes) >= 21:
                h_ema9 = self._ema(h_closes, 9)
                h_ema21 = self._ema(h_closes, 21)
                hourly_bull = h_ema9[-1] > h_ema21[-1]
                signal_bull = score > 50
                if hourly_bull != signal_bull:
                    tf_adjustment = -15  # disagrees → reduce confidence
                    score += tf_adjustment
        breakdown["1h_confirm"] = f"{tf_adjustment:+d}"

        # ── Clamp & Classify ──
        score = max(0, min(100, score))
        if score >= 70:
            signal = "STRONG_BUY" if score >= 80 else "BUY"
            condition = "trend_up"
        elif score <= 30:
            signal = "STRONG_SELL" if score <= 20 else "SELL"
            condition = "trend_down"
        else:
            signal = "NEUTRAL"
            condition = "mixed"

        # Fibonacci proximity check
        near_fib = False
        if len(closes) >= 20:
            hi, lo = max(closes[-20:]), min(closes[-20:])
            rng = hi - lo
            if rng > 0:
                for level in [0.382, 0.500, 0.618]:
                    fib = hi - rng * level
                    if abs(closes[-1] - fib) / closes[-1] < 0.005:
                        near_fib = True; break

        # Log breakdown
        log.info(f"🔍 Signal Breakdown: {' | '.join(f'{k}:{v}' for k,v in breakdown.items())}")
        return signal, score, condition, near_fib, spot, 0.50


# ══════════════════════════════════════════════════════════════
# RISK MANAGER
# ══════════════════════════════════════════════════════════════
class RiskManager:
    def __init__(self, capital):
        self.capital = capital
        self.daily_pnl = 0.0
        self.open_count = 0
        self.trade_log = []

    def update_capital(self, c):
        if c > 0: self.capital = c; log.info(f"💰 Capital: ${c:,.4f}")

    def can_trade(self):
        if self.capital <= 0: return False, "Zero balance"
        if self.daily_pnl <= -(self.capital * DAILY_LOSS_LIMIT):
            return False, f"Daily loss limit hit: ${abs(self.daily_pnl):.2f}"
        if self.open_count >= 2: return False, "Max 2 positions open"
        return True, "OK"

    def contracts_to_buy(self, premium):
        budget = self.capital * OPTIONS_RISK_PCT
        return max(1, min(int(budget / max(premium, 0.01)), 10))

    def stop_level(self, entry): return entry * 0.50
    def target_level(self, entry): return entry * 2.0

    def record_close(self, pnl):
        self.daily_pnl += pnl
        self.open_count = max(0, self.open_count - 1)
        self.trade_log.append({"time": datetime.now().isoformat(), "pnl": pnl})

    def daily_reset(self):
        wins = len([t for t in self.trade_log if t["pnl"] > 0])
        total = max(len(self.trade_log), 1)
        log.info(f"📅 Day reset | P&L: ${self.daily_pnl:+.2f} | Win: {wins/total*100:.1f}%")
        self.daily_pnl = 0.0; self.trade_log = []


# ══════════════════════════════════════════════════════════════
# PERFORMANCE TRACKER
# ══════════════════════════════════════════════════════════════
class PerfTracker:
    def __init__(self):
        self.data = _load_json(PERFORMANCE_FILE, {
            "total_trades": 0, "wins": 0, "losses": 0,
            "total_pnl": 0.0, "max_drawdown": 0.0, "peak_pnl": 0.0,
            "trade_history": []})

    def record(self, pnl, symbol, reason):
        d = self.data
        d["total_trades"] += 1
        d["total_pnl"] += pnl
        if pnl > 0: d["wins"] += 1
        else: d["losses"] += 1
        if d["total_pnl"] > d["peak_pnl"]: d["peak_pnl"] = d["total_pnl"]
        dd = d["peak_pnl"] - d["total_pnl"]
        if dd > d["max_drawdown"]: d["max_drawdown"] = dd
        d["trade_history"].append({"time": datetime.now().isoformat(),
                                    "symbol": symbol, "pnl": pnl, "reason": reason})
        _save_json(PERFORMANCE_FILE, d)
        # Print summary every 10 trades
        if d["total_trades"] % 10 == 0:
            log.info(self.summary())

    def summary(self):
        d = self.data; t = max(d["total_trades"], 1)
        wr = d["wins"] / t * 100
        return (f"📈 Trades: {d['total_trades']} | Win: {wr:.0f}% | "
                f"PnL: ${d['total_pnl']:+.2f} | MaxDD: ${d['max_drawdown']:.2f}")


# ══════════════════════════════════════════════════════════════
# STRIKE SELECTOR
# ══════════════════════════════════════════════════════════════
class StrikeSelector:
    def select(self, spot, signal, score, near_fib):
        if signal in ("STRONG_BUY", "BUY"):
            opt_type = "call"
        elif signal in ("STRONG_SELL", "SELL"):
            opt_type = "put"
        else:
            return {"error": "No directional signal"}

        if score >= 80:
            offset = 0.02; stype = "OTM 2%"
        elif score >= 65:
            offset = 0.01; stype = "OTM 1%"
        else:
            offset = 0.005; stype = "ATM"

        if near_fib:
            offset = 0.005; stype = "ATM (fib)"

        raw = spot * (1 + offset) if opt_type == "call" else spot * (1 - offset)
        interval = 500 if BASE_UNDERLYING == "BTC" else 50
        strike = round(raw / interval) * interval

        return {"option_type": opt_type, "strike": strike, "strike_type": stype,
                "spot": spot, "score": score}


# ══════════════════════════════════════════════════════════════
# MAIN BOT
# ══════════════════════════════════════════════════════════════
class OptionsTradingBot:

    def __init__(self, capital=15000.0):
        self.api = DeltaAPI()
        self.signals = SignalEngine(self.api)
        self.striker = StrikeSelector()
        self.perf = PerfTracker()
        self.tg = TelegramNotifier()
        self.positions: List[OptionsPosition] = []
        self.last_reset = datetime.now().date()

        wallet = self.api.get_wallet_balance()
        if wallet["available"] > 0:
            capital = wallet["available"]

        self.risk = RiskManager(capital)
        self._load_positions()

        log.info("═" * 60)
        log.info("🎯 OPTIONS BOT v3.0 STARTED")
        log.info(f"   Mode: {'📝 PAPER TRADE' if PAPER_TRADE else '🔴 LIVE'}")
        log.info(f"   API: {BASE_URL}")
        log.info(f"   Balance: ${capital:,.4f} | Leverage: {LEVERAGE}x")
        log.info(f"   Min score: {OPTIONS_MIN_SCORE} | Risk/trade: {OPTIONS_RISK_PCT*100:.1f}%")
        log.info(f"   Open positions: {len([p for p in self.positions if p.status=='open'])}")
        log.info("═" * 60)

    def _save_positions(self):
        data = []
        for p in self.positions:
            c = p.contract
            data.append({
                "contract": {"symbol": c.symbol, "underlying": c.underlying,
                    "expiry": c.expiry, "strike": c.strike, "option_type": c.option_type,
                    "premium": c.premium, "delta": c.delta, "implied_vol": c.implied_vol,
                    "open_interest": c.open_interest, "bid": c.bid, "ask": c.ask,
                    "spread_pct": c.spread_pct, "product_id": c.product_id,
                    "expiry_datetime": c.expiry_datetime},
                "side": p.side, "quantity": p.quantity, "entry_premium": p.entry_premium,
                "entry_time": p.entry_time, "stop_premium": p.stop_premium,
                "target_premium": p.target_premium, "order_id": p.order_id,
                "exit_premium": p.exit_premium, "exit_reason": p.exit_reason,
                "pnl": p.pnl, "status": p.status, "leverage": p.leverage,
                "peak_premium": p.peak_premium})
        _save_json(POSITIONS_FILE, data)

    def _load_positions(self):
        data = _load_json(POSITIONS_FILE, [])
        self.positions = []
        for d in data:
            try:
                c = d["contract"]
                contract = OptionContract(
                    symbol=c["symbol"], underlying=c["underlying"],
                    expiry=c.get("expiry",""), strike=c["strike"],
                    option_type=c["option_type"], premium=c["premium"],
                    delta=c.get("delta",0), implied_vol=c.get("implied_vol",0.5),
                    open_interest=c.get("open_interest",0),
                    bid=c.get("bid",0), ask=c.get("ask",0),
                    spread_pct=c.get("spread_pct",0),
                    product_id=c.get("product_id",0),
                    expiry_datetime=c.get("expiry_datetime",""))
                pos = OptionsPosition(
                    contract=contract, side=d["side"], quantity=d["quantity"],
                    entry_premium=d["entry_premium"], entry_time=d["entry_time"],
                    stop_premium=d["stop_premium"], target_premium=d["target_premium"],
                    order_id=d.get("order_id",""), status=d.get("status","open"),
                    leverage=d.get("leverage",LEVERAGE),
                    peak_premium=d.get("peak_premium",d["entry_premium"]))
                self.positions.append(pos)
                if pos.status == "open": self.risk.open_count += 1
            except Exception as e:
                log.error(f"Position load error: {e}")

    def run(self):
        while True:
            try:
                if datetime.now().date() != self.last_reset:
                    self.risk.daily_reset()
                    self.last_reset = datetime.now().date()
                    log.info(self.perf.summary())
                self._cycle()
                log.info(f"⏳ Sleeping {CYCLE_INTERVAL//60} min...\n")
                time.sleep(CYCLE_INTERVAL)
            except KeyboardInterrupt:
                log.info("🛑 Stopped."); self._save_positions(); break
            except Exception as e:
                log.error(f"Loop error: {e}", exc_info=True); time.sleep(60)

    def _cycle(self):
        ts = datetime.now(pytz.UTC)
        log.info(f"── Cycle {ts.strftime('%H:%M:%S UTC')} ──────────────")

        wallet = self.api.get_wallet_balance()
        if wallet["available"] > 0:
            self.risk.update_capital(wallet["available"])

        # Monitor & auto-close open positions
        for pos in [p for p in self.positions if p.status == "open"]:
            self._check_auto_close(pos)
        for pos in [p for p in self.positions if p.status == "open"]:
            self._monitor(pos)

        can, reason = self.risk.can_trade()
        if not can:
            log.info(f"🚫 {reason}"); return

        # Generate signal
        sym = f"{BASE_UNDERLYING}USD"
        sig, score, cond, near_fib, spot, iv = self.signals.evaluate(sym)
        log.info(f"📊 Signal: {sig} | Score: {score} | Spot: ${spot:,.0f}")

        if score < OPTIONS_MIN_SCORE or sig == "NEUTRAL":
            log.info(f"⚪ Score {score} below {OPTIONS_MIN_SCORE} or neutral — skip")
            return

        # Get options chain & select strike
        chain = self.api.get_options_chain(BASE_UNDERLYING)
        if not chain:
            log.warning("No chain data"); return

        sel = self.striker.select(spot, sig, score, near_fib)
        if "error" in sel:
            log.warning(f"Strike: {sel['error']}"); return

        best = self.api.find_best_contract(chain, sel["strike"], sel["option_type"])
        if not best:
            log.warning(f"No contract near {sel['strike']}"); return

        self._open_position(sel, best)

    def _open_position(self, sel, bc):
        ep = bc["ask"] if bc["ask"] > 0 else bc["mark_price"]
        if ep <= 0: return
        qty = self.risk.contracts_to_buy(ep)
        order = self.api.place_order(bc["product_id"], "buy", qty, bc["symbol"])
        if not order: return

        pos = OptionsPosition(
            contract=OptionContract(
                symbol=bc["symbol"], underlying=BASE_UNDERLYING, expiry="",
                strike=bc["strike"], option_type=sel["option_type"],
                premium=ep, delta=bc.get("delta",0), implied_vol=bc.get("iv",0.5),
                open_interest=bc.get("oi",0), bid=bc["bid"], ask=bc["ask"],
                spread_pct=bc["spread_pct"], product_id=bc["product_id"]),
            side="buy", quantity=qty, entry_premium=ep,
            entry_time=datetime.now(pytz.UTC).isoformat(),
            stop_premium=self.risk.stop_level(ep),
            target_premium=self.risk.target_level(ep),
            order_id=str(order.get("result",{}).get("id","")),
            peak_premium=ep)
        self.positions.append(pos)
        self.risk.open_count += 1
        self._save_positions()

        msg = f"🎯 OPENED: {qty}x {bc['symbol']} @ ${ep:.4f} ({sel['strike_type']})"
        log.info(msg)
        self.tg.send(msg)

    def _monitor(self, pos):
        pd = self.api.get_option_premium(pos.contract.symbol)
        if not pd: return
        cur = pd.get("mark_price", pos.entry_premium)
        if cur <= 0: return

        # Update peak for trailing stop
        if cur > pos.peak_premium:
            pos.peak_premium = cur

        # Trailing stop: if we've hit 50%+ profit, trail at 30% below peak
        if pos.peak_premium >= pos.entry_premium * 1.5:
            trailing_stop = pos.peak_premium * 0.70
            if cur <= trailing_stop:
                self._close(pos, cur, "TRAILING_STOP"); return
            # Also update stop to break-even minimum
            pos.stop_premium = max(pos.entry_premium, trailing_stop)

        if cur <= pos.stop_premium:
            self._close(pos, cur, "STOP_LOSS")
        elif cur >= pos.target_premium:
            self._close(pos, cur, "TAKE_PROFIT")
        else:
            pct = (cur - pos.entry_premium) / max(pos.entry_premium, 0.0001) * 100
            log.info(f"📊 {pos.contract.symbol} ${cur:.4f} ({pct:+.1f}%)")

    def _check_auto_close(self, pos):
        now = datetime.now(pytz.UTC)
        try:
            edt = datetime.fromisoformat(pos.entry_time)
            if edt.tzinfo is None: edt = edt.replace(tzinfo=pytz.UTC)
        except: edt = now
        hrs = (now - edt).total_seconds() / 3600

        reason = ""
        if hrs >= MAX_HOLD_HOURS:
            reason = f"AUTO_CLOSE ({hrs:.1f}h)"
        if pos.contract.expiry_datetime:
            try:
                exp = datetime.fromisoformat(pos.contract.expiry_datetime.replace("Z","+00:00"))
                mins = (exp - now).total_seconds() / 60
                if 0 < mins < CLOSE_BEFORE_EXPIRY_MINS:
                    reason = f"EXPIRY ({mins:.0f}min left)"
                elif mins <= 0:
                    reason = "EXPIRED"
            except: pass
        if reason:
            pd = self.api.get_option_premium(pos.contract.symbol)
            cur = pd.get("mark_price", pos.entry_premium * 0.5) if pd else pos.entry_premium * 0.5
            self._close(pos, cur, reason)

    def _close(self, pos, exit_prem, reason):
        self.api.place_order(pos.contract.product_id, "sell", pos.quantity, pos.contract.symbol)
        pnl_per = exit_prem - pos.entry_premium
        total = pnl_per * pos.quantity
        pos.exit_premium = exit_prem
        pos.exit_reason = reason
        pos.pnl = total
        pos.status = "closed"
        self.risk.record_close(total)
        self.perf.record(total, pos.contract.symbol, reason)
        self._save_positions()

        history = _load_json(TRADE_HISTORY_FILE, [])
        history.append({"symbol": pos.contract.symbol, "entry": pos.entry_premium,
                         "exit": exit_prem, "qty": pos.quantity, "pnl": total,
                         "reason": reason, "time": datetime.now(pytz.UTC).isoformat()})
        _save_json(TRADE_HISTORY_FILE, history)

        e = "✅" if total > 0 else "❌"
        msg = f"{e} CLOSED [{reason}] {pos.contract.symbol} ${pos.entry_premium:.4f}→${exit_prem:.4f} PnL: ${total:+.4f}"
        log.info(msg); self.tg.send(msg)


# ══════════════════════════════════════════════════════════════
# ENTRY POINT
# ══════════════════════════════════════════════════════════════
if __name__ == "__main__":
    mode = "📝 PAPER TRADE" if PAPER_TRADE else "🔴 LIVE TRADING"
    print("\n" + "█" * 60)
    print(f"  CRYPTO OPTIONS BOT v3.0 — {mode}")
    print(f"  API:  {BASE_URL}")
    if FORCE_PAPER_TRADE:
        print("  ⚠️  Paper mode FORCED — edit FORCE_PAPER_TRADE to go live")
    print("█" * 60)
    print("\n  Starting in 3 seconds...")
    time.sleep(3)
    bot = OptionsTradingBot(capital=float(os.getenv("CAPITAL", "15000")))
    bot.run()
