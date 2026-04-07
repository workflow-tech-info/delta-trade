# 🐺 DELTA OPTIONS BOT v5.0 — "One Kill a Day" Blueprint

> *One blockbuster trade per day. Full conviction. Full wallet. Full leverage.*

## 4-Layer Decision System

### LAYER 1: Daily Bias (1W + 1D candles)
Runs at startup + every 4 hours. Sets a global variable for the day:
- **BULLISH** → Only CALL options allowed
- **BEARISH** → Only PUT options allowed  
- **CHOPPY** → No trading at all (conflicting weekly vs daily)

Uses: EMA9 vs EMA21, RSI, last 5 candle direction on Weekly and Daily charts.

### LAYER 2: Higher TF Confirmation (4h, 1h, 15m)
Every cycle, checks if at least 2 of 3 higher timeframes agree with daily bias.
If they don't → no trade. Uses: EMA crossover, RSI, momentum.

### LAYER 3: 5m Entry Trigger (Base Timeframe)
The actual entry signal from the 5-minute chart. Must score ≥ 70/100.

**Indicators:** RSI(14), EMA(9 vs 21), MACD(12,26,9), 5-bar momentum

**27 Patterns Detected:**
| # | Pattern | Type | Score |
|---|---------|------|-------|
| 1 | Dragonfly Doji | Single | +5 |
| 2 | Gravestone Doji | Single | -5 |
| 3 | Long-Legged Doji | Single | 0 |
| 4 | Bullish Marubozu | Single | +8 |
| 5 | Bearish Marubozu | Single | -8 |
| 6 | Hammer | Single | +8 |
| 7 | Hanging Man | Single | -5 |
| 8 | Inverted Hammer | Single | +6 |
| 9 | Shooting Star | Single | -8 |
| 10 | Spinning Top | Single | 0 |
| 11 | Bullish Belt Hold | Single | +5 |
| 12 | Bearish Belt Hold | Single | -5 |
| 13 | Bullish Engulfing | 2-bar | +10 |
| 14 | Bearish Engulfing | 2-bar | -10 |
| 15 | Bullish Harami | 2-bar | +6 |
| 16 | Bearish Harami | 2-bar | -6 |
| 17 | Piercing Line | 2-bar | +10 |
| 18 | Dark Cloud Cover | 2-bar | -10 |
| 19 | Tweezer Bottom | 2-bar | +8 |
| 20 | Tweezer Top | 2-bar | -8 |
| 21 | Morning Star | 3-bar | +12 |
| 22 | Evening Star | 3-bar | -12 |
| 23 | Three White Soldiers | 3-bar | +12 |
| 24 | Three Black Crows | 3-bar | -12 |
| 25 | Three Inside Up | 3-bar | +10 |
| 26 | Three Inside Down | 3-bar | -10 |
| 27 | Bullish Abandoned Baby | 3-bar | +15 |
| 28 | Bearish Abandoned Baby | 3-bar | -15 |

**Chart Patterns:** Double Top/Bottom, Bullish/Bearish Flag, Head & Shoulders, Inverse H&S, Symmetrical Triangle

### LAYER 4: Full Wallet Execution
- Fetch wallet balance → 100% allocation
- Set 50x leverage via API
- Select best option by Greeks (Delta, Gamma, Theta, Vega, IV)
- Place market order
- Set trailing stop at 50% from peak

## Position Sizing
```
Wallet: $200
Leverage: 50x
Notional: $200 × 50 = $10,000
Quantity: $10,000 / option_ask_price
```

## Trailing Stop
- Starts at entry × 0.50
- Tracks highest price seen (peak)
- Stop = peak × 0.50 (only moves UP)
- No take-profit ceiling — ride the wave

## Daily Rules
- Max 1 trade per day (resets at UTC midnight)
- Max 8 hour hold time
- No trading on CHOPPY days
- Entry must match daily bias direction
- Score must be ≥ 70/100
