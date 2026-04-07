# 🐺 THE WOLF'S PLAYBOOK — Options Bot v5.0

> *"A wolf never hunts blindly. It reads the wind, studies the terrain, watches the herd for days — and strikes once, with absolute precision."*
> 
> This bot is that wolf. It watches Bitcoin like a predator watches prey. It studies the landscape across weeks and days, identifies the strongest signals, and makes ONE devastating trade per day — with full conviction.

---

## 🗺️ THE HUNTING GROUND — What This Bot Actually Does

Imagine you're a wolf standing on a mountaintop, looking down at a vast valley where herds of animals (price movements) roam. Before you run down there and attack, you need to answer three questions:

1. **Which way is the herd moving?** (Is BTC going UP or DOWN this week?)
2. **Is this the right moment to strike?** (Are the short-term signals confirming?)
3. **Which prey is the easiest to catch?** (Which option contract gives us the best edge?)

The bot answers all three questions automatically, every 2 minutes, 24/7.

---

## 🏗️ THE 4-LAYER HUNT — How Decisions Are Made

Think of it like a 4-step checklist. ALL 4 must pass before we trade.

```
LAYER 1: 🗻 Survey the Landscape  (Weekly + Daily charts)
    "Is the overall market bullish, bearish, or choppy?"
    → Sets the DAILY BIAS (locked for 24 hours)
         │
         ▼
LAYER 2: 🌲 Check the Forest     (4-hour, 1-hour, 15-min charts)
    "Do the medium-term trends agree with our daily bias?"
    → Need at least 2 of 3 to agree
         │
         ▼
LAYER 3: 🎯 Spot the Prey        (5-minute chart — our main hunting ground)
    "Is there a specific entry signal RIGHT NOW?"
    → Score must be ≥ 70 out of 100
         │
         ▼
LAYER 4: 🏹 Attack               (Option selection + order execution)
    "Pick the best option contract using Greeks, set leverage, execute!"
    → Full wallet × 50x leverage → Trailing stop protects profits
```

If ANY layer fails → **No trade.** The wolf waits.

---

## 🗻 LAYER 1: Survey the Landscape (The Most Important Part)

### What Happens Here

Every day at midnight UTC, the bot does a **deep reconnaissance** — like a wolf climbing to the highest peak to survey the entire territory before deciding where to hunt.

It downloads **months of price data** and runs EVERYTHING on it:

### 📅 Weekly Chart (20 candles = ~5 months of history)

The wolf checks the **"high ground"** — the big picture trend that takes weeks to form.

### 📅 Daily Chart (120 candles = ~4 months of history)

The wolf checks the **"forest floor"** — yesterday's action and recent momentum.

### What Gets Analyzed on Each Timeframe

#### 📡 Indicators (The Wolf's Senses)

| Indicator | What It Is (Simple) | What the Wolf Sees |
|-----------|--------------------|--------------------|
| **RSI** (Relative Strength Index) | Measures if price has gone too far up or too far down | RSI < 30 = "Prey is EXHAUSTED" (oversold, likely to bounce UP) |
| | | RSI > 70 = "Prey is OVERHEATED" (overbought, likely to drop DOWN) |
| **EMA 9 vs 21** | Two moving averages — fast vs slow | Fast above slow = "Short-term bulls are LEADING" |
| | | Fast below slow = "Bears seized control" |
| **EMA 20 vs 50** | Same concept but bigger picture | EMA20 > EMA50 = "The herd migrates UPHILL" |
| | | EMA20 < EMA50 = "The herd heads DOWNHILL" |
| **MACD** | Momentum indicator (are things speeding up?) | MACD > Signal = "Momentum favors the hunter" |
| | | MACD < Signal = "Momentum fading, prey escaping" |
| **Momentum** | Simply: is price higher now than 5/10 bars ago? | Up = bulls pushing, Down = bears pushing |

> **For a total beginner:** Think of RSI like a thermometer. Below 30°? The market has a fever of selling — it's going to recover. Above 70°? Market is overheating with buying — it's going to cool down.

#### 🕯️ Candlestick Patterns (The Wolf's Footprint Reading)

Every candle on a chart is a record of what happened in that time period. The body shows open→close, the wicks show how high/low it went. When candles form specific shapes, they tell us what the buyers and sellers are doing.

##### 🕯️ Single Candle Tracks (1 candle = 1 footprint)

| Pattern | What It Looks Like | What It Means | Score |
|---------|-------------------|---------------|-------|
| **🟢 Hammer** | Small body at TOP, long wick going DOWN | "The sellers tried to push price down, but buyers fought back and WON" — bullish reversal | +8 |
| **🔴 Shooting Star** | Small body at BOTTOM, long wick going UP | "Buyers tried to push up, but sellers slammed them back DOWN" — bearish reversal | -8 |
| **🟢 Bullish Marubozu** | Full green candle, NO wicks at all | "Pure bull power — buyers dominated from open to close with zero resistance" | +8 |
| **🔴 Bearish Marubozu** | Full red candle, NO wicks at all | "Pure bear power — sellers crushed it the entire bar" | -8 |
| **⚪ Doji** | Tiny body, cross-shaped | "Indecision — buyers and sellers are equally matched. Something is about to change." | 0 |
| **🟢 Dragonfly Doji** | Cross with long bottom wick | "Sellers pushed down hard but got completely rejected — bulls are coming" | +5 |
| **🔴 Gravestone Doji** | Cross with long top wick | "Buyers pushed up but got smacked down — bears are coming" | -5 |
| **🟢 Inverted Hammer** | Small body at bottom, long upper wick, after downtrend | "First sign that buyers are trying to take control" | +6 |
| **🔴 Hanging Man** | Hammer shape but after uptrend | "Warning — the uptrend may be exhausting" | -5 |
| **⚪ Spinning Top** | Small body, equal wicks both ways | "The market is confused. Wait for clarity." | 0 |
| **🟢 Bullish Belt Hold** | Opens at the very LOW, strong close near HIGH | "A forceful bullish open — buyers grabbed control from the start" | +5 |
| **🔴 Bearish Belt Hold** | Opens at the very HIGH, closes near LOW | "A forceful bearish open — sellers took charge immediately" | -5 |

##### 🕯️🕯️ Combined Candle Formations (2-3 candles = a story)

| Pattern | What Happens | The Story | Score |
|---------|-------------|-----------|-------|
| **🟢 Bullish Engulfing** | Small red candle → BIG green candle that swallows it | "The bears made a small push. Then the bulls came in like a TIDAL WAVE and erased everything. Bulls are in control now." | +10 |
| **🔴 Bearish Engulfing** | Small green → BIG red swallows it | "Bulls tried. Bears CRUSHED them. Trend reversal incoming." | -10 |
| **🟢 Morning Star** | Big red → tiny candle → Big green (3 bars) | "A dark night (red), then a small star appears (tiny body = indecision at bottom), then the sun RISES (big green). The dawn of a new uptrend." | +12 |
| **🔴 Evening Star** | Big green → tiny → Big red | "A glorious day, then a star at the peak, then DARKNESS falls. The uptrend is dying." | -12 |
| **🟢 Three White Soldiers** | 3 big green candles in a row, each higher | "Three warriors march uphill in formation. Strong, relentless buying pressure." | +12 |
| **🔴 Three Black Crows** | 3 big red candles in a row, each lower | "Three crows circle overhead. Death is coming for the bulls." | -12 |
| **🟢 Piercing Line** | Red candle → green candle that closes above 50% of the red | "The bears stabbed down, but bulls PIERCED through their defense halfway back up" | +10 |
| **🔴 Dark Cloud Cover** | Green → red that closes below 50% of the green | "A dark cloud rolls over the bullish sky. Trouble ahead." | -10 |
| **🟢 Tweezer Bottom** | Two candles with the SAME low, opposite colors | "The floor was tested TWICE and held both times. Strong support!" | +8 |
| **🔴 Tweezer Top** | Two candles with the SAME high, opposite colors | "The ceiling was tested twice and rejected both times. Can't break through!" | -8 |
| **🟢 Three Inside Up** | Bearish → Bullish Harami → Close higher | "Bear attacks, gets trapped inside, then bulls finish with a killing blow" | +10 |
| **🔴 Three Inside Down** | Bullish → Bearish Harami → Close lower | "Bull gets trapped, bears escape and take over" | -10 |
| **🟢 Bullish Abandoned Baby** | Red → Doji gaps DOWN → Green gaps UP | "The bears abandoned their baby in the wilderness. Bulls rescued it. EXTREMELY rare = EXTREMELY strong signal" | +15 |
| **🔴 Bearish Abandoned Baby** | Green → Doji gaps UP → Red gaps DOWN | "Bulls abandoned their position at the top. Panic selling follows." | -15 |

> **For a total beginner:** Think of candlestick patterns like reading animal tracks in the snow. Each track tells a story — were the animals running, resting, or fighting? Certain combinations of tracks tell you EXACTLY what happened and what's likely to happen next.

#### 📐 Chart Patterns (The Terrain Itself)

These are bigger structures formed over many candles — like looking at the shape of a mountain range.

| Pattern | What It Looks Like | The Story | Score |
|---------|-------------------|-----------|-------|
| **🟢 Double Bottom** | Price drops, bounces, drops again to SAME level, bounces again | "The ground was tested twice and it's SOLID. This is the floor. Price goes UP from here." | +10 |
| **🔴 Double Top** | Price rises, drops, rises to SAME level, drops again | "The ceiling was hit twice. Can't break through. Price goes DOWN." | -10 |
| **🟢 Bullish Flag** | Strong up-move → tight sideways pause | "The army advanced, paused to rest, about to charge AGAIN upward" | +8 |
| **🔴 Bearish Flag** | Strong down-move → tight sideways pause | "The avalanche paused briefly. More falling to come." | -8 |
| **🔴 Head & Shoulders** | Three peaks — middle one highest | "The three-headed dragon. Left shoulder, HEAD, right shoulder. When the neckline breaks — CRASH." | -12 |
| **🟢 Inverse Head & Shoulders** | Three troughs — middle one lowest | "The Phoenix. Three dives, the middle one deepest. When neckline breaks — LIFTOFF." | +12 |
| **🟢 Ascending Triangle** | Same highs + rising lows | "Buyers keep pushing higher, resistance will break. BULLISH." | +8 |
| **🔴 Descending Triangle** | Same lows + falling highs | "Sellers keep pushing lower, support will crack. BEARISH." | -8 |
| **🟢 Triangle Breakout UP** | Was coiling, now breaks above | "The spring was compressed. It just LAUNCHED upward!" | +12 |
| **🔴 Triangle Breakout DOWN** | Was coiling, now breaks below | "The floor gave way. Freefall!" | -12 |

#### 📏 Fibonacci Levels (The Wolf's Sacred Geometry)

Fibonacci retracement is based on natural mathematical ratios (0.618 is the "Golden Ratio" — it appears everywhere in nature). When price moves up and then pulls back, it tends to find support at these specific levels:

```
Swing High: $73,000  (the peak)
    │
    │  Fib 0.236: $71,638    ← Shallow pullback (barely resting)
    │  Fib 0.382: $70,468    ← Moderate pullback (healthy correction)
    │  Fib 0.500: $69,500    ← Half-way (balanced)
    │  Fib 0.618: $68,532    ← 🏆 GOLDEN RATIO (strongest support!)
    │  Fib 0.786: $67,302    ← Deep pullback (last chance for bulls)
    │
Swing Low:  $66,000  (the valley)
```

> **For a total beginner:** Imagine price is a rubber ball. When you throw it up (rally), it comes back down (pullback). Fibonacci tells us WHERE the ball is most likely to bounce. The 0.618 level (Golden Ratio) is like a trampoline — price bounces from there more than anywhere else.

The bot calculates these levels on both weekly and daily charts and logs `◄━━ 🐺 PREY IS HERE` when price is sitting right on a Fibonacci level.

#### 🟢🔴 Support & Resistance (Floors and Ceilings)

- **Support** = price levels where BTC has bounced UP multiple times (like a floor)
- **Resistance** = price levels where BTC has been rejected DOWN multiple times (like a ceiling)

The bot automatically detects these from swing highs/lows over 4 months.

### The Final Verdict — Daily Bias

After all this analysis, the wolf makes ONE decision for the entire day:

| Verdict | What It Means | Strategy |
|---------|--------------|----------|
| **🟢 BULLISH** | Weekly AND Daily agree: UP | Only buy CALL options (profit when BTC rises) |
| **🔴 BEARISH** | Weekly AND Daily agree: DOWN | Only buy PUT options (profit when BTC falls) |
| **🟡 CHOPPY** | Weekly and Daily DISAGREE | HEDGE MODE — buy both call AND put to profit from volatility |

This verdict is **LOCKED for 24 hours**. No changing mid-day.

---

## 🌲 LAYER 2: Check the Forest (Higher TF Confirmation)

Every 2 minutes, the bot checks three medium-term timeframes:

| Timeframe | Purpose |
|-----------|---------|
| **4-hour** | Intraday macro direction |
| **1-hour** | Swing momentum |
| **15-minute** | Near-term flow |

**Rule:** At least 2 of 3 must agree with the daily bias.

If the daily bias is BULLISH but the 4h and 1h are BEARISH → **No trade.** The wolf doesn't attack when the terrain is unfavorable.

---

## 🎯 LAYER 3: Spot the Prey (5-Minute Entry Trigger)

This is where the actual entry signal comes from. The bot watches the **5-minute chart** and runs:

1. **RSI** — Is price oversold (buy zone) or overbought (sell zone)?
2. **EMA 9 vs 21** — Is the fast average above or below the slow?
3. **MACD** — Did a bullish or bearish crossover just happen?
4. **Momentum** — Is the 5-bar trend up or down?
5. **ALL 27+ candlestick patterns** — Same as the macro analysis
6. **ALL chart patterns** — Double top/bottom, triangles, flags, H&S

Everything adds up to a **score from 0 to 100**.

**Score ≥ 70 = ATTACK!** Score below 70 = wait.

> **For a total beginner:** It's like a checklist for a surgeon before operating. Every item must check out. If even one thing is wrong — we don't cut.

---

## 🏹 LAYER 4: Attack! (Option Selection + Execution)

### Step 1: Fetch the Wallet
```
Wallet balance: $200
```

### Step 2: Full Wallet × 50x Leverage
```
$200 × 50 = $10,000 notional exposure
(You control $10,000 worth of options with just $200)
```

### Step 3: Pick the Best Option (Greeks Analysis)

The bot fetches all available options and scores each one by its "Greeks" — think of these as the prey's vital stats:

| Greek | Hunter Name | What It Tells Us | What We Want |
|-------|------------|------------------|-------------|
| **Delta (Δ)** | ⚡ Speed | How fast the option moves when BTC moves $1 | 0.30-0.50 (sweet spot) |
| **Gamma (Γ)** | 🎯 Reflexes | How fast Delta itself changes (acceleration) | High = explosive gains |
| **Theta (Θ)** | 🩸 Bleed | How much value the option loses PER DAY just from time passing | Low = less daily cost |
| **Vega (V)** | 💪 Vol Sensitivity | How much the option gains when volatility spikes | High = profits from chaos |
| **IV** | 🔥 Fear Level | How scared/excited the market is right now | Low = cheap entry |

The bot logs the top 3 candidates with their strengths and weaknesses, then picks the BEST one.

### Step 4: Place the Order
Market order → instant fill.

### Step 5: Trailing Stop Loss Activates

```
Entry: $1,000 (the option cost this much)
Initial Stop: $500 (50% below entry)

Price rises to $1,500 → Stop rises to $750 ✅
Price rises to $2,000 → Stop rises to $1,000 ✅ (breakeven!)
Price rises to $3,000 → Stop rises to $1,500 ✅ (locked +50% profit!)
Price drops to $1,500 → STOP HIT → EXIT at $1,500 (+50% profit!)
```

**The stop ONLY moves UP, never down.** This means:
- If price goes up, we ride it with no ceiling
- If price reverses, we still keep most of our gains
- If price tanks immediately, max loss is 50%

---

## 🟡 CHOPPY MODE — The Hedge

When the market is "choppy" (weekly says UP, daily says DOWN, or vice versa), the wolf doesn't sit idle. Instead, it plays BOTH sides:

1. Buy a **CALL option** (50% of wallet) — profits if BTC goes UP
2. Buy a **PUT option** (50% of wallet) — profits if BTC goes DOWN

**Why?** In choppy markets, big moves STILL happen — we just don't know which direction. By buying both, we profit from the MOVE itself, regardless of direction. If BTC swings 5%, one option loses but the other EXPLODES.

---

## 📋 DAILY RULES — The Wolf's Code

| Rule | Value | Why |
|------|-------|-----|
| Max trades per day | 1 (2 in hedge mode) | One blockbuster trade, not 20 mediocre ones |
| Max hold time | 8 hours | We're day traders, not investors |
| Min score for entry | 70/100 | Only the BEST signals |
| Trailing stop | 50% from peak | Ride winners, cut losers |
| Leverage | 50x | Amplify gains (and risk) |
| Position sizing | 100% of wallet | Full conviction |
| Cycle interval | 2 minutes | Fast enough for 5m candle trading |
| Bias refresh | Once per day (midnight UTC) | Macro picture doesn't change hourly |

---

## 📊 WHAT THE LOGS LOOK LIKE

When the bot starts, you'll see something like this:

```
╔══════════════════════════════════════════════════════════════════╗
║  🐺 THE WOLF SURVEYS THE LANDSCAPE — Deep Macro Reconnaissance  ║
║  Scanning months of terrain before choosing the hunting ground   ║
╚══════════════════════════════════════════════════════════════════╝

    ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    🗻 HIGH GROUND — WEEKLY RECONNAISSANCE (20 candles)
    ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    🟢 The HIGH GROUND favors the BULLS — prey moves UPHILL | Score: 72/100
    📡 TERRAIN SIGNALS:
        📉 RSI 42 — Prey weakening, buyers gathering (+10)
        🐂 EMA9 > EMA21 — Short-term bulls LEADING (+12)
        🏔️ EMA20 > EMA50 — Herd migrates UPHILL (+8)
        📊 MACD bullish — Momentum favors the HUNTER (+5)
        🏃 Momentum: 5-bar ↑+2.1% | 10-bar ↑+4.3%
    🕯️ SINGLE TRACKS (2 footprints):
        🟢 Hammer (+8) — ⚡ STRONG
        🟢 Bullish Marubozu (+8) — ⚡ STRONG
    🕯️🕯️ COMBINED TRACKS (1 formation):
        🟢 Bullish Engulfing (+10) — ⚡ STRONG
    📐 TERRAIN STRUCTURES (1 found):
        🚀 BREAKOUT: 🟢 Triangle Breakout UP (+12) — The prey BREAKS FREE!
    🟢 Pattern Verdict: +38 points from 4 formations
    📏 FIBONACCI MAP (Swing $62,400 → $73,200):
        Fib 0.236: $70,652
        Fib 0.382: $69,077
        Fib 0.500: $67,800 ⚖️ HALF
        Fib 0.618: $66,523 🏆 GOLDEN RATIO
        Fib 0.786: $64,922
    🟢 SUPPORT FLOORS: $64,800 | $66,200 | $67,500
    🔴 RESISTANCE CEILINGS: $71,500 | $73,200

    ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    🏷️  THE WOLF'S VERDICT — Today's Hunting Strategy
    ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    🟢🐂 THE TERRAIN FAVORS THE BULLS
    📋 Strategy: Hunt CALL options only. The herd moves uphill.
    🎯 Look for: Pullbacks to support / Fib 0.382-0.618 for entries
    💥 MACRO SIGNAL: 🟢 Triangle Breakout UP on WEEKLY — act accordingly!

    ⏰ This terrain map is LOCKED until midnight UTC.
    🐺 The wolf has surveyed. Now we wait for the perfect moment.
╔══════════════════════════════════════════════════════════════════╗
║  BIAS: BULLISH  | BTC: $68,715.50 | Refresh: midnight UTC      ║
╚══════════════════════════════════════════════════════════════════╝
```

---

## 🎬 REAL EXAMPLES — Watch the Wolf Hunt

### Example 1: 🟢 The Perfect Bullish Kill

> *April 7, 2026. BTC is at $83,500. The wolf wakes up.*

**LAYER 1 — Macro Survey (runs at midnight UTC):**

```
📅 WEEKLY (20 candles):
   RSI: 42 — "Prey weakened after a sell-off, buyers gathering" (+10)
   EMA9 > EMA21 — "Short-term bulls LEADING" (+12)
   EMA20 > EMA50 — "Herd migrating UPHILL for weeks" (+8)
   MACD: Bullish — "Momentum favors the hunter" (+5)
   Momentum: 5-bar ↑+3.2% — Uphill (+3)
   🕯️ Bullish Engulfing found (+10)
   📐 Ascending Triangle found (+8)
   Weekly Score: 50 + 10 + 12 + 8 + 5 + 3 + 10 + 8 = 106 → capped at 100
   ✅ WEEKLY = BULLISH

📅 DAILY (120 candles):
   RSI: 38 — "Prey exhausted from yesterday's dip" (+10)
   EMA9 > EMA21 — "Still bullish short-term" (+12)
   MACD: Bullish (+5)
   Momentum: 5-bar ↑+1.1% (+3)
   🕯️ Hammer found (+8), Morning Star found (+12)
   📏 Fib 0.618 = $82,900 — PRICE IS RIGHT THERE! 🏆
   Daily Score: 50 + 10 + 12 + 5 + 3 + 8 + 12 = 100
   ✅ DAILY = BULLISH

🏷️ VERDICT: Both WEEKLY and DAILY are BULLISH → TODAY'S BIAS = 🟢 BULLISH
📋 Rule: Only CALL options today. The prey runs uphill.
```

**LAYER 2 — Higher TF Confirmation (runs every cycle):**

```
4h:  RSI 45, EMA9 > EMA21 → BULLISH ✅
1h:  RSI 51, EMA9 > EMA21 → BULLISH ✅
15m: RSI 55, EMA9 < EMA21 → NEUTRAL ❌

Result: 2/3 confirm BULLISH → ✅ PASSED (need 2)
```

**LAYER 3 — 5-Minute Entry (the moment of truth):**

```
5m Chart Analysis:
   RSI: 35 → "Prey momentarily oversold" (+12)
   EMA9 just crossed above EMA21 → "Bullish crossover!" (+10)
   MACD: Bullish cross happening! (+8)
   Momentum: 5-bar ↑+0.3% (+5)
   🕯️ Bullish Engulfing on 5m (+10)
   🕯️ Three Inside Up on 5m (+10)

TOTAL: 50 + 12 + 10 + 8 + 5 + 10 + 10 = 105 → capped at 100
Score: 100 ≥ 70 threshold → ✅ SIGNAL: BUY!
```

**LAYER 4 — The Strike:**

```
💰 Wallet: $200.00
⚙️ Leverage: 50x confirmed
💰 Notional: $200 × 50 = $10,000

Top option found: C-BTC-84000-100426  (Call, strike $84,000, expires Apr 10)
   Greeks: Δ=+0.42 (⚡ good speed) | Θ=-28 (🟢 low bleed) | IV=38% (cheap!)
   Ask price: $3.50 per contract

Qty: $10,000 / $3.50 = 2,857 contracts
Order placed: BUY 2,857x C-BTC-84000-100426 @ $3.50

Trailing Stop: $3.50 × 0.50 = $1.75

🏆 PREY CAPTURED. Now monitoring...
```

**What happens next:**

```
14:30 — BTC rises to $84,200 → Option now $5.20 → Peak! Stop → $2.60
14:45 — BTC rises to $85,000 → Option now $8.10 → New peak! Stop → $4.05
15:00 — BTC dips to $84,500 → Option at $6.50 → Still above $4.05 stop ✅
15:15 — BTC rises to $85,800 → Option now $11.40 → New peak! Stop → $5.70
15:30 — BTC dips to $84,800 → Option drops to $5.50 → HIT STOP at $5.70!

EXIT: Sold 2,857 contracts at $5.70
P&L: ($5.70 - $3.50) × 2,857 = $6,285.40 profit! 🎉
Return: +62.9% on premium, +3,142% on $200 wallet (leveraged)
```

> **The wolf turned $200 into $6,285 in 1 hour.** That's the "one kill a day" philosophy.

---

### Example 2: 🔴 The Bearish Hunt

> *BTC is at $90,000 after a massive rally. Weekly RSI is 75 (OVERHEATED).*

```
📅 WEEKLY: RSI 75 (overbought -18), EMA9 still > EMA21 (+12), MACD bearish cross (-5)
   🕯️ Evening Star detected (-12), 🔴 Shooting Star (-8)
   Score: 50 - 18 + 12 - 5 - 12 - 8 = 19 → BEARISH

📅 DAILY: RSI 68 (-10), EMA9 just crossed BELOW EMA21 (-12)
   🕯️ Bearish Engulfing (-10), 🔴 Dark Cloud Cover (-10)
   📐 Double Top at $91,200 detected (-10)
   Score: 50 - 10 - 12 - 10 - 10 - 10 = -2 → capped at 0 → BEARISH

🏷️ VERDICT: 🔴 BEARISH — Hunt PUT options only.
💥 MACRO SIGNAL: 🔴 Double Top on DAILY — the ceiling is confirmed!

Higher TFs confirm (4h bearish, 1h bearish) → ✅
5m chart: Bearish Marubozu + Three Black Crows → Score 85 → ✅

Execution:
   PUT option: P-BTC-89000-100426, Δ=-0.38, Ask=$4.20
   Qty: $10,000 / $4.20 = 2,380 contracts
   Stop: $2.10 (50% from entry)

BTC crashes from $90,000 → $86,500 in 3 hours
PUT option: $4.20 → $14.80 (peak)
Stop trails up: $7.40

Exit at $8.60 when price bounces
P&L: ($8.60 - $4.20) × 2,380 = $10,472 profit! 🎉
```

> **BTC dropped and the wolf profited from the fall.** PUT options gain value when BTC goes down.

---

### Example 3: 🟡 The Choppy Hedge

> *Weekly says BULLISH (score 62), but Daily says BEARISH (score 38). They disagree!*

```
🏷️ VERDICT: 🟡 CHOPPY — Weekly and Daily fight each other.
📋 Strategy: HEDGE MODE — buying BOTH a call and a put.

The wolf splits its wallet in half:

LEG 1: CALL option (50% wallet = $100 × 50x = $5,000)
   C-BTC-84000-100426 @ $3.00 → 1,666 contracts

LEG 2: PUT option (50% wallet = $100 × 50x = $5,000)
   P-BTC-83000-100426 @ $2.80 → 1,785 contracts
```

**Scenario A: BTC drops $2,000**
```
CALL: $3.00 → $0.80 (loses $3,663)
PUT:  $2.80 → $9.50 (gains $11,959)
NET:  +$8,296 profit! The PUT more than covers the CALL loss.
```

**Scenario B: BTC rises $2,000**
```
CALL: $3.00 → $8.20 (gains $8,663)
PUT:  $2.80 → $0.60 (loses $3,927)
NET:  +$4,736 profit! The CALL explosion covers the PUT loss.
```

**Scenario C: BTC goes sideways (worst case)**
```
CALL: $3.00 → $2.50 (loses $832)
PUT:  $2.80 → $2.30 (loses $892)
NET:  -$1,724 loss. Both legs bleed from theta decay.
```

> **The hedge wins when BTC makes a BIG move in either direction.** It only loses when nothing happens (sideways). Since crypto rarely sits still, hedges work well on choppy days.

---

### Example 4: 🚫 The Wolf Waits (No Trade)

> *Not every day is a hunting day. Here's what "standing down" looks like.*

```
═══════════ 🔄 HUNT CYCLE — 14:32:00 UTC ══════════
👃 Running 4-layer analysis...

📊 HIGHER TF CONFIRMATION:
    4h: 🟢 BULLISH | RSI: 55 | Score: 67
   1h: ⚪ NEUTRAL | RSI: 50 | Score: 52
   15m: 🔴 BEARISH | RSI: 62 | Score: 38
📐 Confirmation: 1/3 align with BULLISH bias

⚠️ Higher TFs don't confirm daily bias — standing down.
😴 Criteria not met — standing down.
💤 Resting 120s before next scan...
```

**Why no trade?** Layer 2 failed. Only 1 of 3 higher TFs agreed with the bullish bias. The wolf needs at least 2. So it waits.

**Another "no trade" example — weak score:**

```
📊 HIGHER TF CONFIRMATION:
    4h: 🟢 BULLISH ✅
    1h: 🟢 BULLISH ✅
   15m: 🟢 BULLISH ✅
📐 Confirmation: 3/3 ✅

📊 ENTRY ANALYSIS (5m base):
   RSI: 52 (+0)
   EMA9 > EMA21 (+10)
   MACD: no cross (+0)
   Momentum: barely up (+5)
   No patterns found (+0)

TOTAL: 50 + 0 + 10 + 0 + 5 + 0 = 65
Score: 65 < 70 threshold → ❌ NOT ENOUGH

😴 Criteria not met — standing down.
```

**Why no trade?** Layer 3 scored only 65. We need 70. The signal exists but it's not strong enough. The wolf doesn't eat scraps — it waits for a feast.

> **The wolf passes on 80% of opportunities.** That's what makes the 20% it takes so powerful. Patience is the predator's greatest weapon.

---

### Example 5: 📏 Fibonacci in Action

> *How the wolf uses Fibonacci to time entries perfectly.*

```
BTC rallied from $78,000 (swing low) to $86,000 (swing high)
Now BTC is pulling back. WHERE will it bounce?

Fibonacci Map:
   Fib 0.236: $84,112  ← "Just a scratch — too shallow"
   Fib 0.382: $82,944  ← "Healthy pullback zone"
   Fib 0.500: $82,000  ← "Half-way — decisive moment"
   Fib 0.618: $81,056  ← 🏆 "GOLDEN RATIO — strongest bounce!"
   Fib 0.786: $79,712  ← "Deep pullback — last stand before reversal"

BTC drops to $81,100... right at the 0.618 Golden Ratio!
The bot logs: "⚡ PRICE AT FIB 0.618 — Critical decision zone!"

At this moment, if the 5m chart shows a Hammer or Bullish Engulfing
→ Score jumps to 78 → ATTACK! Buy a CALL right at the golden bounce.

Result: BTC bounces from $81,056 back to $84,500 → +4.2% move
The CALL option explodes: $2.50 → $9.80 → +292% gain! 🎉
```

> **Fibonacci works because millions of traders are watching the same levels.** When BTC hits 0.618, everyone expects a bounce — and the collective action MAKES it bounce. The wolf knows where the herd gathers.

---

## ⚠️ RISKS — The Wolf Can Still Bleed

| Risk | What It Means | Mitigation |
|------|--------------|------------|
| **50% max loss per trade** | Trailing stop at 50% means you can lose half your premium | This is aggressive by design — one big win covers multiple losses |
| **50x leverage** | Small moves are amplified 50× (2% move = 100% P&L) | Options have natural leverage already; 50x is the exchange margin |
| **Options expire worthless** | If BTC doesn't move enough by expiry, premium = $0 | Max 8-hour hold prevents sitting on dying options |
| **Theta decay** | Every passing hour, your option loses a little value | We trade near ATM (high delta) to minimize theta impact |
| **API downtime** | If the exchange goes down, we can't close positions | 8-hour max hold + trailing stop provide safety nets |

---

## 🔧 HOW TO DEPLOY

```bash
# On your VPS (Ubuntu)
cd ~/delta-trade
git pull origin main
rm -f bot_data/positions.json    # Clear old positions
sudo systemctl restart trading-bot
journalctl -u trading-bot -f     # Watch the wolf hunt
```

### .env Configuration
```env
DELTA_API_KEY=your_key_here
DELTA_API_SECRET=your_secret_here
DELTA_BASE_URL=https://cdn-ind.testnet.deltaex.org
PAPER_TRADE=false
LEVERAGE=50
CAPITAL=200
BASE_UNDERLYING=BTC
```

---

## 📁 FILE MAP

| File | What It Does |
|------|-------------|
| `options_bot.py` | The wolf itself — all logic, all decisions |
| `.env` | Secret keys and config settings |
| `blueprint1.md` | This document — the wolf's playbook |
| `bot_data/positions.json` | Currently active trades |
| `bot_data/trade_history.json` | Record of all past hunts |

---

*The wolf doesn't chase every rabbit. It waits for the elk. 🐺*
