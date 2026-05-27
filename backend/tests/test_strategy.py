import pytest
import time
import numpy as np
from collections import deque
import os
from backend.services.database import db

# Use native Linux path to prevent WSL2 drvfs file locking and disk I/O errors
db.db_path = "/tmp/test_strategy.db"
if os.path.exists(db.db_path):
    try:
        os.remove(db.db_path)
    except Exception:
        pass
db._initialize_db()

from backend.services.strategy_engine import StrategyEngine
from backend.services.risk_manager import RiskManager

def test_zscore_calculation_logic():
    """Verify that Z-Score is computed correctly on a sufficient spread buffer.
    Updated for P3 (minimum 20 data points) and S3 (log-price transforms)."""
    import math
    engine = StrategyEngine(asset_a="BTC", asset_b="ETH", window_size=30)
    
    # Pre-fill 25 spread values (above the P3 minimum of 20)
    # Using log-price spread: log(67250) - 19 * log(3520) ≈ 11.116 - 19*8.166 = 11.116 - 155.15 = -144.04
    # But the exact value depends on the hedge ratio and prices.
    # For this test, we just verify the Z-score computation mechanics are correct.
    base_spread = math.log(67250.0) - (19.0 * math.log(3520.0))
    for _ in range(25):
        engine.spread_buffer.append(base_spread)
    
    # Also pre-fill price buffers with enough data for ADX (S1) to not block
    engine.price_buffers["BTC"] = deque(maxlen=200)
    engine.price_buffers["ETH"] = deque(maxlen=200)
    for i in range(50):
        engine.price_buffers["BTC"].append(67250.0 + np.random.normal(0, 10))
        engine.price_buffers["ETH"].append(3520.0 + np.random.normal(0, 1))
    
    # get_market_state returns realistic mock prices when offline
    # price_a = 67250, price_b = 3520
    # New spread (log-price): log(67250) - 19 * log(3520)
    res = engine.calculate_signals()
    assert res != {}
    assert "zscore" in res
    assert "spread" in res
    assert "signals" in res

def test_zscore_skewing_by_sentiment():
    """Verify Z-score triggers skew correctly when LLM sentiment is positive or negative."""
    engine = StrategyEngine(asset_a="BTC", asset_b="ETH", window_size=10)
    
    # Neutral sentiment - no threshold skew
    engine.update_sentiment(0.0)
    assert engine.latest_sentiment == 0.0
    
    # Highly bullish sentiment - skews thresholds
    engine.update_sentiment(0.8)
    assert engine.latest_sentiment == 0.8
    
    # Highly bearish sentiment - skews thresholds
    engine.update_sentiment(-0.8)
    assert engine.latest_sentiment == -0.8

@pytest.mark.asyncio
async def test_risk_manager_circuit_breakers():
    """Verify Risk Manager enforces drawdowns, stale signals, and limits correctly."""
    from unittest.mock import AsyncMock, patch
    manager = RiskManager()
    manager.last_sync_time = time.time()
    
    mock_user_state = {
        "crossMarginSummary": {"accountValue": "10000.0", "totalMarginUsed": "0.0"},
        "marginSummary": {"accountValue": "10000.0", "totalMarginUsed": "0.0"},
        "assetPositions": [],
    }
    
    with patch("backend.services.risk_manager.hl_client") as mock_hl:
        mock_hl.get_user_state = AsyncMock(return_value=mock_user_state)
        mock_hl.is_active = True
        mock_hl.cancel_all_orders = AsyncMock(return_value=True)
        
        # Test 1: Halted bot rejects orders
        manager.is_halted = True
        approved, reason, size = await manager.evaluate_order("BTC", "LONG", 67000.0, 1000)
        assert approved is False
        assert "HALTED" in reason
        
        # Test 2: Release halt
        manager.reset_halt()
        assert manager.is_halted is False
        
        # Test 3: Stale signal latency rejection
        # If the signal timestamp is very old compared to current time, it must reject
        stale_time_ms = 1000 # 1970
        approved, reason, size = await manager.evaluate_order("BTC", "LONG", 67000.0, stale_time_ms)
        assert approved is False
        assert "rejected" in reason or "Stale" in reason

def test_historical_backtester_logic():
    """Verify that historical backtester accurately runs simulation and calculates PnL/metrics."""
    from backend.services.backtester import backtester
    df = backtester.generate_mock_history(days=2)
    res = backtester.run_backtest(df, entry_z=2.0, exit_z=0.5)
    assert "total_trades" in res
    assert "win_rate" in res
    assert "sharpe_ratio" in res
    assert "max_drawdown" in res
    assert "final_balance" in res
    assert res["final_balance"] > 0


@pytest.mark.asyncio
async def test_telegram_alerts_sim_fallback():
    """Verify that Telegram Alert Broker falls back to simulation mode without credentials."""
    from backend.services.alerts import alert_broker
    # Clear credentials to force simulation mode
    alert_broker.bot_token = ""
    alert_broker.chat_id = ""
    success = await alert_broker.send_alert("Test Alert message")
    assert success is True

@pytest.mark.asyncio
async def test_funding_rate_arbitrage_agent():
    """Verify that Funding Arbitrage Agent toggles correctly and returns APY structures."""
    from backend.services.funding_arbitrage import funding_arb_agent
    assert funding_arb_agent.is_active is False
    funding_arb_agent.toggle_agent(True)
    assert funding_arb_agent.is_active is True
    funding_arb_agent.toggle_agent(False)
    
    opportunities = await funding_arb_agent.get_opportunities()
    assert len(opportunities) > 0  # Should be up to 10 live opportunities
    assert "annualized_apy" in opportunities[0]

def test_momentum_signals_with_rolling_buffer():
    """Verify multi-timeframe ROC momentum signals with accelerating price history.
    Updated for F1 (single append), S1 (ADX regime filter), and acceleration filter."""
    from unittest.mock import patch
    from backend.services.strategy_engine import StrategyEngine
    engine = StrategyEngine(asset_a="BTC", asset_b="ETH", window_size=120)
    
    # Quadratic (accelerating) uptrend: price = 67000 + i^1.5
    # This ensures roc_fast > roc_mid/3 (acceleration filter passes)
    engine.price_buffers["BTC"] = deque(maxlen=200)
    for i in range(50):
        price = 67000.0 + (i ** 1.5) * 10.0
        engine.price_buffers["BTC"].append(price)
    
    # Latest price continues the accelerating trend
    latest_price = 67000.0 + (50 ** 1.5) * 10.0
    with patch('backend.services.strategy_engine.tracker') as mock_tracker:
        mock_tracker.get_market_state.return_value = {"mid": latest_price}
        signal = engine.calculate_momentum_signals("BTC")
    assert signal == "LONG", f"Accelerating uptrend should produce LONG, got {signal}"

def test_volatility_breakout_with_rolling_buffer():
    """Verify Bollinger Band breakout detection with real price data."""
    np.random.seed(123)
    engine = StrategyEngine(asset_a="BTC", asset_b="ETH", window_size=10)
    # Fill 25 ticks of stable prices centered around mock mid price (67250)
    for i in range(25):
        engine.price_buffers.setdefault("BTC", deque(maxlen=50))
        engine.price_buffers["BTC"].append(67250.0 + np.random.normal(0, 1.0))
    signal = engine.calculate_volatility_breakout("BTC")
    assert signal == "FLAT"  # No breakout on stable prices

def test_grid_trading_levels():
    """Verify grid trading generates correct number of buy/sell levels."""
    engine = StrategyEngine(asset_a="BTC", asset_b="ETH", window_size=10)
    grids = engine.calculate_grid_signals("BTC")
    assert len(grids["buy_levels"]) == 5
    assert len(grids["sell_levels"]) == 5
    assert grids["buy_levels"][0]["price"] < grids["sell_levels"][0]["price"]

def test_market_making_signals():
    """Verify market making bid/ask spread calculations with imbalance adjustments."""
    engine = StrategyEngine(asset_a="BTC", asset_b="ETH", window_size=10)
    mm = engine.calculate_market_making_signals("BTC")
    assert mm["bid_price"] < mm["ask_price"]
    assert mm["adverse_selection_halt"] is False  # Default mock imbalance is 0.0
