# 📈 Hyperliquid LLM-Powered Quantitative Trading Bot

A high-performance, modular, and containerized quantitative algorithmic trading bot engineered for the **Hyperliquid L1 Perpetual DEX**.

This system implements a hybrid strategy combining **Statistical Arbitrage (Pairs Trading)** with **Dynamic NLP Sentiment Analysis** scraped from global crypto news RSS feeds, executing with strict **risk constraints** and manageable through a **stunning, Apple-inspired Next.js glassmorphic dashboard**.

---

## ⚡ System Core Features

### 1. High-Performance Market Ingestion & Execution
- **WebSocket Order Book Tracker:** Reconstructs the L2 order book in memory via a dedicated background thread directly connected to Hyperliquid L1, providing zero-lag mid-prices and spreads.
- **Python Execution Core:** Built on top of the official `hyperliquid` python library, enabling sub-millisecond cryptographic order signing and sub-millisecond execution.
- **Agent Wallet Protocol:** Utilizes dedicated API Agent keys allowing the bot to trade autonomously without giving it withdrawal/deposit rights.

### 2. Algorithmic Trading Strategy (Z-Score Pairs Trading)
- **Mean Reversion Spread Model:** Computes a rolling standard deviation and simple moving average of spreads between cointegrated assets (BTC/ETH).
- **Z-Score Signals:** Automatically issues entry signals when spread deviation is overstretched ($|Z| > 2.0$) and closes positions when it reverts ($|Z| < 0.5$).
- **Dynamic NLP Skewing:** Integrates a local, free sentiment analyzer (`NLTK VADER` / Keyword analysis) and integrates **Gemini API (Free Tier)**. If the market narrative is highly bullish/bearish, the bot dynamically skews the Z-score entry threshold to enter narrative-backed positions early!

### 3. Institutional Risk Management
- **Fixed Fractional Sizing:** Calculates size dynamically to limit exposure to exactly 1% of total equity per trade.
- **Max Margin Ceiling:** Prevents entering new positions if total margin allocated exceeds 20% of account balance.
- **Daily Drawdown circuit breaker:** Automatically halts trading, cancels all active orders, and freezes operations if the account balance falls by -5% within a single day.
- **Latency filter:** Rejects execution signals older than 100ms to avoid executing stale prices.

---

## 🛠️ Technology Stack
- **Backend:** Python 3.10, FastAPI, Websockets, NumPy, Pandas, NLTK
- **Frontend Dashboard:** Next.js 14, React 18, Tailwind CSS, Lucide Icons
- **Database:** SQLite (lightweight, zero-setup, WSL2-friendly persistent data)
- **Sandbox/Orchestration:** Docker, Docker Compose

---

## 🚀 Setup & Launch (WSL2 / Local Linux)

### Step 1: Clone or Copy this Directory
Ensure all files are inside a directory in your WSL2/Linux terminal.

### Step 2: Configure Environment Variables
Copy `.env.example` to `.env` and fill in your settings:
```bash
cp .env.example .env
```

If you leave `AGENT_PRIVATE_KEY` empty, **the bot will run in 100% simulated, risk-free dry-run mode** utilizing real-time live price streams!

To trade with real collateral on Hyperliquid Testnet or Mainnet:
1. Navigate to the **[Hyperliquid API Settings](https://app.hyperliquid.xyz/API)**.
2. Authorize an **Agent Wallet address** (you can generate a secondary private key on MetaMask/Rabby for this).
3. Set your `AGENT_PRIVATE_KEY` inside `.env`.
4. (Optional) Put your `ACCOUNT_ADDRESS` (your main wallet address) inside `.env` if using Agent delegation.
5. Set `IS_MAINNET=True` if you are ready to trade with real USDC.

### Step 3: Spin Up with Docker Compose
Run the following command inside your WSL2/Linux terminal:
```bash
docker compose up --build -d
```

### Step 4: Access your Visual Quant Dashboard
Open your browser and navigate to:
- **Dashboard UI:** [http://localhost:3001](http://localhost:3001)
- **FastAPI Documentation:** [http://localhost:8000/docs](http://localhost:8000/docs)

---

## 🎛️ Dashboard Control Center
The premium glassmorphic user interface provides:
- **Net Asset Value Metrics:** Visualizes account value, active margin ratios, and perpetual sizes.
- **Z-Score Meter:** Dynamic progress bar showing real-time spread stretch and exit thresholds.
- **LLM News Sentiments:** Real-time table displaying scraped RSS headlines, parsed NLP sentiment score, and summary logs.
- **Recent Trades Grid:** Real-time log showing executed bids and asks.
- **System Operation Terminal Logs:** Direct streaming of the bot's background logs (e.g. websocket state changes, signal analysis, order responses).
- **Global Kill Switch:** A big neon red **EMERGENCY STOP** button to manually freeze all trading operations instantly and cancel all open order books.
