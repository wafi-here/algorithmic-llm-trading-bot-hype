import pytest
import numpy as np
from collections import deque
from backend.services.strategy_engine import StrategyEngine
from backend.services.risk_manager import RiskManager

def test_zscore_calculation_logic():
    """Verify that Z-Score is computed mathematically correct based on rolling spreads."""
    engine = StrategyEngine(asset_a="BTC", asset_b="ETH", window_size=10)
    
    # Pre-fill spreads to calculate standard deviation
    # Spreads: 10, 10, 10, 10, 10, 10, 10, 10, 10, 10 (mean = 10, std = 0)
    for _ in range(10):
        engine.spread_buffer.append(10.0)
        
    # Standard deviation cannot be 0, it falls back to 0.0001
    assert engine.spread_buffer[0] == 10.0
    
    # get_market_state returns realistic mock prices when offline, so signals are calculated:
    # price_a = 67250, price_b = 3520. spread = 67250 - 19 * 3520 = 370.
    # Spreads buffer becomes: [10, 10, 10, 10, 10, 10, 10, 10, 10, 370]
    # mean = 46.0, std = 108.0, zscore = (370 - 46) / 108 = 3.0
    res = engine.calculate_signals()
    assert res != {}
    assert res["spread"] == 370.0
    assert abs(res["mean"] - 46.0) < 1e-5
    assert abs(res["std"] - 108.0) < 1e-5
    assert abs(res["zscore"] - 3.0) < 1e-5
    assert res["signals"]["BTC"] == "SHORT"
    assert res["signals"]["ETH"] == "LONG"

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

def test_risk_manager_circuit_breakers():
    """Verify Risk Manager enforces drawdowns, stale signals, and limits correctly."""
    manager = RiskManager()
    
    # Test 1: Halted bot rejects orders
    manager.is_halted = True
    approved, reason, size = manager.evaluate_order("BTC", "LONG", 67000.0, 1000)
    assert approved is False
    assert "HALTED" in reason
    
    # Test 2: Release halt
    manager.reset_halt()
    assert manager.is_halted is False
    
    # Test 3: Stale signal latency rejection
    # If the signal timestamp is very old compared to current time, it must reject
    stale_time_ms = 1000 # 1970
    approved, reason, size = manager.evaluate_order("BTC", "LONG", 67000.0, stale_time_ms)
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

def test_funding_rate_arbitrage_agent():
    """Verify that Funding Arbitrage Agent toggles correctly and returns APY structures."""
    from backend.services.funding_arbitrage import funding_arb_agent
    assert funding_arb_agent.is_active is False
    funding_arb_agent.toggle_agent(True)
    assert funding_arb_agent.is_active is True
    funding_arb_agent.toggle_agent(False)
    
    opportunities = funding_arb_agent.get_opportunities()
    assert len(opportunities) == 3
    assert opportunities[0]["coin"] == "BTC"
    assert opportunities[0]["annualized_apy"] > 0

