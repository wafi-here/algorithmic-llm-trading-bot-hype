# ⚙️ Hyperliquid LLM Trading Bot Setup & Operations Runbook

This document details the configuration, system operations, and deployment parameters for the Hyperliquid LLM-Powered Quantitative Trading Bot.

---

## 🏗️ Architectural Topology

The system operates as a containerized dual-service deployment:

```
                  ┌────────────────────────────────────────┐
                  │          Hyperliquid L1 DEX            │
                  └───────────┬────────────────┬───────────┘
                              │                ▲
               WebSocket Price│                │Cryptographic
                Ticks Stream  │                │Exchange REST Txs
                              ▼                │
                  ┌───────────┴────────────────┴───────────┐
                  │         Python Trading Engine          │
                  │   FastAPI Server + WebSocket Worker    │
                  └───────────┬────────────────▲───────────┘
                              │                │
            REST API & Live WS│                │Emergency HALT
               State Stream   │                │Control Post Requests
                              ▼                │
                  ┌───────────┴────────────────┴───────────┐
                  │        Next.js UI Dashboard            │
                  │       Glassmorphic Render Desk         │
                  └────────────────────────────────────────┘
```

### 1. Dashboard Expansion
The frontend Glassmorphic Render Desk has been expanded with new advanced panels:
- **Altcoin Pairs Scanner**: Real-time statistical tracking of correlation, hedge ratio, and cointegration status for multiple asset pairs.
- **Funding Arbitrage Spreads**: Visual card tracking 8h funding rates and annualized APY opportunities to extract risk-free yield.
- **Historical Backtest Simulator**: Form controls to test Z-score parameters against simulated price histories before live deployment.
- **Execution Algorithms**: Execution panels for Time-Weighted Average Price (TWAP), Volume-Weighted Average Price (VWAP), and Iceberg orders for advanced liquidity extraction.
- **Strategy Diagnostics**: Live monitoring of Momentum Trends, Bollinger Breakouts, Grid Trading levels, and Market Making bid/ask spreads.

---

## 🔒 Security Protocol: Agent Wallet Delegation

To ensure absolute security of your funds, we enforce Hyperliquid's **Agent Wallet Protocol**. This prevents the risk of compromising your primary Web3 private key.

### Setup Instructions:
1. Access the **[Hyperliquid API Settings page](https://app.hyperliquid.xyz/API)** with your main Web3 wallet (MetaMask/Rabby).
2. Click **"Approve Agent"**.
3. Generate a secondary, empty Web3 wallet (e.g., standard EVM private key) to serve as your **Agent Wallet**.
4. Input the Agent Wallet's public address into the Hyperliquid Approve Agent modal and sign the transaction.
   - *This grants the Agent Wallet the right to execute orders on behalf of your main account, but forbids it from making withdrawals or transfers.*
5. Copy the Agent Wallet's **Private Key** and assign it to the `AGENT_PRIVATE_KEY` variable in your `.env` file.
6. Copy your Main Wallet's **Public Address** and assign it to the `ACCOUNT_ADDRESS` variable in your `.env` file.

---

## 🐳 WSL2 & Docker Container Orchestration

Both backend and frontend services are completely containerized. The `docker-compose.yml` mounts a local persistent volume for SQLite history.

### 1. File Volume Layout
- **`trading_bot.db`**: Local SQLite database storing Z-score logs, scraped news sentiment values, executed trades, and backend system logs. Wiped containers do *not* lose this data as it resides in the Docker persistent volume storage.
- **Note on Connections**: The backend utilizes thread-local `threading.local()` connection pooling to prevent SQLite lock collisions during high-concurrency event loops.

### 2. Networking Topology
- **`backend`**: Runs on port `8000`. Exposes FastAPI swagger docs, REST statistics endpoints, and log buffers.
- **`frontend`**: Runs on port `3000`. Serves the Next.js visual dashboard, which polls price tick updates, trade states, and terminal logs from `http://localhost:8000`.

---

## 🚦 System Operation & Emergency Runbook

### 1. Manual Kill Switch (Emergency Stop)
If the Z-score spread behaves erratically or a black-swan market event occurs:
- Click the glowing red **"EMERGENCY STOP (KILL)"** button on the top right of the dashboard.
- This invokes a POST request to `/api/emergency-control` with payload `{"action": "HALT"}`.
- **Backend Reaction:**
  1. The risk engine instantly sets `is_halted = True`.
  2. Discards any outstanding background signals.
  3. Triggers `hl_client.cancel_all_orders()` which sweeps Hyperliquid API to cancel all pending limit order sheets.
  4. Shuts down active execution triggers.

### 2. Circuit Breaker Recovery
To reactivate the bot once market conditions normalize:
- Click the flashing green **"REACTIVATE BOT"** button on the dashboard.
- This POSTs `{"action": "RESET"}` to backend.
- The risk engine resets `is_halted = False`, and normal operations resume.

### 3. Log Diagnostics
If the system rejects trades:
- Review the **Engine Operation Terminal Logs** on the bottom of the dashboard.
- Common rejection triggers:
  - `DAILY DRAWDOWN LIMIT BREACHED`: Daily PnL fell past -5% of starting equity. Reset requires manual reboot or day-cycle rollover.
  - `Max exposure limit reached!`: Total allocated margin exceeds 20% of net equity.
  - `Signal rejected: Stale signal latency is Xms`: The local network or CPU lag exceeded the 100ms processing threshold. Optimize local connection or migrate to a low-latency VPS.
