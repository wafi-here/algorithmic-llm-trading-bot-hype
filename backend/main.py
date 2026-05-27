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
from backend.services.execution_algos import execution_algos
from backend.services.universe_manager import universe_manager
from backend.services.trailing_stop import trailing_stop_manager
from backend.services.insight import insight_manager, Insight

# State holder to check bot execution status
BOT_STATUS = {
    "is_running": False,
    "last_cycle_time": 0.0,
    "cycles_executed": 0
}

background_tasks = set()

def create_background_task(coro):
    task = asyncio.create_task(coro)
    background_tasks.add(task)
    task.add_done_callback(background_tasks.discard)
    return task

async def trading_bot_loop():
    """Main trading bot execution loop running asynchronously in the background."""
    db.log_system("SYSTEM", "Starting main trading bot evaluation loop...")
    BOT_STATUS["is_running"] = True
    
    # Run initial news scraping sentiment update
    try:
        sentiment_data = await sentiment_engine.scrape_and_analyze()
        avg_sentiment = sentiment_data.get("score", 0.0)
        avg_confidence = sentiment_data.get("confidence", 0.5)
        strategy_engine.update_sentiment(avg_sentiment, avg_confidence)
    except Exception as e:
        db.log_system("WARNING", f"Initial sentiment scrape failed: {str(e)}")
        
    # Initial Universe Scan
    try:
        top_coins = await universe_manager.update_universe(max_coins=10)
        tracker.update_target_coins(top_coins)
        strategy_engine.sync_with_universe(top_coins)
    except Exception as e:
        db.log_system("ERROR", f"Initial universe scan failed: {str(e)}")
        
    last_sentiment_scrape = time.time()
    last_db_prune = time.time()
    last_universe_scan = time.time()
    
    while BOT_STATUS["is_running"]:
        try:
            now = time.time()
            BOT_STATUS["last_cycle_time"] = now
            BOT_STATUS["cycles_executed"] += 1
            
            # 1. Scrape news every 10 minutes (600 seconds)
            if now - last_sentiment_scrape > 600:
                # Trigger in background so it doesn't block fast tick execution
                create_background_task(update_sentiment_async())
                last_sentiment_scrape = now

            # 2. Prune database logs every hour (3600 seconds) to ensure storage efficiency
            if now - last_db_prune > 3600:
                db.prune_stale_data(days_threshold=2)
                last_db_prune = now

            # 3. Dynamic Universe Rescan every hour (3600 seconds)
            if now - last_universe_scan > 3600:
                top_coins = await universe_manager.update_universe(max_coins=10)
                tracker.update_target_coins(top_coins)
                strategy_engine.sync_with_universe(top_coins)
                last_universe_scan = now

            # ============================================================
            # P1: Fetch user_state ONCE at the top of each cycle.
            # This eliminates 50-70% of redundant get_user_state() API calls
            # that were previously scattered across risk_manager, margin checks,
            # and grid/MM threshold checks.
            # ============================================================
            cycle_user_state = await hl_client.get_user_state()
            
            # S2: Determine account tier once for signal architecture decisions
            is_micro_account = True
            account_value = 0.0
            if cycle_user_state:
                cross_val = float(cycle_user_state.get("crossMarginSummary", {}).get("accountValue", "0.0"))
                isolated_val = float(cycle_user_state.get("marginSummary", {}).get("accountValue", "0.0"))
                account_value = max(cross_val, isolated_val)
                if account_value >= 50.0:
                    is_micro_account = False
                elif cycle_user_state.get("is_mock", False):
                    is_micro_account = False
            
            # ============================================================
            # STEP 1.5: TRAILING STOP CHECK (Lean-inspired, runs FIRST)
            # Risk management ALWAYS takes priority over alpha signals.
            # This checks every tracked position for stop/TP/expiry triggers.
            # ============================================================
            # Risk management ALWAYS takes priority over alpha signals.
            # This checks every tracked position for stop/TP/expiry triggers.
            # ============================================================
            current_prices = {}
            
            # Ensure we fetch prices for ALL currently open/tracked positions
            for coin in trailing_stop_manager.positions.keys():
                state = tracker.get_market_state(coin)
                mid = state.get("mid", 0.0)
                if mid > 0:
                    current_prices[coin] = mid
                    
            # Also fetch for the active universe
            for coin in universe_manager.get_active_universe():
                if coin not in current_prices:
                    state = tracker.get_market_state(coin)
                    mid = state.get("mid", 0.0)
                    if mid > 0:
                        current_prices[coin] = mid
            
            # Update trailing stop peak tracking
            trailing_stop_manager.update_prices(current_prices)
            
            # Check for exit triggers
            exit_signals = trailing_stop_manager.check_exits(current_prices)
            for exit_sig in exit_signals:
                exit_coin = exit_sig["coin"]
                exit_price = exit_sig["current_price"]
                exit_size = exit_sig["size"]
                exit_reason = exit_sig["reason"]
                
                db.log_system("EXIT_EXECUTION",
                    f"Auto-exit {exit_coin} | Reason: {exit_reason} | "
                    f"PnL: {exit_sig['pnl_pct']}% | Peak: {exit_sig['peak_pnl_pct']}%"
                )
                
                # Execute the exit order
                is_buy = exit_sig["side"] == "SHORT"  # Close SHORT = buy, close LONG = sell
                slippage = execution_algos.calculate_dynamic_slippage(exit_coin, exit_size, is_buy)
                exec_price = execution_algos.compute_exec_price(exit_price, is_buy, slippage)
                
                await hl_client.place_order(
                    coin=exit_coin,
                    is_buy=is_buy,
                    size=exit_size,
                    price=exec_price,
                    reduce_only=True
                )
                
                # Record the trade and untrack
                pnl_usd = exit_sig["pnl_pct"] / 100.0 * exit_sig["entry_price"] * exit_size
                db.record_trade(exit_coin, "SELL" if is_buy else "BUY", exit_size, exec_price, pnl=pnl_usd, cloid=f"EXIT_{exit_reason}")
                trailing_stop_manager.unregister_position(exit_coin)
                insight_manager.clear_coin(exit_coin)

            # STEP 1.6: Expire stale insights
            expired_count = insight_manager.expire_stale()

            # 2. Run strategy signal check
            # Calculates Z-scores and generates trade signals (LONG/SHORT/FLAT)
            strat_result = strategy_engine.calculate_signals()
            
            # Generate consensus signals from multiple strategies
            zscore_signals = {}
            if strat_result and "signals" in strat_result:
                zscore_signals = strat_result["signals"]
            
            # Collect coins to evaluate: primary pair + all universe coins
            coins_to_evaluate = set()
            coins_to_evaluate.add(strategy_engine.asset_a)
            coins_to_evaluate.add(strategy_engine.asset_b)
            for coin in universe_manager.get_active_universe():
                coins_to_evaluate.add(coin)
            coins_to_evaluate.discard(None)
            
            # Emit insights to the manager
            # S2: For micro accounts (<$50), only emit Momentum + OBI signals.
            # Z-score and Breakout are suppressed because:
            # (a) Z-score pairs trading can't be hedged at this capital level
            # (b) Breakout conflicts with momentum signals, weakening consensus
            # This produces clearer, higher-conviction directional signals.
            for coin in coins_to_evaluate:
                mom_sig = strategy_engine.calculate_momentum_signals(coin)
                obi_sig = strategy_engine.calculate_orderbook_imbalance_signal(coin)
                
                # Always emit momentum and OBI (universally useful)
                if mom_sig:
                    insight_manager.emit(Insight(coin, mom_sig, confidence=0.7, magnitude=0.01, period_seconds=300, source="Momentum"))
                if obi_sig:
                    insight_manager.emit(Insight(coin, obi_sig, confidence=0.8, magnitude=0.005, period_seconds=60, source="OBI"))
                
                # S2: Only emit Z-score and Breakout for accounts >= $50
                if not is_micro_account:
                    brk_sig = strategy_engine.calculate_volatility_breakout(coin)
                    zs_sig = zscore_signals.get(coin)
                    
                    if brk_sig and brk_sig != "FLAT":
                        insight_manager.emit(Insight(coin, brk_sig, confidence=0.6, magnitude=0.02, period_seconds=300, source="Breakout"))
                    if zs_sig:
                        insight_manager.emit(Insight(coin, zs_sig, confidence=0.9, magnitude=0.015, period_seconds=600, source="Z-Score"))
                else:
                    # For micro accounts, still emit FLAT Z-score signals for exit handling
                    zs_sig = zscore_signals.get(coin)
                    if zs_sig and zs_sig == "FLAT":
                        insight_manager.emit(Insight(coin, zs_sig, confidence=0.9, magnitude=0.015, period_seconds=600, source="Z-Score"))

            # Retrieve processed consensus from InsightManager
            ranked_insights = insight_manager.get_ranked_signals(coins_to_evaluate)
            flat_signals = insight_manager.get_flat_signals(coins_to_evaluate)
            
            # Exit signals (FLAT) take priority over entries, so put them first
            consensus_list = flat_signals + ranked_insights
            
            # Map InsightConsensus to the downstream format
            ranked_signals = []
            for consensus in consensus_list:
                ranked_signals.append({
                    "coin": consensus.coin,
                    "action": consensus.direction,
                    "strength": consensus.strength,
                    "sources": consensus.sources,
                    "confidence": consensus.confidence
                })
            
            # Process ranked signals with margin pre-check
            executed_any = False
            margin_failures = 0
            
            # Pre-fetch positions once (not per-coin) to avoid API spam
            active_positions = await hl_client.get_positions()
            
            for signal in ranked_signals:
                coin = signal["coin"]
                action = signal["action"]
                strength = signal["strength"]
                sources = signal["sources"]
                
                # Fetch latest market state for pricing
                market_state = tracker.get_market_state(coin)
                mid_px = market_state.get("mid", 0.0)
                
                if mid_px == 0.0:
                    continue
                    
                timestamp_ms = int(time.time() * 1000)
                
                # Evaluate side vs current active positions to prevent double entries but allow reversals
                active_pos = next((p for p in active_positions if p["coin"] == coin), None)
                
                # Prevent entering identical position twice
                if active_pos:
                    if action == active_pos["side"]:
                        continue # Already have this exact position
                    # If we reach here, we have a position but the signal is the OPPOSITE side (reversal)
                elif action == "FLAT":
                    continue # No position to close

                # Quick margin feasibility pre-check for entry orders (skip for exits)
                # P1: Pass cycle_user_state to avoid redundant API call
                if action != "FLAT":
                    feasible, feasibility_reason = await risk_manager.check_margin_feasibility(coin, mid_px, user_state=cycle_user_state)
                    if not feasible:
                        margin_failures += 1
                        # Only log margin skip once every 5 cycles to avoid log spam
                        if BOT_STATUS["cycles_executed"] % 5 == 1:
                            db.log_system("MARGIN_SKIP", f"Pre-check skip {coin} ({action}, str={strength}): {feasibility_reason}")
                        continue

                # Full Risk Gatekeeper Evaluation
                # P1: Pass cycle_user_state to avoid redundant API call
                approved, reason, size = await risk_manager.evaluate_order(
                    coin=coin,
                    side=action,
                    price=mid_px,
                    timestamp_ms=timestamp_ms,
                    confidence=signal.get("confidence", 1.0),
                    user_state=cycle_user_state
                )
                
                if approved:
                    db.log_system("RISK", f"Order APPROVED for {coin} ({action}). Size: {size} | Signal: {'+'.join(sources)} (str={strength:.2f})")
                    
                    # L1 Execution with dynamic slippage (Lean VolumeShareSlippageModel)
                    if action == "FLAT":
                        if active_pos:
                            is_buy = active_pos["side"] == "SHORT"
                            size = abs(float(active_pos.get("szi", size)))
                        else:
                            continue
                    else:
                        is_buy = action == "LONG"
                        
                    reduce_only = action == "FLAT"
                    
                    # Dynamic slippage replaces static 0.5% buffer
                    slippage = execution_algos.calculate_dynamic_slippage(coin, size, is_buy)
                    exec_price = execution_algos.compute_exec_price(mid_px, is_buy, slippage)
                    
                    # Execute trade
                    await hl_client.place_order(
                        coin=coin,
                        is_buy=is_buy,
                        size=size,
                        price=exec_price,
                        reduce_only=reduce_only
                    )
                    executed_any = True
                    
                    # Register with Trailing Stop Manager for exit protection
                    if action in ("LONG", "SHORT"):
                        trailing_stop_manager.register_position(
                            coin=coin,
                            side=action,
                            entry_price=mid_px,
                            size=size
                        )
                    elif action == "FLAT":
                        # Untrack on manual FLAT exit
                        trailing_stop_manager.unregister_position(coin)
                        insight_manager.clear_coin(coin)
                else:
                    # Log risk rejection details
                    if action != "FLAT":
                        db.log_system("RISK_REJECT", f"Order REJECTED for {coin} ({action}). Reason: {reason}")
            
            # Log summary if all entry signals failed margin check
            if not executed_any and margin_failures > 0 and len(ranked_signals) > 0:
                entry_signals = [s for s in ranked_signals if s["action"] != "FLAT"]
                if entry_signals and margin_failures >= len(entry_signals):
                    if BOT_STATUS["cycles_executed"] % 10 == 1:
                        db.log_system("MARGIN_EXHAUSTED", 
                            f"No executable opportunities this cycle. "
                            f"{margin_failures}/{len(entry_signals)} entry signals failed margin pre-check. "
                            f"Consider depositing more funds or closing existing positions."
                        )
            
            # 3. Active Market Making and Grid Trading on Top 2 Coins
            # (Bypassed for micro-accounts < $50 to protect request ratio limits)
            # P1: Reuse cycle_user_state — no redundant API call needed
            if is_micro_account:
                if BOT_STATUS["cycles_executed"] % 5 == 1:
                    db.log_system("SYSTEM", "Skipping High-Frequency Grid & MM for micro-account (< $50) to protect request-to-volume ratio limits.")
            else:
                top_2_coins = [strategy_engine.asset_a, strategy_engine.asset_b]
                for coin in top_2_coins:
                    if not coin:
                        continue
                    
                    # Fetch optimal sizes through Risk Manager or use dynamic minimum notional.
                    # Grid and MM layers use slices (e.g. 10%). To satisfy $12 minimum per slice, we set base notional to $120.
                    state = tracker.get_market_state(coin)
                    mid_px = state.get("mid", 0.0)
                    if mid_px > 0:
                        base_size = 120.0 / mid_px
                    else:
                        base_size = 1.0 if coin != "BTC" else 0.001 
                    
                    # Execute Market Making
                    mm_signals = strategy_engine.calculate_market_making_signals(coin)
                    create_background_task(execution_algos.execute_market_making(coin, mm_signals, base_size))
                    
                    # Execute Grid Trading
                    grid_signals = strategy_engine.calculate_grid_signals(coin)
                    create_background_task(execution_algos.execute_grid_trading(coin, grid_signals, base_size))
                        
            # 4. Run cyclical Funding Arbitrage evaluations
            await funding_arb_agent.run_arbitrage_checks()
            
            # Sleep between strategy iterations
            await asyncio.sleep(Config.BOT_CYCLE_INTERVAL_SECONDS)
            
        except asyncio.CancelledError:
            db.log_system("SYSTEM", "Trading bot loop task cancelled.")
            break
        except Exception as e:
            db.log_system("ERROR", f"Error in main bot loop: {str(e)}")
            await asyncio.sleep(Config.BOT_CYCLE_INTERVAL_SECONDS)

async def update_sentiment_async():
    """Helper to run news scraping in background without blocking the loop."""
    try:
        sentiment_data = await sentiment_engine.scrape_and_analyze()
        avg_sentiment = sentiment_data.get("score", 0.0)
        avg_confidence = sentiment_data.get("confidence", 0.5)
        strategy_engine.update_sentiment(avg_sentiment, avg_confidence)
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
    
    # Sync existing positions into Trailing Stop Manager
    try:
        active_positions = await hl_client.get_positions()
        for pos in active_positions:
            if not trailing_stop_manager.has_position(pos["coin"]):
                trailing_stop_manager.register_position(
                    coin=pos["coin"],
                    side=pos["side"],
                    entry_price=pos["entry_px"],
                    size=pos["size"]
                )
        db.log_system("SYSTEM", f"Synced {len(active_positions)} open positions to Trailing Stop Manager.")
    except Exception as e:
        db.log_system("ERROR", f"Failed to sync positions on startup: {str(e)}")
        
    # Start the background trading loop task
    bot_task = create_background_task(trading_bot_loop())
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
async def get_dashboard_metrics():
    """Aggregates active metrics for NextJS Dashboard."""
    # Fetch real-time balances and positions (single API call)
    user_state = await hl_client.get_user_state()
    positions = await hl_client.get_positions(user_state=user_state)  # Reuse user_state to avoid redundant call
    
    # Get latest zscores
    latest_zscores = db.get_latest_zscores(limit=30)
    
    # Get recent trades
    recent_trades = db.get_recent_trades(limit=10)
    
    # Get latest news sentiment logs
    sentiment_logs = db.get_latest_sentiment(limit=5)
    
    # Current active market prices with live/mock status
    btc_state = tracker.get_market_state("BTC")
    eth_state = tracker.get_market_state("ETH")
    doge_state = tracker.get_market_state("DOGE")
    sui_state = tracker.get_market_state("SUI")
    
    btc_price = btc_state.get("mid", 75500.0)
    eth_price = eth_state.get("mid", 2070.0)
    doge_price = doge_state.get("mid", 0.17)
    sui_price = sui_state.get("mid", 3.50)
    
    # Determine if market feeds are live (from real WebSocket) or mock fallback
    markets_live = not btc_state.get("is_mock", False)
    
    margin_summary = {}
    is_mock = True
    if user_state:
        is_mock = user_state.get("is_mock", False)
        # Use crossMarginSummary if it has a non-zero accountValue, otherwise isolated marginSummary
        cross_summary = user_state.get("crossMarginSummary", {})
        isolated_summary = user_state.get("marginSummary", {})
        
        cross_val = float(cross_summary.get("accountValue", 0.0))
        isolated_val = float(isolated_summary.get("accountValue", 0.0))
        
        if cross_val > 0 or isolated_val == 0:
            margin_summary = cross_summary
        else:
            margin_summary = isolated_summary
    elif user_state is None:
        is_mock = True
        
    return {
        "bot_running": BOT_STATUS["is_running"] and not risk_manager.is_halted,
        "is_halted": risk_manager.is_halted,
        "account_value": margin_summary.get("accountValue", "0.00"),
        "total_margin_used": margin_summary.get("totalMarginUsed", "0.00"),
        "withdrawable": margin_summary.get("withdrawable", "0.00"),
        "positions": positions,
        "btc_price": btc_price,
        "eth_price": eth_price,
        "doge_price": doge_price,
        "sui_price": sui_price,
        "markets_live": markets_live,
        "asset_a": strategy_engine.asset_a,
        "asset_b": strategy_engine.asset_b,
        "current_zscore": strategy_engine.current_zscore,
        "current_spread": strategy_engine.current_spread,
        "latest_sentiment": strategy_engine.latest_sentiment,
        "zscore_history": latest_zscores[::-1], # Return chronological
        "recent_trades": recent_trades,
        "sentiment_logs": sentiment_logs,
        "is_mock": is_mock
    }

@app.get("/api/logs")
def get_system_logs():
    """Retrieve raw engine operation logs."""
    return db.get_logs(limit=50)

@app.post("/api/emergency-control")
async def handle_emergency_control(request: HaltRequest):
    """Triggers manual Kill Switch or recovers the system state."""
    if request.action == "HALT":
        success = await risk_manager.trigger_emergency_kill()
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
    sentiment_data = await sentiment_engine.scrape_and_analyze()
    avg_sentiment = sentiment_data.get("score", 0.0)
    avg_confidence = sentiment_data.get("confidence", 0.5)
    strategy_engine.update_sentiment(avg_sentiment, avg_confidence)
    return {
        "status": "success",
        "message": f"Sentiment updated manually. Score: {avg_sentiment:.2f}, Confidence: {avg_confidence:.2f}",
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
async def get_funding_arbitrage_metrics():
    """Retrieve Cash-and-Carry funding rate arbitrage metrics."""
    return {
        "agent_active": funding_arb_agent.is_active,
        "opportunities": await funding_arb_agent.get_opportunities()
    }

@app.post("/api/funding-arbitrage/toggle")
def toggle_funding_arbitrage(request: FundingToggleRequest):
    """Enables/Disables the Cash-and-Carry dynamic execution broker."""
    success = funding_arb_agent.toggle_agent(request.enabled)
    return {"status": "success", "agent_active": funding_arb_agent.is_active}

@app.post("/api/backtest")
def run_historical_backtest(request: BacktestRequest):
    """Executes a pairs trading simulation run over simulated cointegrated price trails."""
    df_data = backtester.generate_mock_history(days=5, ticks_per_day=288)
    results = backtester.run_backtest(
        df_data,
        entry_z=request.entry_threshold,
        exit_z=request.exit_threshold,
        window=request.window_size
    )
    return results

class WalkForwardRequest(BaseModel):
    entry_threshold: float = 2.0
    exit_threshold: float = 0.5
    k_folds: int = 5
    days: int = 10

@app.post("/api/backtest/walk-forward")
def run_walk_forward_validation(request: WalkForwardRequest):
    """Walk-Forward Validation Backtest (Anti-Overfitting per López de Prado).
    Partitions data into K chronological folds and produces a distribution of
    Sharpe ratios to test strategy robustness across multiple market regimes."""
    results = backtester.run_walk_forward_backtest(
        entry_z=request.entry_threshold,
        exit_z=request.exit_threshold,
        k_folds=request.k_folds,
        days=request.days
    )
    return results

class MonteCarloRequest(BaseModel):
    entry_threshold: float = 2.0
    exit_threshold: float = 0.5
    n_paths: int = 50

@app.post("/api/backtest/monte-carlo")
def run_monte_carlo_simulation(request: MonteCarloRequest):
    """Monte Carlo Multi-Path Simulation. Generates multiple synthetic price paths
    and tests strategy performance across diverse market scenarios."""
    results = backtester.generate_monte_carlo_paths(
        n_paths=request.n_paths,
        entry_z=request.entry_threshold,
        exit_z=request.exit_threshold
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

class TwapRequest(BaseModel):
    coin: str
    is_buy: bool
    total_size: float
    duration_seconds: int = 30
    slices: int = 3

class VwapRequest(BaseModel):
    coin: str
    is_buy: bool
    total_size: float
    duration_seconds: int = 30
    slices: int = 3

class IcebergRequest(BaseModel):
    coin: str
    is_buy: bool
    total_size: float
    visible_size: float

@app.post("/api/execution/twap")
async def trigger_twap_execution(request: TwapRequest):
    """Asynchronously triggers the TWAP slicing algorithm."""
    create_background_task(execution_algos.execute_twap(
        coin=request.coin,
        is_buy=request.is_buy,
        total_size=request.total_size,
        duration_seconds=request.duration_seconds,
        slices=request.slices
    ))
    return {"status": "success", "message": "TWAP order thread dispatched."}

@app.post("/api/execution/vwap")
async def trigger_vwap_execution(request: VwapRequest):
    """Asynchronously triggers the VWAP weighted slicing algorithm."""
    create_background_task(execution_algos.execute_vwap(
        coin=request.coin,
        is_buy=request.is_buy,
        total_size=request.total_size,
        duration_seconds=request.duration_seconds,
        slices=request.slices
    ))
    return {"status": "success", "message": "VWAP order thread dispatched."}

@app.post("/api/execution/iceberg")
async def trigger_iceberg_execution(request: IcebergRequest):
    """Asynchronously triggers the Iceberg fractional slice algorithm."""
    create_background_task(execution_algos.execute_iceberg(
        coin=request.coin,
        is_buy=request.is_buy,
        total_size=request.total_size,
        visible_size=request.visible_size
    ))
    return {"status": "success", "message": "Iceberg order thread dispatched."}

@app.get("/api/strategies/evaluate-all")
def get_strategy_diagnostics(coin: str = "BTC"):
    """Evaluates all advanced strategy metrics (Momentum, Breakout, Grid, MM) for diagnostic monitoring."""
    momentum = strategy_engine.calculate_momentum_signals(coin)
    breakout = strategy_engine.calculate_volatility_breakout(coin)
    grid = strategy_engine.calculate_grid_signals(coin)
    market_making = strategy_engine.calculate_market_making_signals(coin)
    
    return {
        "coin": coin,
        "momentum_trend": momentum,
        "bollinger_breakout": breakout,
        "active_grid_levels": grid,
        "market_making_skew": market_making
    }

@app.get("/api/trailing-stops")
def get_trailing_stop_positions():
    """Returns all positions tracked by the Trailing Stop Manager with exit levels."""
    return {
        "tracked_positions": trailing_stop_manager.get_tracked_positions(),
        "total_tracked": len(trailing_stop_manager.positions)
    }

@app.get("/api/insights")
def get_active_insights():
    """Returns all active (non-expired) insights from the Insight Manager."""
    return {
        "active_insights": insight_manager.get_all_active(),
        "total_active": len(insight_manager.get_all_active())
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("backend.main:app", host="0.0.0.0", port=Config.PORT, reload=False)
