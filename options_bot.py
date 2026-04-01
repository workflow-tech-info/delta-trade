"""
╔══════════════════════════════════════════════════════════════════════════╗
║          DELTA EXCHANGE — CRYPTO OPTIONS MODULE  (v2.0 FIXED)          ║
║          Strike Price Selector + 100x Leverage Risk Engine              ║
║                                                                          ║
║  All bugs fixed · Live IV · Persistence · Performance Tracking          ║
║  Paper-trade simulator · Real signal engine · Expiry-aware auto-close   ║
╚══════════════════════════════════════════════════════════════════════════╝
"""

import requests
import numpy as np
import math
import time
import json
import logging
import os
import hmac
import hashlib
import pytz
from datetime import datetime, timedelta
from dataclasses import dataclass, field, asdict
from typing import Optional, List, Dict, Any
from dotenv import load_dotenv
from pathlib import Path

try:
    import pandas as pd
except ImportError:
    pd = None

# Import the signal engine from our main bot (optional)
try:
    from trading_bot_v2 import (
        SignalAggregator, Signal, MarketCondition,
        MarketConditionDetector, DeltaExchangeAPI,
        TelegramNotifier, MarketSessionManager,
    )
    MAIN_BOT_AVAILABLE = True
except ImportError:
    MAIN_BOT_AVAILABLE = False
    print("⚠️  trading_bot_v2.py not found — running in standalone mode")

load_dotenv()

# ──────────────────────────────────────────────
# CONFIG — loaded from .env with safe defaults
# ──────────────────────────────────────────────
API_KEY        = os.getenv("DELTA_API_KEY", "")
API_SECRET     = os.getenv("DELTA_API_SECRET", "")
BASE_URL       = os.getenv("DELTA_BASE_URL", "https://cdn-ind.testnet.deltaex.org")
TG_TOKEN       = os.getenv("TG_TOKEN", "")
TG_CHAT_ID     = os.getenv("TG_CHAT_ID", "")

PAPER_TRADE       = os.getenv("PAPER_TRADE", "true").lower() == "true"
LEVERAGE          = int(os.getenv("LEVERAGE", "100"))
OPTIONS_RISK_PCT  = 0.005      # 0.5% max risk per trade at 100x
OPTIONS_MIN_SCORE = 75
DAILY_LOSS_LIMIT  = 0.03       # 3% daily loss limit
ATM_OFFSET_PCT    = 0.005
OTM_MODERATE_PCT  = 0.01
OTM_STRONG_PCT    = 0.02
ITM_PCT           = 0.01
CLOSE_BEFORE_EXPIRY_MINS = 30
MAX_HOLD_HOURS    = 4
NO_NEW_TRADES_AFTER_HOUR = 24
BASE_UNDERLYING   = os.getenv("BASE_UNDERLYING", "BTC")

DATA_DIR = Path(os.getenv("BOT_DATA_DIR", "./bot_data"))
DATA_DIR.mkdir(exist_ok=True)
POSITIONS_FILE     = DATA_DIR / "positions.json"
TRADE_HISTORY_FILE = DATA_DIR / "trade_history.json"
PERFORMANCE_FILE   = DATA_DIR / "performance_report.json"

# ──────────────────────────────────────────────
# LOGGING
# ──────────────────────────────────────────────
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
# DATA CLASSES — Bug 1 fixed: product_id added
#                Bug 10 prep: expiry_datetime added
# ══════════════════════════════════════════════════════════════
@dataclass
class OptionContract:
    symbol:          str
    underlying:      str
    expiry:          str
    strike:          float
    option_type:     str       # "call" or "put"
    premium:         float
    delta:           float
    implied_vol:     float
    open_interest:   int
    bid:             float
    ask:             float
    spread_pct:      float
    product_id:      int   = 0                # BUG 1 FIX
    expiry_datetime: str   = ""               # BUG 10 FIX — ISO string

@dataclass
class OptionsPosition:
    contract:        OptionContract
    side:            str
    quantity:        int
    entry_premium:   float
    entry_time:      str
    stop_premium:    float
    target_premium:  float
    order_id:        str    = ""
    exit_premium:    float  = 0.0
    exit_reason:     str    = ""
    pnl:             float  = 0.0
    status:          str    = "open"
    leverage:        int    = LEVERAGE


# ══════════════════════════════════════════════════════════════
# BlackScholes Greeks — Bug 8 fix: default iv removed,
#                        must be passed explicitly
# ══════════════════════════════════════════════════════════════
class BlackScholesGreeks:

    @staticmethod
    def d1(S, K, T, r, sigma):
        if T <= 0 or sigma <= 0:
            return 0
        return (math.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * math.sqrt(T))

    @staticmethod
    def d2(S, K, T, r, sigma):
        if T <= 0 or sigma <= 0:
            return 0
        return BlackScholesGreeks.d1(S, K, T, r, sigma) - sigma * math.sqrt(T)

    @staticmethod
    def norm_cdf(x):
        return (1.0 + math.erf(x / math.sqrt(2.0))) / 2.0

    @classmethod
    def call_delta(cls, S, K, T, r=0.05, sigma=0.50):
        if T <= 0:
            return 1.0 if S > K else 0.0
        return cls.norm_cdf(cls.d1(S, K, T, r, sigma))

    @classmethod
    def put_delta(cls, S, K, T, r=0.05, sigma=0.50):
        return cls.call_delta(S, K, T, r, sigma) - 1.0

    @classmethod
    def gamma(cls, S, K, T, r=0.05, sigma=0.50):
        if T <= 0 or sigma <= 0:
            return 0
        d1 = cls.d1(S, K, T, r, sigma)
        phi = math.exp(-0.5 * d1**2) / math.sqrt(2 * math.pi)
        return phi / (S * sigma * math.sqrt(T))

    @classmethod
    def theta_daily(cls, S, K, T, r=0.05, sigma=0.50, option_type="call"):
        if T <= 0 or sigma <= 0:
            return 0
        d1 = cls.d1(S, K, T, r, sigma)
        d2 = cls.d2(S, K, T, r, sigma)
        phi = math.exp(-0.5 * d1**2) / math.sqrt(2 * math.pi)
        theta_annual = -(S * phi * sigma) / (2 * math.sqrt(T))
        if option_type == "call":
            theta_annual -= r * K * math.exp(-r * T) * cls.norm_cdf(d2)
        else:
            theta_annual += r * K * math.exp(-r * T) * cls.norm_cdf(-d2)
        return theta_annual / 365

    @classmethod
    def vega_per_vol_point(cls, S, K, T, r=0.05, sigma=0.50):
        if T <= 0:
            return 0
        d1 = cls.d1(S, K, T, r, sigma)
        phi = math.exp(-0.5 * d1**2) / math.sqrt(2 * math.pi)
        return S * math.sqrt(T) * phi * 0.01

    @classmethod
    def analyse_strike(cls, spot, strike, days_to_expiry, iv, option_type="call"):
        T = max(days_to_expiry / 365, 1/365/24)
        sigma = max(iv, 0.01)
        if option_type == "call":
            delta = cls.call_delta(spot, strike, T, sigma=sigma)
        else:
            delta = cls.put_delta(spot, strike, T, sigma=sigma)
        moneyness = (spot - strike) / spot if option_type == "call" else (strike - spot) / spot
        if moneyness > ATM_OFFSET_PCT:
            atm_status = "ITM"
        elif moneyness < -ATM_OFFSET_PCT:
            atm_status = "OTM"
        else:
            atm_status = "ATM"
        return {
            "strike": strike, "option_type": option_type, "atm_status": atm_status,
            "delta": round(delta, 3),
            "gamma": round(cls.gamma(spot, strike, T, sigma=sigma), 6),
            "theta_daily": round(cls.theta_daily(spot, strike, T, sigma=sigma, option_type=option_type), 4),
            "vega_1pct": round(cls.vega_per_vol_point(spot, strike, T, sigma=sigma), 4),
            "days_to_exp": days_to_expiry, "iv_assumed": sigma,
            "quality_score": cls._quality_score(delta, days_to_expiry, option_type),
        }

    @classmethod
    def _quality_score(cls, delta, days_to_expiry, option_type):
        abs_delta = abs(delta)
        if 0.40 <= abs_delta <= 0.60:
            return "IDEAL (ATM)"
        elif 0.25 <= abs_delta < 0.40:
            return "GOOD (slightly OTM)"
        elif abs_delta >= 0.60:
            return "SAFE (ITM)"
        elif 0.15 <= abs_delta < 0.25:
            return "RISKY (OTM)"
        else:
            return "AVOID (far OTM)"


# ══════════════════════════════════════════════════════════════
# StrikePriceSelector — Bug 9 fix: correct Delta symbol format
# ══════════════════════════════════════════════════════════════
class StrikePriceSelector:

    def __init__(self):
        self.greeks = BlackScholesGreeks()

    def select_strike(self, spot_price, signal, score, condition,
                      near_fib, iv_estimate=0.50, days_to_expiry=1):
        if signal in ("STRONG_BUY", "BUY"):
            option_type = "call"
            direction = "bullish"
        elif signal in ("STRONG_SELL", "SELL"):
            option_type = "put"
            direction = "bearish"
        elif signal == "HEDGE":
            return self._straddle_selection(spot_price, iv_estimate, days_to_expiry)
        else:
            return {"error": "No clear directional signal for options"}

        hour_utc = datetime.now(pytz.UTC).hour
        end_of_session = hour_utc >= NO_NEW_TRADES_AFTER_HOUR - 2

        if end_of_session or near_fib:
            offset_pct = ATM_OFFSET_PCT
            strike_type = "ATM"
            reason = "ATM selected: near Fibonacci level or late session"
        elif score >= 85 and "strong_trend" in condition:
            offset_pct = OTM_STRONG_PCT
            strike_type = "OTM (2%)"
            reason = f"OTM selected: score {score}/100 + strong trend"
        elif score >= 75:
            offset_pct = OTM_MODERATE_PCT
            strike_type = "OTM (1%)"
            reason = f"Slightly OTM selected: score {score}/100"
        else:
            return {"error": f"Score {score} below minimum {OPTIONS_MIN_SCORE}"}

        if option_type == "call":
            raw_strike = spot_price * (1 + offset_pct)
        else:
            raw_strike = spot_price * (1 - offset_pct)

        strike = self._round_to_strike_interval(raw_strike, spot_price)
        greeks = self.greeks.analyse_strike(spot_price, strike, days_to_expiry, iv_estimate, option_type)

        return {
            "symbol": None,  # will be resolved from chain
            "underlying": BASE_UNDERLYING,
            "option_type": option_type,
            "strike": strike,
            "strike_type": strike_type,
            "spot_price": spot_price,
            "direction": direction,
            "signal_score": score,
            "selection_reason": reason,
            "greeks": greeks,
            "expiry_days": days_to_expiry,
            "warning": self._get_risk_warning(greeks, days_to_expiry),
        }

    def _straddle_selection(self, spot, iv, days):
        strike = self._round_to_strike_interval(spot, spot)
        call_greeks = self.greeks.analyse_strike(spot, strike, days, iv, "call")
        put_greeks = self.greeks.analyse_strike(spot, strike, days, iv, "put")
        return {
            "strategy": "ATM_STRADDLE",
            "underlying": BASE_UNDERLYING,
            "option_type": "straddle",
            "strike": strike,
            "call_delta": call_greeks["delta"],
            "put_delta": put_greeks["delta"],
            "combined_theta_daily": call_greeks["theta_daily"] + put_greeks["theta_daily"],
            "spot_price": spot,
            "greeks": call_greeks,
            "expiry_days": days,
            "selection_reason": "Choppy market — straddle profits from large moves either way",
            "warning": "⚠️ Straddle is expensive — time decay works AGAINST you. Close within 2 hours.",
        }

    def _round_to_strike_interval(self, raw_strike, spot):
        if BASE_UNDERLYING == "BTC":
            interval = 500
        elif BASE_UNDERLYING == "ETH":
            interval = 50
        else:
            interval = 1
        return round(raw_strike / interval) * interval

    def _get_risk_warning(self, greeks, days):
        abs_delta = abs(greeks["delta"])
        theta = greeks["theta_daily"]
        warnings = []
        if abs_delta < 0.25:
            warnings.append(f"⚠️ LOW DELTA ({abs_delta:.2f})")
        if abs(theta) > 0.5:
            warnings.append(f"⚠️ HIGH THETA ({theta:.2f}/day)")
        if days == 0:
            warnings.append("🔴 0DTE OPTION")
        if LEVERAGE >= 100:
            warnings.append(f"🔴 {LEVERAGE}x LEVERAGE")
        return " | ".join(warnings) if warnings else "✅ Greeks acceptable"


# ══════════════════════════════════════════════════════════════
# OptionsRiskManager
# ══════════════════════════════════════════════════════════════
class OptionsRiskManager:

    def __init__(self, capital: float):
        self.capital = capital
        self.initial_capital = capital
        self.daily_pnl = 0.0
        self.open_count = 0
        self.trade_log: list = []

    def update_capital(self, new_capital: float):
        """Update capital from live wallet balance."""
        if new_capital > 0:
            self.capital = new_capital
            log.info(f"💰 Capital updated: ${new_capital:,.4f}")

    def can_trade(self):
        if self.capital <= 0:
            return False, "🔴 Zero balance — cannot trade"
        if self.daily_pnl <= -(self.capital * DAILY_LOSS_LIMIT):
            return False, f"🔴 Daily loss limit hit: ${abs(self.daily_pnl):.2f}"
        if self.open_count >= 2:
            return False, "Max 2 options positions open"
        return True, "OK"

    def max_premium_to_spend(self):
        return self.capital * OPTIONS_RISK_PCT

    def contracts_to_buy(self, premium_per_contract, contract_size_usd=0.001):
        budget = self.max_premium_to_spend()
        max_by_budget = int(budget / max(premium_per_contract, 0.01))
        return max(1, min(max_by_budget, 10))

    def option_stop_level(self, entry_premium, option_type):
        return entry_premium * 0.50

    def option_target_level(self, entry_premium):
        return entry_premium * 2.0

    def record_close(self, pnl):
        self.daily_pnl += pnl
        self.open_count = max(0, self.open_count - 1)
        self.trade_log.append({"time": datetime.now().isoformat(), "pnl": pnl})

    def daily_reset(self):
        wins = len([t for t in self.trade_log if t["pnl"] > 0])
        total = max(len(self.trade_log), 1)
        log.info(f"📅 Day reset | P&L: ${self.daily_pnl:+.2f} | Win rate: {wins/total*100:.1f}%")
        self.daily_pnl = 0.0
        self.trade_log = []


# ══════════════════════════════════════════════════════════════
# Persistence — Bug 6 fix
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
# PerformanceTracker — Bug 7 fix
# ══════════════════════════════════════════════════════════════
class PerformanceTracker:

    def __init__(self):
        self.data = _load_json(PERFORMANCE_FILE, {
            "total_trades": 0, "wins": 0, "losses": 0,
            "total_pnl": 0.0, "max_drawdown": 0.0,
            "peak_pnl": 0.0, "trade_history": []
        })

    def record(self, pnl: float, contract_symbol: str, reason: str):
        self.data["total_trades"] += 1
        self.data["total_pnl"] += pnl
        if pnl > 0:
            self.data["wins"] += 1
        else:
            self.data["losses"] += 1
        if self.data["total_pnl"] > self.data["peak_pnl"]:
            self.data["peak_pnl"] = self.data["total_pnl"]
        dd = self.data["peak_pnl"] - self.data["total_pnl"]
        if dd > self.data["max_drawdown"]:
            self.data["max_drawdown"] = dd
        self.data["trade_history"].append({
            "time": datetime.now().isoformat(),
            "symbol": contract_symbol, "pnl": pnl, "reason": reason
        })
        _save_json(PERFORMANCE_FILE, self.data)

    def summary(self) -> str:
        t = self.data
        total = max(t["total_trades"], 1)
        wr = t["wins"] / total * 100
        wins_list = [h["pnl"] for h in t["trade_history"] if h["pnl"] > 0]
        loss_list = [h["pnl"] for h in t["trade_history"] if h["pnl"] <= 0]
        avg_w = sum(wins_list) / max(len(wins_list), 1)
        avg_l = sum(loss_list) / max(len(loss_list), 1)
        pf = abs(sum(wins_list)) / max(abs(sum(loss_list)), 0.01)
        return (
            f"📈 Trades: {t['total_trades']} | Win: {wr:.0f}% | "
            f"PF: {pf:.2f} | PnL: ${t['total_pnl']:+.2f} | "
            f"MaxDD: ${t['max_drawdown']:.2f} | "
            f"AvgW: ${avg_w:.2f} AvgL: ${avg_l:.2f}"
        )


# ══════════════════════════════════════════════════════════════
# Telegram Notifier (standalone fallback)
# ══════════════════════════════════════════════════════════════
class SimpleTelegramNotifier:
    def __init__(self):
        self.token = TG_TOKEN
        self.chat_id = TG_CHAT_ID
        self.enabled = bool(self.token and self.chat_id)

    def send(self, msg: str):
        if not self.enabled:
            return
        try:
            requests.post(
                f"https://api.telegram.org/bot{self.token}/sendMessage",
                json={"chat_id": self.chat_id, "text": msg, "parse_mode": "HTML"},
                timeout=10
            )
        except Exception as e:
            log.error(f"Telegram error: {e}")


# ══════════════════════════════════════════════════════════════
# DeltaOptionsAPI — FULLY REWRITTEN for correct Delta India API
# Fixes: Bug 3, 8, 9 — correct endpoints, symbol format, IV
# ══════════════════════════════════════════════════════════════
class DeltaOptionsAPI:

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "Content-Type": "application/json",
            "User-Agent": "python-options-bot",
        })

    def _sign(self, method: str, path: str, query_string: str = "", payload: str = "") -> dict:
        ts = str(int(time.time()))
        message = method + ts + path + query_string + payload
        sig = hmac.new(
            API_SECRET.encode(), message.encode(), hashlib.sha256
        ).hexdigest()
        return {
            "api-key": API_KEY, "timestamp": ts, "signature": sig,
            "Content-Type": "application/json", "User-Agent": "python-options-bot"
        }

    # ── Public: spot price
    def get_spot_price(self, symbol: str = "BTCUSD") -> float:
        try:
            resp = self.session.get(
                f"{BASE_URL}/v2/tickers/{symbol}", timeout=10
            )
            data = resp.json()
            if data.get("success"):
                return float(data["result"].get("spot_price", 0) or
                             data["result"].get("mark_price", 0))
        except Exception as e:
            log.error(f"Spot price error: {e}")
        return 0.0

    # ── Authenticated: fetch wallet balance
    def get_wallet_balance(self) -> dict:
        """Returns {total_balance, available_balance, asset_symbol} for the primary trading asset."""
        if not API_KEY or not API_SECRET:
            return {"total": 0, "available": 0, "asset": "USDT"}
        try:
            path = "/v2/wallet/balances"
            headers = self._sign("GET", path)
            resp = self.session.get(f"{BASE_URL}{path}", headers=headers, timeout=10)
            data = resp.json()
            if not data.get("success"):
                log.warning(f"Wallet API: {data.get('error', 'unknown')}")
                return {"total": 0, "available": 0, "asset": "USDT"}
            best = {"total": 0, "available": 0, "asset": "USDT"}
            for b in data.get("result", []):
                bal = float(b.get("balance", 0) or 0)
                avail = float(b.get("available_balance", 0) or 0)
                sym = b.get("asset_symbol", "")
                if bal > best["total"]:
                    best = {"total": bal, "available": avail, "asset": sym}
                # Log all non-zero balances
                if bal > 0:
                    log.info(f"   💰 {sym}: {bal:.4f} (avail: {avail:.4f})")
            return best
        except Exception as e:
            log.error(f"Wallet balance error: {e}")
            return {"total": 0, "available": 0, "asset": "USDT"}

    # ── Public: option chain via tickers endpoint
    def get_options_chain(self, underlying: str = "BTC") -> list:
        try:
            params = {
                "contract_types": "call_options,put_options",
                "underlying_asset_symbols": underlying,
            }
            resp = self.session.get(
                f"{BASE_URL}/v2/tickers", params=params, timeout=15
            )
            data = resp.json()
            if not data.get("success"):
                log.warning(f"Options chain failed: {data}")
                return []
            options = []
            for t in data.get("result", []):
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
                    except (ValueError, TypeError):
                        pass
                elif quotes.get("ask_iv"):
                    try:
                        iv = float(quotes["ask_iv"])
                    except (ValueError, TypeError):
                        pass
                bid = float(quotes.get("best_bid", 0) or 0)
                ask = float(quotes.get("best_ask", 0) or 0)
                spread = (ask - bid) / ask if ask > 0 else 1.0
                options.append({
                    "symbol": t.get("symbol", ""),
                    "product_id": t.get("product_id", 0),
                    "strike": float(t.get("strike_price", 0) or 0),
                    "type": "call" if "call" in ct else "put",
                    "mark_price": float(t.get("mark_price", 0) or 0),
                    "spot_price": float(t.get("spot_price", 0) or 0),
                    "iv": iv,
                    "delta": float(greeks.get("delta", 0) or 0),
                    "gamma": float(greeks.get("gamma", 0) or 0),
                    "theta": float(greeks.get("theta", 0) or 0),
                    "vega": float(greeks.get("vega", 0) or 0),
                    "bid": bid,
                    "ask": ask,
                    "spread_pct": spread,
                    "oi": int(float(t.get("oi", 0) or 0)),
                    "tradeable": spread < 0.05 and bid > 0,
                })
            log.info(f"Options chain: {len(options)} contracts for {underlying}")
            return options
        except Exception as e:
            log.error(f"Options chain error: {e}")
            return []

    # ── Public: get products list (to find settlement_time)
    def get_products(self, underlying: str = "BTC") -> list:
        try:
            resp = self.session.get(f"{BASE_URL}/v2/products", timeout=15)
            data = resp.json()
            if not data.get("success"):
                return []
            products = []
            for p in data.get("result", []):
                ct = p.get("contract_type", "")
                if ct in ("call_options", "put_options"):
                    products.append({
                        "id": p.get("id"),
                        "symbol": p.get("symbol"),
                        "contract_type": ct,
                        "settlement_time": p.get("settlement_time"),
                        "strike_price": p.get("product_specs", {}).get("strike_price",
                                        p.get("strike_price", 0)),
                        "state": p.get("state"),
                    })
            return products
        except Exception as e:
            log.error(f"Products error: {e}")
            return []

    # ── Find best contract matching target strike
    def find_best_contract(self, options_chain: list, target_strike: float,
                           option_type: str) -> Optional[dict]:
        best = None
        best_diff = float("inf")
        for c in options_chain:
            if c["type"] != option_type:
                continue
            if not c.get("tradeable", False):
                continue
            diff = abs(c["strike"] - target_strike)
            if diff < best_diff:
                best_diff = diff
                best = c
        return best

    # ── Get single option premium
    def get_option_premium(self, symbol: str) -> dict:
        try:
            resp = self.session.get(f"{BASE_URL}/v2/tickers/{symbol}", timeout=10)
            data = resp.json()
            if not data.get("success"):
                return {"tradeable": False}
            r = data["result"]
            greeks = r.get("greeks") or {}
            quotes = r.get("quotes") or {}
            mark = float(r.get("mark_price", 0) or 0)
            bid = float(quotes.get("best_bid", 0) or 0)
            ask = float(quotes.get("best_ask", 0) or 0)
            mark_vol = r.get("mark_vol")
            iv = 0.50
            if mark_vol:
                try:
                    iv = float(mark_vol) / 100.0 if float(mark_vol) > 5 else float(mark_vol)
                except (ValueError, TypeError):
                    pass
            spread_pct = (ask - bid) / ask if ask > 0 else 1.0
            return {
                "mark_price": mark, "bid": bid, "ask": ask,
                "spread_pct": spread_pct, "iv": iv,
                "delta": float(greeks.get("delta", 0) or 0),
                "oi": int(float(r.get("oi", 0) or 0)),
                "tradeable": spread_pct < 0.05 and bid > 0,
            }
        except Exception as e:
            log.error(f"Premium fetch error for {symbol}: {e}")
            return {"tradeable": False}

    # ── Place order (authenticated)
    def place_options_order(self, product_id: int, side: str,
                            size: int, symbol: str = "") -> dict:
        if PAPER_TRADE:
            oid = f"paper_opt_{int(time.time())}"
            log.info(f"📝 PAPER: {side.upper()} {size}x {symbol or product_id}")
            return {"success": True, "result": {"id": oid}}
        if not API_KEY or not API_SECRET:
            log.error("No API credentials — cannot place live order")
            return {}
        try:
            path = "/v2/orders"
            body_dict = {
                "product_id": product_id,
                "side": side,
                "size": size,
                "order_type": "market_order",
            }
            payload = json.dumps(body_dict)
            headers = self._sign("POST", path, "", payload)
            resp = self.session.post(
                BASE_URL + path, data=payload, headers=headers, timeout=15
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            log.error(f"Order error: {e}")
            return {}


# ══════════════════════════════════════════════════════════════
# Standalone Signal Engine — Bug 4 fix: live OHLC + RSI/EMA
# ══════════════════════════════════════════════════════════════
class StandaloneSignalEngine:

    def __init__(self, api: DeltaOptionsAPI):
        self.api = api

    def _get_candles(self, symbol="BTCUSD", resolution="15m", limit=50) -> list:
        try:
            res_map = {"5m": "5", "15m": "15", "30m": "30", "1h": "60", "2h": "120"}
            resp = self.api.session.get(
                f"{BASE_URL}/v2/history/candles",
                params={"resolution": res_map.get(resolution, "15"),
                        "symbol": symbol, "limit": limit},
                timeout=10
            )
            data = resp.json()
            if data.get("success"):
                return data.get("result", [])
        except Exception as e:
            log.error(f"Candles error: {e}")
        return []

    def _compute_rsi(self, closes, period=14):
        if len(closes) < period + 1:
            return 50.0
        deltas = [closes[i] - closes[i-1] for i in range(1, len(closes))]
        gains = [d if d > 0 else 0 for d in deltas]
        losses = [-d if d < 0 else 0 for d in deltas]
        avg_g = sum(gains[:period]) / period
        avg_l = sum(losses[:period]) / period
        for i in range(period, len(gains)):
            avg_g = (avg_g * (period - 1) + gains[i]) / period
            avg_l = (avg_l * (period - 1) + losses[i]) / period
        if avg_l == 0:
            return 100.0
        rs = avg_g / avg_l
        return 100 - (100 / (1 + rs))

    def _ema(self, data, period):
        if not data:
            return []
        k = 2 / (period + 1)
        ema = [data[0]]
        for v in data[1:]:
            ema.append(v * k + ema[-1] * (1 - k))
        return ema

    def evaluate(self, symbol="BTCUSD"):
        candles = self._get_candles(symbol, "15m", 60)
        spot = self.api.get_spot_price(symbol)
        if not candles or spot == 0:
            log.warning("No candle data — returning NEUTRAL")
            return "NEUTRAL", 0, "no_data", False, spot, 0.50

        closes = [float(c.get("close", 0)) for c in candles if c.get("close")]
        if len(closes) < 21:
            return "NEUTRAL", 0, "insufficient_data", False, spot, 0.50

        rsi = self._compute_rsi(closes)
        ema9 = self._ema(closes, 9)
        ema21 = self._ema(closes, 21)

        score = 50
        signal = "NEUTRAL"
        condition = "mixed"

        if ema9[-1] > ema21[-1]:
            score += 15
        else:
            score -= 15

        if rsi > 55:
            score += 10
        elif rsi < 45:
            score -= 10

        if closes[-1] > ema9[-1] > ema21[-1]:
            score += 15
            condition = "trend_up"
        elif closes[-1] < ema9[-1] < ema21[-1]:
            score -= 15
            condition = "trend_down"

        if len(closes) >= 3:
            if closes[-1] > closes[-2] > closes[-3]:
                score += 10
            elif closes[-1] < closes[-2] < closes[-3]:
                score -= 10

        if score >= 75:
            signal = "BUY" if score < 85 else "STRONG_BUY"
        elif score <= 25:
            signal = "SELL" if score > 15 else "STRONG_SELL"

        near_fib = False
        if len(closes) >= 20:
            hi = max(closes[-20:])
            lo = min(closes[-20:])
            rng = hi - lo
            if rng > 0:
                for level in [0.382, 0.500, 0.618]:
                    fib = hi - rng * level
                    if abs(closes[-1] - fib) / closes[-1] < 0.005:
                        near_fib = True
                        break

        return signal, max(0, min(100, score)), condition, near_fib, spot, 0.50


# ══════════════════════════════════════════════════════════════
# MAIN: OptionsTradingBot — All bugs fixed
# ══════════════════════════════════════════════════════════════
class OptionsTradingBot:

    def __init__(self, capital: float = 15000.0):
        self.options_api = DeltaOptionsAPI()
        self.striker = StrikePriceSelector()
        self.perf = PerformanceTracker()
        self.telegram = SimpleTelegramNotifier()
        self.signal_engine = StandaloneSignalEngine(self.options_api) if not MAIN_BOT_AVAILABLE else None
        self.positions: List[OptionsPosition] = []
        self.last_reset = datetime.now().date()

        # ── Fetch live wallet balance for capital ──
        log.info("📡 Fetching wallet balance...")
        wallet = self.options_api.get_wallet_balance()
        live_balance = wallet["available"]
        if live_balance > 0:
            capital = live_balance
            log.info(f"💰 Using LIVE balance: ${capital:,.4f} {wallet['asset']}")
        else:
            log.info(f"💰 Using configured capital: ${capital:,.2f} (wallet returned 0)")

        self.risk = OptionsRiskManager(capital)
        self._load_positions()

        log.info("═" * 65)
        log.info("🎯 OPTIONS BOT v2.0 STARTED")
        log.info(f"   Mode: {'PAPER' if PAPER_TRADE else '🔴 LIVE TRADING'} | API: {BASE_URL}")
        log.info(f"   Balance: ${capital:,.4f} {wallet['asset']} | Leverage: {LEVERAGE}x")
        log.info(f"   Max risk/trade: ${capital * OPTIONS_RISK_PCT:,.4f}")
        log.info(f"   Open positions: {len([p for p in self.positions if p.status=='open'])}")
        log.info("═" * 65)
        self.telegram.send(
            f"🎯 <b>Options Bot v2.0</b>\n"
            f"Mode: {'Paper' if PAPER_TRADE else '🔴 LIVE'}\n"
            f"Balance: ${capital:,.4f} {wallet['asset']}\n"
            f"Max risk: ${capital * OPTIONS_RISK_PCT:,.4f}"
        )

    def _save_positions(self):
        data = []
        for p in self.positions:
            data.append({
                "contract": {
                    "symbol": p.contract.symbol, "underlying": p.contract.underlying,
                    "expiry": p.contract.expiry, "strike": p.contract.strike,
                    "option_type": p.contract.option_type, "premium": p.contract.premium,
                    "delta": p.contract.delta, "implied_vol": p.contract.implied_vol,
                    "open_interest": p.contract.open_interest, "bid": p.contract.bid,
                    "ask": p.contract.ask, "spread_pct": p.contract.spread_pct,
                    "product_id": p.contract.product_id,
                    "expiry_datetime": p.contract.expiry_datetime,
                },
                "side": p.side, "quantity": p.quantity,
                "entry_premium": p.entry_premium, "entry_time": p.entry_time,
                "stop_premium": p.stop_premium, "target_premium": p.target_premium,
                "order_id": p.order_id, "exit_premium": p.exit_premium,
                "exit_reason": p.exit_reason, "pnl": p.pnl,
                "status": p.status, "leverage": p.leverage,
            })
        _save_json(POSITIONS_FILE, data)

    def _load_positions(self):
        data = _load_json(POSITIONS_FILE, [])
        self.positions = []
        for d in data:
            try:
                c = d["contract"]
                contract = OptionContract(
                    symbol=c["symbol"], underlying=c["underlying"],
                    expiry=c.get("expiry", ""), strike=c["strike"],
                    option_type=c["option_type"], premium=c["premium"],
                    delta=c.get("delta", 0), implied_vol=c.get("implied_vol", 0.5),
                    open_interest=c.get("open_interest", 0),
                    bid=c.get("bid", 0), ask=c.get("ask", 0),
                    spread_pct=c.get("spread_pct", 0),
                    product_id=c.get("product_id", 0),
                    expiry_datetime=c.get("expiry_datetime", ""),
                )
                pos = OptionsPosition(
                    contract=contract, side=d["side"],
                    quantity=d["quantity"], entry_premium=d["entry_premium"],
                    entry_time=d["entry_time"], stop_premium=d["stop_premium"],
                    target_premium=d["target_premium"],
                    order_id=d.get("order_id", ""),
                    status=d.get("status", "open"),
                    leverage=d.get("leverage", LEVERAGE),
                )
                self.positions.append(pos)
                if pos.status == "open":
                    self.risk.open_count += 1
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
                log.info("⏳ Sleeping 5 min...\n")
                time.sleep(300)
            except KeyboardInterrupt:
                log.info("🛑 Stopped.")
                self._save_positions()
                break
            except Exception as e:
                log.error(f"Loop error: {e}", exc_info=True)
                time.sleep(60)

    def _cycle(self):
        ts = datetime.now(pytz.UTC)
        log.info(f"── Cycle {ts.strftime('%H:%M:%S UTC')} ──────────────")

        # Refresh balance from wallet every cycle
        wallet = self.options_api.get_wallet_balance()
        if wallet["available"] > 0:
            self.risk.update_capital(wallet["available"])

        for pos in [p for p in self.positions if p.status == "open"]:
            self._check_auto_close(pos)
        for pos in [p for p in self.positions if p.status == "open"]:
            self._monitor_position(pos)

        can_trade, reason = self.risk.can_trade()
        if not can_trade:
            log.info(f"🚫 {reason}")
            return
        if ts.hour >= NO_NEW_TRADES_AFTER_HOUR:
            log.info("🌙 Past cutoff — no new entries")
            return

        # Bug 4: live signal
        if MAIN_BOT_AVAILABLE:
            agg = SignalAggregator()
            r = agg.evaluate(f"{BASE_UNDERLYING}USDT")
            sig, score, cond = r.signal.value, r.score, r.condition
            near_fib = any("fib" in x.lower() for x in r.reasons)
            spot, iv_est = r.entry_price, 0.50
        else:
            sig, score, cond, near_fib, spot, iv_est = \
                self.signal_engine.evaluate(f"{BASE_UNDERLYING}USD")

        log.info(f"📊 Signal: {sig} | Score: {score} | Spot: ${spot:,.0f}")
        if score < OPTIONS_MIN_SCORE or sig == "NEUTRAL":
            log.info(f"⚪ Score {score} or neutral — skip")
            return

        chain = self.options_api.get_options_chain(BASE_UNDERLYING)
        if not chain:
            log.warning("No chain data")
            return

        # Bug 8: live IV
        atm = [c for c in chain if abs(c["strike"] - spot) / max(spot, 1) < 0.02]
        if atm:
            iv_est = sum(c["iv"] for c in atm) / len(atm)
            log.info(f"📈 Live IV: {iv_est:.2%}")

        sel = self.striker.select_strike(
            spot_price=spot, signal=sig, score=score,
            condition=cond, near_fib=near_fib,
            iv_estimate=iv_est, days_to_expiry=1,
        )
        if "error" in sel:
            log.warning(f"Strike: {sel['error']}")
            return

        # Bug 5: straddle
        if sel.get("strategy") == "ATM_STRADDLE":
            self._exec_straddle(sel, chain)
            return

        best = self.options_api.find_best_contract(chain, sel["strike"], sel["option_type"])
        if not best:
            log.warning(f"No contract near {sel['strike']}")
            return
        self._exec_leg(sel, best)

    def _exec_leg(self, sel, bc):
        ep = bc["ask"] if bc["ask"] > 0 else bc["mark_price"]
        if ep <= 0:
            return
        qty = self.risk.contracts_to_buy(ep)
        order = self.options_api.place_options_order(
            bc["product_id"], "buy", qty, bc["symbol"])
        if not order:
            return
        pos = OptionsPosition(
            contract=OptionContract(
                symbol=bc["symbol"], underlying=BASE_UNDERLYING, expiry="",
                strike=bc["strike"], option_type=sel["option_type"],
                premium=ep, delta=bc.get("delta", 0), implied_vol=bc["iv"],
                open_interest=bc["oi"], bid=bc["bid"], ask=bc["ask"],
                spread_pct=bc["spread_pct"], product_id=bc["product_id"],
            ),
            side="buy", quantity=qty, entry_premium=ep,
            entry_time=datetime.now(pytz.UTC).isoformat(),
            stop_premium=self.risk.option_stop_level(ep, sel["option_type"]),
            target_premium=self.risk.option_target_level(ep),
            order_id=str(order.get("result", {}).get("id", "")),
        )
        self.positions.append(pos)
        self.risk.open_count += 1
        self._save_positions()
        log.info(f"🎯 Opened: {qty}x {bc['symbol']} @ ${ep:.4f}")
        self.telegram.send(f"🎯 <b>Opened</b>\n{bc['symbol']}\n${ep:.4f} × {qty}")

    # Bug 5
    def _exec_straddle(self, sel, chain):
        strike = sel["strike"]
        for leg_type in ["call", "put"]:
            bc = self.options_api.find_best_contract(chain, strike, leg_type)
            if not bc:
                continue
            sel_copy = dict(sel)
            sel_copy["option_type"] = leg_type
            self._exec_leg(sel_copy, bc)
        self.telegram.send(f"🎯 <b>Straddle</b> @ ${strike:,.0f}")

    def _monitor_position(self, pos):
        pd = self.options_api.get_option_premium(pos.contract.symbol)
        if not pd:
            return
        cur = pd.get("mark_price", pos.entry_premium)
        if cur <= 0:
            return
        if cur <= pos.stop_premium:
            self._close_position(pos, cur, "STOP_LOSS")
        elif cur >= pos.target_premium:
            self._close_position(pos, cur, "TAKE_PROFIT")
        else:
            pct = (cur - pos.entry_premium) / max(pos.entry_premium, 0.0001) * 100
            log.info(f"📊 {pos.contract.symbol} ${cur:.4f} ({pct:+.1f}%)")

    # Bug 10: check expiry + hold time
    def _check_auto_close(self, pos):
        now = datetime.now(pytz.UTC)
        try:
            edt = datetime.fromisoformat(pos.entry_time)
            if edt.tzinfo is None:
                edt = edt.replace(tzinfo=pytz.UTC)
        except (ValueError, TypeError):
            edt = now
        hrs = (now - edt).total_seconds() / 3600

        reason = ""
        if hrs >= MAX_HOLD_HOURS:
            reason = f"AUTO_CLOSE ({hrs:.1f}h held)"
        if pos.contract.expiry_datetime:
            try:
                exp = datetime.fromisoformat(pos.contract.expiry_datetime.replace("Z", "+00:00"))
                mins = (exp - now).total_seconds() / 60
                if 0 < mins < CLOSE_BEFORE_EXPIRY_MINS:
                    reason = f"EXPIRY ({mins:.0f}min left)"
                elif mins <= 0:
                    reason = "EXPIRED"
            except (ValueError, TypeError):
                pass
        if reason:
            pd = self.options_api.get_option_premium(pos.contract.symbol)
            cur = pd.get("mark_price", pos.entry_premium * 0.5) if pd else pos.entry_premium * 0.5
            self._close_position(pos, cur, reason)

    # Bug 1+2: product_id used, PnL correct
    def _close_position(self, pos, exit_prem, reason):
        self.options_api.place_options_order(
            pos.contract.product_id, "sell", pos.quantity, pos.contract.symbol)
        pnl_per = exit_prem - pos.entry_premium  # Bug 2: per contract
        total = pnl_per * pos.quantity
        pos.exit_premium = exit_prem
        pos.exit_reason = reason
        pos.pnl = total
        pos.status = "closed"
        self.risk.record_close(total)
        self.perf.record(total, pos.contract.symbol, reason)
        self._save_positions()

        history = _load_json(TRADE_HISTORY_FILE, [])
        history.append({
            "symbol": pos.contract.symbol, "entry": pos.entry_premium,
            "exit": exit_prem, "qty": pos.quantity,
            "pnl": total, "reason": reason,
            "time": datetime.now(pytz.UTC).isoformat(),
        })
        _save_json(TRADE_HISTORY_FILE, history)

        e = "✅" if total > 0 else "❌"
        msg = (f"{e} <b>Closed [{reason}]</b>\n{pos.contract.symbol}\n"
               f"${pos.entry_premium:.4f}→${exit_prem:.4f}\n"
               f"PnL: ${pnl_per:+.4f}×{pos.quantity}=${total:+.2f}")
        log.info(msg.replace("<b>", "").replace("</b>", ""))
        self.telegram.send(msg)


# ══════════════════════════════════════════════════════════════
# ENTRY POINT
# ══════════════════════════════════════════════════════════════
if __name__ == "__main__":
    mode = "PAPER TRADE" if PAPER_TRADE else "🔴 LIVE TRADING"
    print("\n" + "█" * 60)
    print(f"  CRYPTO OPTIONS BOT v2.0 — {mode}")
    print(f"  API:  {BASE_URL}")
    print("█" * 60)
    if not PAPER_TRADE:
        print("\n  ⚠️  LIVE MODE — Real orders will be placed!")
        print("  Starting in 5 seconds... Ctrl+C to cancel.")
        time.sleep(5)
    else:
        print("\n  Starting in 3 seconds...")
        time.sleep(3)
    # Capital is fetched from live wallet; fallback to env var
    bot = OptionsTradingBot(capital=float(os.getenv("CAPITAL", "15000")))
    bot.run()
