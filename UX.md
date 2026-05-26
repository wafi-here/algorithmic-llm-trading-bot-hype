# 📈 Hyperliquid LLM-Powered Quant-Desk: Visual UX Guide

Welcome to the **Visual UX Guide** for the Hyperliquid L1 perpetual trading system. This document provides an in-depth, professional walkthrough of the Apple-inspired glassmorphic dashboard—engineered to combine **High-Frequency Statistical Arbitrage (Z-Score Pairs Trading)** with **Dynamic NLP Sentiment Analysis**.

---

## 🎛️ Dashboard Architectural Overview

The Visual Quant Desk is partitioned into modular, high-fidelity widgets designed to provide real-time latency diagnostics, balance monitoring, algorithmic execution triggers, and deep mathematical strategy monitoring.

```
┌────────────────────────────────────────────────────────────────────────┐
│                        SYSTEM CONTROL HEADER                           │
│  [AUTHENTICATED]    [TESTNET/MAINNET]    [EMERGENCY STOP (KILL SWITCH)]│
├───────────────────────────────────┬────────────────────────────────────┤
│     PORTFOLIO COLLATERAL NET      │        REAL-TIME TICK FEEDS        │
│  • Margin Balance: $0.00          │  • BTC-PERP: $75,516.00            │
│  • Margin Used: $0.00             │  • ETH-PERP: $2,072.30             │
│  • Withdrawable: $10,000.00       │  • LLM Narrative Skew: -0.27       │
├───────────────────────────────────┴────────────────────────────────────┤
│                        Z-SCORE ARBITRAGE RADAR                         │
│  ◄ [LONG BTC / SHORT ETH]         [MEAN]         [SHORT BTC / LONG ETH] ►│
│  Current Z-Score: 0.182            Spread: $36,146.10                  │
├───────────────────────────────────┬────────────────────────────────────┤
│       ALTCOIN PAIRS SCANNER       │       FUNDING ARBITRAGE DESK       │
│  Dynamic Cointegration Rankings   │  Real-Time APY Yield Harvests      │
├───────────────────────────────────┼────────────────────────────────────┤
│    HISTORICAL BACKTEST CLIENT     │     ALGORITHMIC EXECUTION DESK     │
│  Interactive Strategy Simulator   │  TWAP / VWAP / Iceberg Slicers     │
├───────────────────────────────────┴────────────────────────────────────┤
│                     REAL-TIME OPERATIONS TERMINAL                      │
│  Live Background Engine, Risk gatekeeper logs, and WS Feed Health      │
└────────────────────────────────────────────────────────────────────────┘
```

---

## 🔍 Deep Feature Analysis

### 1. System Control Header & Authentication Status
* **L1 Authenticated Badge:** Real-time feedback of the cryptographic wallet signature status. It illuminates bright green when the bot has successfully established connection to Hyperliquid L1 L2 Book streams via MetaMask/Rabby API Agent delegation keys.
* **Manual Override - EMERGENCY STOP (KILL SWITCH):** A high-contrast, pulsating neon-red control button. When clicked, it dispatches an immediate `/api/emergency-control` `HALT` payload to the FastAPI risk engine.
  1. Instantly sets `is_halted = True` across the backend.
  2. Discards any in-flight entry signals.
  3. Triggers `hl_client.cancel_all_orders()` to sweep and cancel all open order books on Hyperliquid L1.
* **REACTIVATE BOT Button:** Flashes green to reset the risk engine and return the system to normal automated tracking once high-volatility market events normalize.

### 2. Collateral Net & Live Market Feeds
* **Portfolio Health Metrics:**
  * **Margin Balance:** Total collateral allocated to active perpetual position margin.
  * **Margin Used:** Live maintenance margin utilization.
  * **Withdrawable:** Cash reserves available for immediate vault/account withdrawals (defaults to a mock `$10,000.00` safety balance if running without a live private key).
* **High-Performance Market Tickers:** Live mid-prices for cointegrated pairs derived from the WebSocket L2 order book reconstructed in memory (e.g., **BTC-PERP** at `$75,516.00` and **ETH-PERP** at `$2,072.30`).

### 3. LLM News Sentiment Edge (NLP Skewing)
* **Live Sentiment Meter:** Displays the compound financial sentiment score calculated from global RSS crypto feeds (ranging between `-1.0` extremely bearish and `+1.0` extremely bullish).
* **Gemini LLaMA Integration / VADER Fallback:** Utilizes the free Gemini 2.5 Flash API to digest full article text. If rate limits are met or no key is provided, it seamlessly falls back to local `NLTK VADER` and rule-based lexicon matchings to prevent latency locks.
* **Dynamic Z-Score Skewing:** 
  * If sentiment is **highly bullish (> 0.3)**, the bot skews parameters (e.g., LONG threshold falls from `-2.0` to `-1.5` to capture narrative-backed uptrends early; SHORT threshold rises to `2.5` to prevent shorting into a rally).
  * If sentiment is **highly bearish (< -0.3)**, the reverse is applied (SHORT threshold falls to `1.5`, making shorts eager; LONG threshold moves to `-2.5`).

### 4. Z-Score Arbitrage Spread Radar
* **Rolling Spread Model:** Calculates spreads dynamically via the cointegration formula:
  $$\text{Spread} = \text{Price}_{\text{BTC}} - (\text{Hedge Ratio} \times \text{Price}_{\text{ETH}})$$
* **Visual Z-Score Meter:** An Apple-style glassmorphic indicator bar displaying how far the spread is currently stretched in terms of standard deviation from the moving average.
  * **Entry Signal Short:** Z-Score $> +2.0$ (Spread overstretched; SHORT BTC / LONG ETH).
  * **Entry Signal Long:** Z-Score $< -2.0$ (Spread compressed; LONG BTC / SHORT ETH).
  * **Exit Signal Flat:** $|Z| < 0.5$ (Spread reverted to mean; closes both positions to secure profit).

### 5. Altcoin Pairs Scanner (Advanced Diagnostics)
* **Cointegration Ranking:** Real-time matrix displaying alternative coin pairs tracked by the background `PairsScanner` thread (e.g., **SOL-SUI**, **SOL-NEAR**, **NEAR-SUI**).
* **Stability Index & Hedge Ratio:** Computes active correlation and statistical cointegration. Highly cointegrated pairs are highlighted with a green **"COINTEGRATED"** status badge, allowing you to easily swap trading pairs directly from the dashboard!

### 6. Funding Arbitrage Desk
* **Yield Harvesting:** Extracts "risk-free" yield utilizing cash-and-carry positions that take advantage of perpetual funding rates.
* **Annualized APY Calculator:** Aggregates live 8h funding rates directly from Hyperliquid's REST API and projects annualized returns (e.g., **SOL** APY at `36.42%`, **ETH** at `18.61%`). A toggle button allows you to authorize the automated arbitrage broker to execute delta-neutral hedge trades.

### 7. Historical Backtest Simulator
* **Form-Based Controls:** Allows you to set Z-Score entry/exit triggers and rolling window sizes, then execute historical backtests locally inside Next.js.
* **Quantitative Backtest Metrics:** Reports simulated trade performance parameters:
  * **Total Trades Executed** (e.g., `51` trades).
  * **Win Rate** and **Sharpe Ratio** (risk-adjusted return index).
  * **Max Drawdown %** (to measure historical risk exposure).
  * **Final Simulated Balance & Cumulative PnL**.

### 8. Algorithmic Execution Trigger Panel
Allows institutional order execution by slicing larger block orders to minimize slippage and eliminate order book footprints:
* **TWAP (Time-Weighted Average Price):** Splits a large trade into equal volume slices spaced over equal time intervals.
* **VWAP (Volume-Weighted Average Price):** Weights trade size slices dynamically using historical volume distribution weights.
* **Iceberg Orders:** Keeps most of the order size hidden by exposing only a small, visible slice (`visible_size`) to the public order book at any given time.

### 9. Strategy Diagnostics & Operations Terminal Logs
* **Diagnostics Grid:** Monitors alternative trade signals simultaneously:
  * **Momentum Trend:** Moving average crossovers (Fast SMA 5 vs. Slow SMA 20).
  * **Bollinger Breakouts:** Triggered when pricing penetrates extreme standard deviation channels.
  * **Grid Trading Levels:** Lists target limit grids computed relative to current mid-price.
  * **Market Making spreads:** Real-time bidding and asking quotes adjusted for book imbalance.
* **Engine Operations Console:** A direct live-feed from the FastAPI backend system database. This is a critical diagnostic tool displaying Websocket subscription states, strategy signal events, and risk rejection alerts (such as **`RISK_REJECT: Stale signal latency is Xms`** to indicate network delays).

---

## 🛡️ Risk Management & Safety Constraints

The dashboard provides immediate transparency into the bot's risk gatekeeper rules:
1. **Daily Drawdown Circuit Breaker:** Halts trading automatically if equity drops by $-5\%$ within a single day.
2. **Exposure Margin Ceiling:** Rejects new trade entries if the active margin ratio exceeds $20\%$ of total account equity.
3. **Latency Filter:** Rejects execution signals older than `100ms` to protect you from executing trades on stale prices during high network congestion.
