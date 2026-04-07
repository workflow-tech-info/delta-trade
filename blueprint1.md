# 🐺 DELTA OPTIONS BOT v4.0 — TRADING BLUEPRINT

> *"The wolf doesn't hunt blindly. It reads the terrain, studies its prey, and strikes with precision."*

---

## 🏗️ Architecture Overview

```
┌──────────────────────────────────────────────────────────┐
│                     BOT v4.0 FLOW                        │
│                                                          │
│  ┌─────────┐    ┌──────────────┐    ┌──────────────┐    │
│  │ 10m Wait │──▶│ Multi-TF     │──▶│ Pattern      │    │
│  │ (Cycle)  │   │ Analysis     │   │ Detection    │    │
│  └─────────┘    │ 5m/10m/15m/1h│   │ 16+ Patterns │    │
│                  └──────┬───────┘   └──────┬───────┘    │
│                         │                   │            │
│                   ┌─────▼───────────────────▼─────┐     │
│                   │   SIGNAL ENGINE               │     │
│                   │   Score 0-100                  │     │
│                   │   BUY / SELL / NEUTRAL         │     │
│                   └──────────────┬─────────────────┘     │
│                                  │ Score ≥ 55?           │
│                            ┌─────▼─────┐                │
│                            │ OPTIONS   │                │
│                            │ CHAIN     │                │
│                            │ + GREEKS  │                │
│                            └─────┬─────┘                │
│                                  │ Score + Rank          │
│                            ┌─────▼─────┐                │
│                            │ SET 50x   │                │
│                            │ LEVERAGE  │                │
│                            └─────┬─────┘                │
│                                  │                       │
│                            ┌─────▼─────┐                │
│                            │ PLACE     │                │
│                            │ ORDER     │                │
│                            └─────┬─────┘                │
│                                  │                       │
│                            ┌─────▼─────┐                │
│                            │ TRAILING  │                │
│                            │ STOP 50%  │                │
│                            │ MONITOR   │                │
│                            └───────────┘                │
└──────────────────────────────────────────────────────────┘
```

---

## 📐 Configuration (Production Settings)

| Parameter | Value | Why |
|-----------|-------|-----|
| Cycle Interval | 600s (10 min) | Avoids overtrading, matches primary TF |
| Min Score | 55/100 | Quality entries only |
| Leverage | 50x | Set via API before each trade |
| Max Positions | 1 | Full focus, never 2 at once |
| Trailing Stop | 50% from peak | Ride profits, cut losses |
| Max Hold Time | 12 hours | Safety net for stale positions |
| Risk per Trade | 0.5% of capital | Conservative position sizing |
| Primary TF | 10m candles | Signal generation |
| Confirm TFs | 5m, 10m, 15m, 1h | Multi-timeframe consensus |

---

## 🧠 SIGNAL ENGINE — How We Decide to Trade

### Phase 1: Multi-Timeframe Analysis

Before any trade, the bot fetches candles from **4 timeframes** and runs the same indicators on each:

#### Timeframes Used
| TF | Resolution | Purpose | Weight |
|----|-----------|---------|--------|
| 5m | `"5m"` | Short-term momentum & timing | Confirmer |
| 10m | `"10m"` | **Primary signal** (score comes from here) | Primary |
| 15m | `"15m"` | Medium-term trend | Confirmer |
| 1h | `"1h"` | Higher TF trend direction | Confirmer |

#### Consensus Rule
- Each TF produces: `bullish` (score ≥ 55), `bearish` (score ≤ 45), or `neutral`
- **At least 3 of 4** must agree to enter a trade
- If all 4 agree → extra +5 score bonus
- If fewer than 3 agree → NEUTRAL (no trade)

### Phase 2: Technical Indicators (Per Timeframe)

Each timeframe is analyzed with these indicators:

#### 1. RSI (Relative Strength Index) — Period 14
```
RSI < 35  → +15 points (oversold, likely to bounce UP)
RSI < 45  → +8 points  (slightly oversold)
RSI > 55  → -8 points  (slightly overbought)
RSI > 65  → -15 points (overbought, likely to drop DOWN)
```

**How RSI works**: Measures the speed and magnitude of price changes.
- RSI = 100 - (100 / (1 + avg_gains / avg_losses))
- Uses 14-period lookback
- Range: 0-100

#### 2. EMA Crossover (9 vs 21 periods)
```
EMA9 > EMA21 → +10 points (short-term above long-term = uptrend)
EMA9 < EMA21 → -10 points (short-term below long-term = downtrend)
```

**How EMA works**: Exponential Moving Average gives more weight to recent prices.
- EMA = price × k + EMA_prev × (1 - k), where k = 2/(period+1)
- 9-period is fast (reacts quickly), 21-period is slow (smooths noise)

#### 3. Momentum Check (5-period lookback)
```
Current close > Close 5 bars ago → +5 points  (price going up)
Current close < Close 5 bars ago → -5 points  (price going down)
```

#### Score Calculation
```
Base Score = 50 (neutral)
+ RSI component (-15 to +15)
+ EMA component (-10 to +10)
+ Momentum component (-5 to +5)
+ Pattern bonus (see below)
= Final Score (clamped 0-100)
```

---

## 🕯️ CANDLESTICK PATTERN DETECTION

The bot scans the primary timeframe (10m) candles for these patterns:

### Bullish Patterns (add points = more likely to BUY)

| Pattern | Score | Description |
|---------|-------|-------------|
| 🟢 Bullish Engulfing | +10 | Small red candle engulfed by large green candle |
| 🟢 Morning Star | +12 | 3-bar: big red → small body → big green (reversal) |
| 🟢 Three White Soldiers | +12 | 3 consecutive green candles, each closing higher |
| 🟢 Hammer | +8 | Small body at top, long lower wick after downtrend |
| 🟢 Inverted Hammer | +6 | Long upper wick after downtrend |
| 🟢 Bullish Harami | +6 | Current candle inside previous (reversal) |
| 🟢 Dragonfly Doji | +5 | Tiny body, long lower wick (bullish at support) |
| 🟢 Bullish Flag | +8 | Strong up-move → tight consolidation → continuation |
| 🟢 Double Bottom | +10 | Two lows at same level (strong reversal) |

### Bearish Patterns (subtract points = more likely to SELL)

| Pattern | Score | Description |
|---------|-------|-------------|
| 🔴 Bearish Engulfing | -10 | Small green candle engulfed by large red candle |
| 🔴 Evening Star | -12 | 3-bar: big green → small body → big red (reversal) |
| 🔴 Three Black Crows | -12 | 3 consecutive red candles, each closing lower |
| 🔴 Shooting Star | -8 | Long upper wick after uptrend |
| 🔴 Hanging Man | -5 | Small body at top, long lower wick after uptrend |
| 🔴 Bearish Harami | -6 | Current candle inside previous (reversal) |
| 🔴 Gravestone Doji | -5 | Tiny body, long upper wick (bearish at resistance) |
| 🔴 Bearish Flag | -8 | Strong down-move → tight consolidation → continuation |
| 🔴 Double Top | -10 | Two highs at same level (strong reversal) |

### How Detection Works
```
Body = |close - open|
Upper Wick = high - max(open, close)
Lower Wick = min(open, close) - low
Range = high - low

Doji: body < 10% of range
Hammer: lower_wick ≥ 2x body, upper_wick < 0.5x body, after downtrend
Engulfing: current body completely covers previous body, opposite direction
Morning Star: big red → tiny body → big green (3 candles)
```

---

## 🧬 OPTION GREEKS — Prey Strengths & Weaknesses

After a signal is confirmed, the bot fetches the options chain and analyzes each contract's Greeks to pick the **best option to trade**.

### What the Greeks Tell Us

| Greek | Symbol | Meaning | Good For Us |
|-------|--------|---------|-------------|
| **Delta** (Speed) | Δ | How much option price moves per $1 of BTC | High Δ = fast profits |
| **Gamma** (Reflexes) | Γ | How fast Delta changes (acceleration) | High Γ = explosive moves |
| **Theta** (Decay) | Θ | How much value bleeds per day (time decay) | Low Θ = less bleeding |
| **Vega** (Vol Sensitivity) | V | How much option gains from volatility spike | High V = vol profits |
| **IV** (Fear Level) | % | Implied volatility — market's fear gauge | Low IV = cheap options |

### How We Score Each Contract

```python
# DELTA: Sweet spot = 0.30 to 0.50 (ATM/slight OTM)
|Δ| 0.30-0.50  → +20 points  # 🎯 Perfect directional exposure
|Δ| 0.20-0.60  → +10 points  # ✅ Acceptable
|Δ| > 0.70     → +5 points   # Deep ITM, expensive but safe
|Δ| < 0.20     → -5 points   # Too far OTM, lottery ticket

# GAMMA: Higher = more explosive
Γ > 0.005  → +10 points  # 🎯 Sharp reflexes
Γ > 0.001  → +5 points   # ✅ Decent
Γ < 0.001  → +0 points   # 😴 Sluggish

# THETA: Less negative = less daily cost
Θ > -50    → +10 points  # 🟢 Low bleed
Θ > -100   → +5 points   # 😐 Moderate
Θ < -100   → -5 points   # 🔴 Heavy bleed, burning money

# VEGA: Higher = profits from vol spikes
V > 10     → +5 points   # 💪 Strong vol sensitivity
V < 10     → +0 points   # 😐 Weak

# STRIKE PROXIMITY: Closer to target = better
proximity_score = max(0, 20 - (strike_diff / spot_price) * 100)
```

### Log Output Example
```
🏹 TOP PREY CANDIDATES (ranked by Greeks):
    👑 C-BTC-71000-100426 | Score: 47
       ⚔️  STRENGTHS & WEAKNESSES:
       └─ Δ Delta (Speed):     [███████░░░] +0.3842 ⚡ Fast
       └─ Γ Gamma (Reflexes):  0.003421 🎯 Sharp
       └─ Θ Theta (Decay):     -32.4500/day 🟢 Low bleed
       └─ V Vega (Vol. Sens.): 14.8700 💪 Strong
       └─ IV (Fear Level):     42.3% 😎 Calm
       └─ 💰 Bid/Ask: $1234.5678/$1245.6789 | Spread: 0.9%
```

### Why This Selection Matters
Delta 0.30-0.50 gives us the "sweet spot": enough directional exposure without paying for deep ITM premium. High gamma means if BTC moves in our direction, our option gains accelerate. Low theta means we're not hemorrhaging money every day. High vega means if a volatility event happens, we profit.

---

## 🛡️ TRAILING STOP LOSS — Ride Profits, Cut Losses

### How It Works

```
Entry: $1000 premium (peak = $1000, stop = $500)

Price rises to $1200:
  → Peak = $1200, Stop = $600 (raised!)
  
Price rises to $2000:
  → Peak = $2000, Stop = $1000 (already in profit territory!)

Price drops to $1000:
  → STOP HIT! Exit at $1000 (0% loss, breakeven!)
  → Without trailing stop, we'd still be waiting...
```

### Rules
1. **Stop starts at Entry × (1 - 0.50)** = 50% below entry
2. **Peak tracks the highest mark price seen** for this position
3. **Stop = Peak × (1 - 0.50)** — always 50% below the peak
4. **Stop ONLY moves UP**, never down — once a level is locked, it stays
5. **No fixed take-profit** — we ride the wave until the trailing stop catches us
6. **Max Hold: 12 hours** — safety net if options go sideways

### Exit Conditions (Priority Order)
1. ✅ Trailing stop hit (profit or loss)
2. ✅ Max hold time exceeded (12 hours)
3. ✅ Premium reaches zero (worthless)

---

## ⚙️ LEVERAGE — How 50x Works

### What It Means
- You control $10,000 worth of BTC options with only $200 margin
- If the option moves +2%, your P&L is +100% (2% × 50)
- If the option moves -2%, your P&L is -100% (liquidation risk)

### API Call
```python
POST /v2/products/{product_id}/orders/leverage
Body: {"leverage": 50}
Response: {"success": true, "result": {"leverage": 50, "order_margin": "xxx"}}
```

### Why 50x (Not 100x)
- 100x = too thin margin, easily liquidated on BTC volatility
- 50x = enough exposure with room for 2% adverse move before danger
- Options already have natural leverage (~70x via premium/notional ratio)

---

## 🔒 POSITION RULES

| Rule | Setting | Reason |
|------|---------|--------|
| Max simultaneous | 1 | Full focus, don't over-expose |
| One direction only | BUY call or BUY put | No naked selling |
| Risk per trade | 0.5% of capital | $1 per trade on $200 account |
| Order type | Market | Instant fill, no slippage games |
| Reduce only exit | Yes | Close sells are reduce-only |

---

## 📊 COMPLETE TRADE LIFECYCLE

```
CYCLE 1: Assessment
├── Fetch 5m, 10m, 15m, 1h candles
├── Run RSI, EMA, Momentum on each
├── Run Pattern Detection on 10m
├── Check consensus (≥3 of 4 agree?)
├── Score ≥ 55? → CONTINUE
└── Score < 55? → SLEEP 10 minutes

CYCLE 2: Hunting
├── Fetch options chain (CALL or PUT based on signal)
├── Parse Greeks for all contracts
├── Score each contract (Delta, Gamma, Theta, Vega, proximity)
├── Rank by Greek score → pick the BEST
├── Display top 3 candidates with strengths/weaknesses
└── Select TOP candidate

CYCLE 3: Attack
├── Set 50x leverage via API
├── Calculate position size (budget / ask price)
├── Place market order
├── Record peak = entry price
├── Set initial stop = entry × 0.50
└── LOG: 🏆 PREY CAPTURED!

CYCLES 4-N: Monitoring
├── Fetch current mark price
├── If price > peak → update peak, raise stop
├── If price ≤ stop → CLOSE (trailing stop exit)
├── If hours held > 12 → CLOSE (max hold exit)
├── LOG: 📈/📉 with current price, P&L, peak, stop
└── If position closed → hunt again next cycle
```

---

## 🧮 SCORING BREAKDOWN — Full Example

### Hypothetical BTC at $70,000

```
5m  Candles: RSI=42(+8), EMA9>EMA21(+10), Momentum UP(+5) = 73 → BULLISH
10m Candles: RSI=38(+15), EMA9>EMA21(+10), Momentum UP(+5) = 80
    + Bullish Engulfing pattern (+10) = 90 → BULLISH
15m Candles: RSI=51(+0), EMA9<EMA21(-10), Momentum DOWN(-5) = 35 → BEARISH
1h  Candles: RSI=44(+8), EMA9>EMA21(+10), Momentum UP(+5) = 73 → BULLISH

CONSENSUS: 3 bullish + 1 bearish = 3/4 AGREE ✅
PRIMARY (10m) SCORE: 90 + consensus bonus 5 = 95

FINAL: BUY signal, Score 95/100 → ENTER TRADE
```

---

## 📋 QUICK REFERENCE — File Paths

| File | Location | Purpose |
|------|----------|---------|
| Bot Code | `~/delta-trade/options_bot.py` | Main trading bot |
| Environment | `~/delta-trade/.env` | API keys, config |
| Positions | `~/delta-trade/bot_data/positions.json` | Active trades |
| Trade History | `~/delta-trade/bot_data/trade_history.json` | All closed trades |
| Service | `trading-bot.service` | Systemd service |
| Logs | `journalctl -u trading-bot -f` | Live logs |

---

## ⚠️ RISK WARNINGS

1. **Options expire worthless** — If BTC doesn't move enough by expiry, your premium = $0
2. **Theta decay** — Every day, your option loses value even if BTC doesn't move
3. **50x leverage** — A 2% adverse move wipes the position margin
4. **Trailing stop at 50%** — You can still lose up to 50% of premium per trade
5. **API failures** — If the bot can't reach the exchange, it can't close positions
6. **Testnet ≠ Real** — Demo fills are instant and perfect; live markets have slippage

---

## 🔄 DEPLOYMENT CHECKLIST

```bash
# 1. Update code
scp options_bot.py ubuntu@<vps>:~/delta-trade/

# 2. Clear old positions
rm -f ~/delta-trade/bot_data/positions.json

# 3. Verify .env
cat ~/delta-trade/.env
# Should show: LEVERAGE=50, CAPITAL=200, PAPER_TRADE=false

# 4. Restart
sudo systemctl restart trading-bot

# 5. Watch the hunt
journalctl -u trading-bot -f
```
