import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    ENV = os.getenv("ENV", "development")
    
    # Hyperliquid Config
    # Defaulting to Testnet for safety. Swap to Mainnet by setting IS_MAINNET=True in .env
    IS_MAINNET = os.getenv("IS_MAINNET", "False").lower() in ("true", "1", "yes")
    
    if IS_MAINNET:
        API_URL = "https://api.hyperliquid.xyz"
        WS_URL = "wss://api.hyperliquid.xyz/ws"
    else:
        API_URL = "https://api.hyperliquid-testnet.xyz"
        WS_URL = "wss://api.hyperliquid-testnet.xyz/ws"
        
    AGENT_PRIVATE_KEY = os.getenv("AGENT_PRIVATE_KEY", "")
    ACCOUNT_ADDRESS = os.getenv("ACCOUNT_ADDRESS", "")
    
    # LLM Sentiment Config
    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
    
    # System Config
    PORT = int(os.getenv("PORT", 8000))
    DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./trading_bot.db")
    
    # Risk Config
    MAX_EXPOSURE_PCT = float(os.getenv("MAX_EXPOSURE_PCT", 0.20)) # Max 20% margin allocated
    RISK_PER_TRADE_PCT = float(os.getenv("RISK_PER_TRADE_PCT", 0.01)) # Risk 1% equity per trade
    CIRCUIT_BREAKER_LATENCY_MS = int(os.getenv("CIRCUIT_BREAKER_LATENCY_MS", 100)) # Sinyal stale after 100ms
    DAILY_DRAWDOWN_LIMIT_PCT = float(os.getenv("DAILY_DRAWDOWN_LIMIT_PCT", 0.05)) # Stop trading if -5% daily
    BOT_CYCLE_INTERVAL_SECONDS = int(os.getenv("BOT_CYCLE_INTERVAL_SECONDS", 30))

