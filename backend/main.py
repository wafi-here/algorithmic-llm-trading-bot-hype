import asyncio
import time
import threading
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from backend.config import Config
from backend.services.database import db
from backend.services.hyperliquid_client import hl_client
from backend.services.orderbook_tracker import tracker
from backend.services.llm_sentiment import sentiment_engine
from backend.services.strategy_engine import strategy_engine
from backend.services.risk_manager import risk_manager
from backend.services.alerts import alert_broker
from backend.services.backtester import backtester
from backend.services.funding_arbitrage import funding_arb_agent
from backend.services.pairs_scanner import scanner

# State holder to check bot execution status
BOT_STATUS = {
    "is_running": False,
    "last_cycle_time": 0.0,
    "cycles_executed": 0
}

background_tasks = set()

async def trading_bot_loop():
    """Main trading bot execution loop running asynchronously in the background."""
    db.log_system("SYSTEM", "Starting main trading bot evaluation loop...")
    BOT_STATUS["is_running"] = True
    
    # Run initial news scraping sentiment update
    try:
        avg_sentiment = await sentiment_engine.scrape_and_analyze()
        strategy_engine.update_sentiment(avg_sentiment)
    except Exception as e:
        db.log_system("WARNING", f"Initial sentiment scrape failed: {str(e)}")
        
    last_sentiment_scrape = time.time()
    last_db_prune = time.time()
    
    while BOT_STATUS["is_running"]:
        try:
            now = time.time()
            BOT_STATUS["last_cycle_time"] = now
            BOT_STATUS["cycles_executed"] += 1
            
            # 1. Scrape news every 10 minutes (600 seconds)
            if now - last_sentiment_scrape > 600:
                # Trigger in background so it doesn't block fast tick execution
                asyncio.create_task(update_sentiment_async())
                last_sentiment_scrape = now

            # 2. Prune database logs every hour (3600 seconds) to ensure storage efficiency
            if now - last_db_prune > 3600:
                db.prune_stale_data(days_threshold=2)
                last_db_prune = now


            # 2. Run strategy signal check
            # Calculates Z-scores and generates trade signals (LONG/SHORT/FLAT)
            strat_result = strategy_engine.calculate_signals()
            
            if strat_result and "signals" in strat_result:
                signals = strat_result["signals"]
                zscore = strat_result["zscore"]
                
                # Check signals for both assets in our pairs trade
                for coin, action in signals.items():
                    if not action:
                        continue
                        
                    # Fetch latest market state for pricing
                    market_state = tracker.get_market_state(coin)
                    mid_px = market_state.get("mid", 0.0)
                    
                    if mid_px == 0.0:
                        continue
                        
                    timestamp_ms = int(time.time() * 1000)
                    
                    # Evaluate side vs current active positions to prevent double entries
                    active_positions = hl_client.get_positions()
                    has_position = any(p["coin"] == coin for p in active_positions)
                    
                    # Prevent entering identical position twice
                    if action == "LONG" and has_position:
                        continue
                    if action == "SHORT" and has_position:
                        continue
                    if action == "FLAT" and not has_position:
                        continue

                    # 3. Risk Gatekeeper Evaluation
                    approved, reason, size = risk_manager.evaluate_order(
                        coin=coin,
                        side=action,
                        price=mid_px,
                        timestamp_ms=timestamp_ms
                    )
                    
                    if approved:
                        db.log_system("RISK", f"Order APPROVED for {coin} ({action}). Size: {size}")
                        
                        # 4. L1 Execution
                        is_buy = action == "LONG"
                        reduce_only = action == "FLAT"
                        
                        # Set slippage price (0.5% buffer)
                        exec_price = mid_px * 1.005 if is_buy else mid_px * 0.995
                        
                        # Execute trade
                        hl_client.place_order(
                            coin=coin,
                            is_buy=is_buy,
                            size=size,
                            price=exec_price,
                            reduce_only=reduce_only
                        )
                    else:
                        # Log risk rejection details
                        if action != "FLAT":
                            db.log_system("RISK_REJECT", f"Order REJECTED for {coin} ({action}). Reason: {reason}")
                            
            # 3. Run cyclical Funding Arbitrage evaluations
            funding_arb_agent.run_arbitrage_checks()
            
            # Sleep for 5 seconds between strategy iterations
            await asyncio.sleep(5)
            
        except asyncio.CancelledError:
            db.log_system("SYSTEM", "Trading bot loop task cancelled.")
            break
        except Exception as e:
            db.log_system("ERROR", f"Error in main bot loop: {str(e)}")
            await asyncio.sleep(5)

async def update_sentiment_async():
    """Helper to run news scraping in background without blocking the loop."""
    try:
        avg_sentiment = await sentiment_engine.scrape_and_analyze()
        strategy_engine.update_sentiment(avg_sentiment)
    except Exception as e:
        db.log_system("WARNING", f"Background news sentiment check failed: {str(e)}")

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup actions
    db.log_system("SYSTEM", "Initializing backend environment components...")
    
    # Start the real-time WebSocket Order Book Tracker thread
    tracker.start()
    
    # Start the altcoin cointegration pairs scanner service
    scanner.start()
    
    # Start the background trading loop task
    bot_task = asyncio.create_task(trading_bot_loop())
    background_tasks.add(bot_task)
    
    yield
    
    # Shutdown actions
    db.log_system("SYSTEM", "Shutting down backend environment...")
    BOT_STATUS["is_running"] = False
    tracker.stop()
    scanner.stop()
    for task in background_tasks:
        task.cancel()
    db.log_system("SYSTEM", "All background workers successfully stopped.")

# Instantiate FastAPI App
app = FastAPI(
    title="Hyperliquid LLM Trading Bot Backend",
    description="WSL2-Ready Algorithmic & NLP Sentiment Engine",
    version="1.0.0",
    lifespan=lifespan
)

# CORS configurations for NextJS Dashboard
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Request schema for manual override halts
class HaltRequest(BaseModel):
    action: str # "HALT" or "RESET"

@app.get("/")
def get_root():
    return {
        "status": "online",
        "bot_running": BOT_STATUS["is_running"],
        "is_mainnet": Config.IS_MAINNET,
        "authenticated": hl_client.is_active
    }

@app.get("/api/dashboard")
def get_dashboard_metrics():
    """Aggregates active metrics for NextJS Dashboard."""
    # Fetch real-time balances and positions
    user_state = hl_client.get_user_state()
    positions = hl_client.get_positions()
    
    # Get latest zscores
    latest_zscores = db.get_latest_zscores(limit=30)
    
    # Get recent trades
    recent_trades = db.get_recent_trades(limit=10)
    
    # Get latest news sentiment logs
    sentiment_logs = db.get_latest_sentiment(limit=5)
    
    # Current active market prices
    btc_price = tracker.get_market_state("BTC").get("mid", 67000.0)
    eth_price = tracker.get_market_state("ETH").get("mid", 3500.0)
    
    margin_summary = {}
    if user_state and "marginSummary" in user_state:
        margin_summary = user_state["marginSummary"]
        
    return {
        "bot_running": BOT_STATUS["is_running"] and not risk_manager.is_halted,
        "is_halted": risk_manager.is_halted,
        "account_value": margin_summary.get("accountValue", "10000.0"),
        "total_margin_used": margin_summary.get("totalMarginUsed", "0.0"),
        "withdrawable": margin_summary.get("withdrawable", "10000.0"),
        "positions": positions,
        "btc_price": btc_price,
        "eth_price": eth_price,
        "current_zscore": strategy_engine.current_zscore,
        "current_spread": strategy_engine.current_spread,
        "latest_sentiment": strategy_engine.latest_sentiment,
        "zscore_history": latest_zscores[::-1], # Return chronological
        "recent_trades": recent_trades,
        "sentiment_logs": sentiment_logs,
        "is_mock": user_state.get("is_mock", False) if user_state else True
    }

@app.get("/api/logs")
def get_system_logs():
    """Retrieve raw engine operation logs."""
    return db.get_logs(limit=50)

@app.post("/api/emergency-control")
def handle_emergency_control(request: HaltRequest):
    """Triggers manual Kill Switch or recovers the system state."""
    if request.action == "HALT":
        success = risk_manager.trigger_emergency_kill()
        if success:
            return {"status": "success", "message": "Bot halt triggered, open orders cancelled!"}
    elif request.action == "RESET":
        success = risk_manager.reset_halt()
        if success:
            return {"status": "success", "message": "Bot circuit breaker reset. Running normally."}
            
    raise HTTPException(status_code=400, detail="Invalid action request")

@app.post("/api/scrape-news")
async def trigger_manual_news_scrape():
    """Force manual sentiment scraper execution."""
    avg_sentiment = await sentiment_engine.scrape_and_analyze()
    strategy_engine.update_sentiment(avg_sentiment)
    return {
        "status": "success",
        "average_sentiment": avg_sentiment,
        "skewed_zscore_long": -1.5 if avg_sentiment > 0.3 else (-2.5 if avg_sentiment < -0.3 else -2.0)
    }

class BacktestRequest(BaseModel):
    window_size: int = 30
    entry_threshold: float = 2.0
    exit_threshold: float = 0.5

class TelegramConfig(BaseModel):
    bot_token: str
    chat_id: str

class FundingToggleRequest(BaseModel):
    enabled: bool

@app.get("/api/scanner/pairs")
def get_scanner_rankings():
    """Retrieve cointegrated altcoin correlation stats and rankings."""
    return scanner.get_rankings()

@app.get("/api/funding-arbitrage")
def get_funding_arbitrage_metrics():
    """Retrieve Cash-and-Carry funding rate arbitrage metrics."""
    return {
        "agent_active": funding_arb_agent.is_active,
        "opportunities": funding_arb_agent.get_opportunities()
    }

@app.post("/api/funding-arbitrage/toggle")
def toggle_funding_arbitrage(request: FundingToggleRequest):
    """Enables/Disables the Cash-and-Carry dynamic execution broker."""
    success = funding_arb_agent.toggle_agent(request.enabled)
    return {"status": "success", "agent_active": funding_arb_agent.is_active}

@app.post("/api/backtest")
def run_historical_backtest(request: BacktestRequest):
    """Executes a pairs trading simulation run over simulated cointegrated price trails."""
    results = backtester.run_backtest(
        window_size=request.window_size,
        entry_threshold=request.entry_threshold,
        exit_threshold=request.exit_threshold
    )
    return results

@app.post("/api/alerts/config")
async def configure_telegram_alerts(request: TelegramConfig):
    """Configures Telegram tokens and dispatches an initialization check message."""
    Config.TELEGRAM_BOT_TOKEN = request.bot_token
    Config.TELEGRAM_CHAT_ID = request.chat_id
    alert_broker.load_config()
    
    # Send quick confirmation alert
    success = await alert_broker.send_alert(
        "✅ *Connection Established!*\n"
        "Your Hyperliquid Algorithmic Trading Bot is now fully connected to this channel for L1 execution notifications."
    )
    
    return {"status": "success" if success else "error", "message": "Configuration updated successfully"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("backend.main:app", host="0.0.0.0", port=Config.PORT, reload=False)

