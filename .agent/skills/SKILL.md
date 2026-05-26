# 🧠 Quantitative Algorithmic Trading Bot Skill Reference

This document catalogs advanced technical learnings, dependency constraints, and architectural patterns developed during the creation of the Hyperliquid LLM-Powered Trading Engine.

---

## 📈 Quantitative System Patterns

### 1. Delta-Sync Order Book Reconstruction
In high-frequency decentralized exchanges like Hyperliquid, reading raw L2 state through continuous REST pooling is a fatal anti-pattern. Instead, systems must implement local orderbook trackers that maintain a persistent WebSocket stream:
- **Operation:**
  1. Initialize connection to `wss://api.hyperliquid.xyz/ws`.
  2. Subscribe to the `l2Book` channel.
  3. Parse raw bid/ask prices and sizes into an in-memory dictionary.
  4. Perform mathematical mid-price and spread calculations locally.
- **V8 Engine/JavaScript Map Optimizations:**
  If using TypeScript/Node.js, V8 engines are highly optimized for direct hash-map operations (`Map<number, number>`). Modifying map properties runs in $O(1)$ constant time, outperforming large arrays or static objects, preventing memory allocation spikes and Garbage Collection pauses that introduce execution lag.

### 2. Z-Score Cointegration Spread Model
For statistical arbitrage pairs trading (e.g., BTC/ETH):
- **Formula:**
  $$ Spread = Price_A - (Hedge\_Ratio \times Price_B) $$
  $$ Z-Score = \frac{Spread_t - Mean(Spread_{[t-k, t]})}{StdDev(Spread_{[t-k, t]})} $$
- **Thresholds:**
  - $|Z| > 2.0$ represents a statistical anomaly indicating that the spread has widened significantly beyond historical bounds. This triggers an entry (short the overvalued asset, long the undervalued asset).
  - $|Z| < 0.5$ indicates the spread has reverted back to its historical mean, triggering an exit/flat.

### 3. Dynamic Narrative Skewing (LLM-Sentiment Edge)
Traditional quantitative models are purely mathematical and ignore fundamental macro shifts or media-driven momentum. By introducing an LLM NLP Scraper:
- Sentiment values are scored dynamically between $-1.0$ (very bearish) and $+1.0$ (very bullish).
- If sentiment is highly bullish ($Score > 0.3$), the Z-score entry threshold for Long positions is dynamically lowered (e.g., $-1.5$ instead of $-2.0$), allowing the bot to enter narrative-aligned trades early. Simultaneously, the Short threshold is raised ($2.5$ instead of $2.0$), preventing the bot from fighting bullish momentum!

---

## 🔒 Security Principles: API Delegation & Withdrawal Isolation
When running bots on public Web3 servers or clouds, **never store primary keys that hold direct custody of assets**.
- **The Agent Delegation Pattern:**
  Hyperliquid L1 allows you to register an authorized EVM address as an "Agent Wallet". This Agent has the cryptographic right to open and close positions, but **cannot withdraw, transfer, or deposit funds**.
- **Implementation:**
  Store the Agent's private key in the bot's environment. If the server is ever compromised, the attacker can only trigger trade executions but cannot steal your collateral by transferring it out.

---

## 🚦 Latency Circuit Breakers
WSL2 networks and host-VPS distance introduce physical network latency. Stale orders must never hit the L1 book:
- **Calculation:**
  Before passing any signal to the execution queue, the engine checks `Date.now() - signal.timestamp`.
- **Constraint:**
  If latency exceeds `100ms`, the signal is immediately dropped. This prevents slippage losses caused by executing old pricing matrices in highly volatile markets.

---

## 🛠️ Python SDK & Dependency Shifts (2026-05-26 Update)
During local environment synchronization and automated test validation inside the WSL2 Sandbox, critical Python SDK boundaries were identified and corrected:
- **PyPI Package Collision (`hyperliquid` vs `hyperliquid-python-sdk`):**
  While PyPI lists a package named `hyperliquid`, its latest version is locked at `0.4.66` and lacks key submodules (e.g. `hyperliquid.info`). The official, actively maintained SDK containing the correct `Info`, `Exchange`, and WebSocket protocols is **`hyperliquid-python-sdk`** (version `0.23.0+`).
- **Cryptographic Wallet Signing (`eth-account`):**
  Programmatic transaction signing for Hyperliquid Agent Wallets relies directly on `eth-account` (version `0.8.0+`). This is a separate required dependency that must be explicitly compiled in the environment.
- **SDK Constants Attribute Mapping:**
  The `hyperliquid.utils.constants` module defines environment urls using `MAINNET_API_URL` and `TESTNET_API_URL` rather than base `MAINNET`/`TESTNET` properties. Use `Config.API_URL` directly as the target parameter for flexible sandbox testnet routing.
