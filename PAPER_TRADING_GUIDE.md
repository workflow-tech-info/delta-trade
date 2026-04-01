# 📝 Paper Trading Guide — Options Bot v2.0

## Quick Start (5 minutes)

### Step 1: Create a Delta Exchange Testnet Account
1. Go to **https://demo.delta.exchange** and sign up (free)
2. You'll get **fake USDT** to trade with — no real money needed
3. Go to **Account → API Keys** and create a new API key
4. Copy the **API Key** and **API Secret**

### Step 2: Configure the Bot
1. Copy `.env.example` to `.env`:
   ```
   copy .env.example .env
   ```
2. Edit `.env` and fill in your testnet credentials:
   ```
   DELTA_API_KEY=your_testnet_api_key
   DELTA_API_SECRET=your_testnet_api_secret
   DELTA_BASE_URL=https://cdn-ind.testnet.deltaex.org
   PAPER_TRADE=true
   CAPITAL=15000
   ```

### Step 3: Install Dependencies
```bash
pip install requests pandas numpy pytz python-dotenv
```

### Step 4: Run the Bot
```bash
python options_bot.py
```

The bot will:
- Fetch **live market data** from Delta's testnet
- Generate signals using **real RSI + EMA analysis**
- Log all decisions to `options_bot_log.txt`
- Save positions to `bot_data/positions.json` (survives restarts!)
- Track performance in `bot_data/performance_report.json`
- Send Telegram alerts (if configured)

---

## What Happens During Paper Trading

### Every 5 Minutes, the Bot:
1. **Checks open positions** for stop loss / take profit / expiry
2. **Auto-closes** positions held > 4 hours or near expiry
3. **Fetches live BTC spot price** from Delta API
4. **Computes signal** from 15min candle RSI + EMA crossover
5. **If score ≥ 75**: Selects optimal strike, finds best contract
6. **Places paper order** (logged but not sent to exchange)
7. **Saves everything** to disk

### Files Created:
| File | Purpose |
|------|---------|
| `bot_data/positions.json` | Active + closed positions (survives restart) |
| `bot_data/trade_history.json` | All closed trades with PnL |
| `bot_data/performance_report.json` | Win rate, profit factor, drawdown |
| `options_bot_log.txt` | Full execution log |

---

## Understanding the Output

```
📊 Signal: BUY | Score: 82 | Spot: $84,500
📈 Live IV: 45.30%
🎯 Opened: 3x C-BTC-85000-020426 @ $0.0234
📊 C-BTC-85000-020426 $0.0251 (+7.3%)
✅ Closed [TAKE_PROFIT] C-BTC-85000-020426 $0.0234→$0.0468 PnL: $0.0234×3=$0.07
📈 Trades: 5 | Win: 60% | PF: 1.85 | PnL: $+0.42
```

---

## Safety Rules (Built Into Code)

| Rule | Value |
|------|-------|
| Max risk per trade | 0.5% of capital |
| Daily loss limit | 3% → bot stops for the day |
| Max open positions | 2 |
| Max hold time | 4 hours |
| Auto-close before expiry | 30 minutes |
| Minimum signal score | 75/100 |
| No new trades after | 20:00 UTC |

---

## Going Live (When Ready)

> ⚠️ Only switch to live after **4+ weeks of consistent paper profits**

1. Create API keys at **https://www.delta.exchange** (real account)
2. Update `.env`:
   ```
   DELTA_API_KEY=your_live_api_key
   DELTA_API_SECRET=your_live_api_secret
   DELTA_BASE_URL=https://api.india.delta.exchange
   PAPER_TRADE=false
   CAPITAL=5000
   ```
3. Start with **small capital** ($100-500)
4. Monitor closely for the first few days

---

## Troubleshooting

| Issue | Fix |
|-------|-----|
| "No options chain data" | Check API URL; testnet may have limited options |
| "Score below minimum" | Market is quiet — bot is being cautious (good!) |
| "No contract near strike" | Widen search or check if testnet has that expiry |
| Bot crashes on start | Check `.env` file exists and has valid keys |
| Positions lost | Check `bot_data/` folder exists (auto-created) |
