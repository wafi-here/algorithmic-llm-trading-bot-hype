# Architecture Decision Record (ADR) - 2026-05-26 - Algorithmic LLM Trading Bot

## Title: Implementation of a Low-Latency Modular Algorithmic Trading Bot on Hyperliquid with LLM Sentiment Integration

### Status: Proposed / Active

### Context:
The user wants to capitalize on algorithmic trading in cryptocurrency markets, specifically targeting **Hyperliquid L1 Perpetual DEX**. Hyperliquid features sub-millisecond execution latency, gas-free trading, and deep orderbooks.
We need to design a system that captures:
1. Market Neutral (Arbitrage, Funding Rate) or Volatility Breakout/Mean Reversion strategy signals.
2. Real-time data feed ingestion via WebSockets.
3. Advanced local Z-score calculations.
4. An optional LLM News Sentiment Scraper using free and robust tools (e.g., local FinBERT or free Gemini API).
5. Strong Web3 API Agent key authentication.
6. A responsive and aesthetically premium Next.js dashboard, all configured for easy Docker-based execution in WSL2.

---

### Proposed System Architecture (PlantUML Representation)

```
@startuml
skinparam backgroundColor #1A1C23
skinparam roundCorner 10
skinparam Handwritten false

skinparam actor {
    BackgroundColor #00E6FF
    BorderColor #00E6FF
}

skinparam node {
    BackgroundColor #2A2E3D
    BorderColor #444B6E
    FontColor #FFFFFF
}

skinparam database {
    BackgroundColor #232731
    BorderColor #444B6E
    FontColor #FFFFFF
}

node "Hyperliquid L1 DEX" as HL #FF007F {
  [WebSocket Price Tick Feed] as HL_WS
  [REST Execution Endpoint] as HL_REST
}

node "Local WSL2 / Docker Sandbox" {
  
  node "Python Backend & Engine" as BE #4F8A10 {
    [WebSocket Client & Orderbook Tracker] as OBT
    [Strategy Engine (Z-Score & Sentiment)] as SE
    [Risk Gatekeeper & Position Sizer] as RM
    [Kriptografi Execution Engine] as EE
    [LLM Sentiment Parser (FinBERT/Gemini)] as SP
    [FastAPI Web Server] as FA
  }
  
  node "Next.js Web UI Client" as FE #1D4ED8 {
    [Premium Glassmorphic UI Dashboard] as UI
    [WebSocket / REST API Broker] as UIB
  }
  
  database "Persistence Layer" as DB #FF5722 {
    [SQLite State Database] as SQLDB
    [Redis Hot Memory State] as REDIS
  }
}

actor "User" as USR

' Data & Control Flow Connections
HL_WS --> OBT : High-Speed Price Stream
SP --> SE : Sentiment Score (-1.0 to 1.0)
OBT --> SE : Real-time Orderbook Ticks
SE --> RM : Sinyal Trading (Long/Short)
RM --> EE : Approved Sinyals Only
EE --> HL_REST : ECC Signed Order Tx
SQLDB <--> FA : Read/Write Logs & Balance
FA <--> UIB : REST/WSS Data Exchange
UIB <--> UI : Live Render
UI --> USR : Display Dashboard
USR --> UI : Emergency Kill Switch Action
UI --> FA : Forward Emergency Stop Signal
FA --> RM : Trigger Drawdown Freeze
@enduml
```

---

### Decisions & Rationale:

#### 1. Language Stack: Python for the Core Engine
- **Why**: Python is the absolute gold standard for data science, quantitative mathematical computing (`numpy`, `pandas`), and machine learning (FinBERT, PyTorch). The official `hyperliquid-python-sdk` is actively maintained and provides robust type checking and cryptographic signature helper functions.
- **Cost**: 100% Free.

#### 2. Local Sentiment Parsing with FinBERT and Gemini API
- **Why**: Sentiment analysis usually relies on expensive external APIs (e.g., Bloomberg, LunarCrush, or paid OpenAI endpoints). We bypass this by utilizing:
  - **FinBERT**: A free, open-source transformer model pre-trained on financial data that runs locally on the CPU within Docker.
  - **Gemini API**: Generous free-tier API that performs advanced summarization of long narrative news articles if local resources are constrained.
- **Cost**: 100% Free.

#### 3. Database Layer: SQLite + Optional Redis
- **Why**: SQLite is serverless, fast, zero-configuration, and files are stored inside the local directory. This is perfect for WSL2 local persistence and makes Docker builds extremely light. For ultra-fast caching of active prices, internal memory variables are used (saving the cost and overhead of installing external Redis unless required).
- **Cost**: 100% Free.

#### 4. Web Framework: Next.js + Tailwind for the Dashboard
- **Why**: Next.js provides premium performance, fast routing, and seamless layout rendering. When combined with glassmorphism Tailwind utilities, we can achieve high-fidelity aesthetics matching institutional trading platforms.
- **Cost**: 100% Free.

---

### Consequences:
- **Pros**:
  - Extremely cheap/free maintenance.
  - Ready to be containerized and run inside Windows WSL2 or uploaded straight to a cheap VPS (e.g., Hetzner or DigitalOcean).
  - Secure: Uses Agent keys that restrict withdrawal rights.
- **Cons**:
  - Requires setting up Python dependencies (handled automatically by Dockerfile).
  - High-frequency execution is bound by local internet speed (mitigated during production by VPS colocation).

### Verification:
- Run strategy simulations inside a local PyTest suite.
- Test cryptographic signing functions against the Hyperliquid Testnet endpoint.
