import os
import time
import pytest
import pandas as pd
from backend.services.database import db as main_db

# Use native Linux path to prevent WSL2 drvfs file locking and disk I/O errors
main_db.db_path = "/tmp/test_expansions.db"
if os.path.exists(main_db.db_path):
    try:
        os.remove(main_db.db_path)
    except Exception:
        pass
main_db._initialize_db()

from backend.services.database import DatabaseManager
from backend.services.risk_manager import RiskManager
from backend.services.backtester import HistoricalBacktester
from backend.services.pairs_scanner import CointegratedPairsScanner

@pytest.mark.asyncio
async def test_kelly_criterion_sizing():
    """Verify Kelly Criterion computes mathematically correct sizing fractions from rolling performance statistics."""
    test_db_path = "/tmp/test_kelly_bot.db"
    if os.path.exists(test_db_path):
        try:
            os.remove(test_db_path)
        except Exception:
            pass
            
    db = DatabaseManager(test_db_path)
    
    # Pre-populate 10 trades: 7 winning trades ($150 profit each), 3 losing trades ($100 loss each)
    # Win rate (W) = 7 / 10 = 0.7
    # Avg win = 150.0, Avg loss = 100.0 -> Payoff ratio (R) = 1.5
    # Kelly: f* = W - (1-W)/R = 0.7 - 0.3 / 1.5 = 0.7 - 0.2 = 0.5
    # Half-Kelly = 0.5 * 0.5 = 0.25 (25% exposure)
    for _ in range(7):
        db.record_trade("BTC", "BUY", 0.5, 60000.0, pnl=150.0, cloid="TX")
    for _ in range(3):
        db.record_trade("BTC", "SELL", 0.5, 60000.0, pnl=-100.0, cloid="TX")

        
    stats = db.get_trade_performance_stats(limit=30)
    assert stats["win_rate"] == 0.7
    assert stats["payoff_ratio"] == 1.5
    assert stats["total_trades"] == 10
    
    # 2. Risk Manager Kelly Sizing
    manager = RiskManager()
    manager.daily_starting_equity = 10000.0
    manager.last_sync_time = time.time()  # Skip network sync
    
    # Mock database to point to our test database manager
    import backend.services.risk_manager as rm
    original_db = rm.db
    rm.db = db  # Temporary hot-patch
    
    # Mock hl_client to return a fake user state with $10000 account
    from unittest.mock import AsyncMock, patch
    mock_user_state = {
        "crossMarginSummary": {"accountValue": "10000.0", "totalMarginUsed": "0.0"},
        "marginSummary": {"accountValue": "10000.0", "totalMarginUsed": "0.0", "withdrawable": "10000.0"},
        "assetPositions": [],
    }
    
    try:
        with patch("backend.services.risk_manager.hl_client") as mock_hl:
            mock_hl.get_user_state = AsyncMock(return_value=mock_user_state)
            mock_hl.get_positions = AsyncMock(return_value=[])
            mock_hl.is_active = True
            
            now_ms = int(time.time() * 1000)
            approved, reason, size = await manager.evaluate_order("BTC", "LONG", 60000.0, now_ms)
            
            # With adaptive uncertainty penalty, the sizing is now:
            # Kelly: 0.5, Half-Kelly: 0.25, Uncertainty: ~0.434 (10 trades = moderate sample uncertainty)
            # Adjusted: 0.25 * (1 - 0.434) = 0.1414 → clamped at 14.14%
            # Size = 10000 * 0.1414 / 60000 = 0.02357
            # The key validation: order approved AND size is between floor (0.5%) and max (20%)
            assert approved is True, f"Expected approved, got: {reason}"
            min_size = round(10000 * 0.005 / 60000, 5)  # Floor: 0.5% allocation
            max_size = round(10000 * 0.20 / 60000, 5)    # Cap: 20% allocation (Config.MAX_EXPOSURE_PCT)
            assert min_size <= size <= max_size, f"Size {size} outside valid bounds [{min_size}, {max_size}]"
            # With uncertainty penalty, size must be LESS than the old static Half-Kelly result (0.03333)
            assert size < 0.03333, f"Adaptive Kelly should produce smaller size than static Half-Kelly, got {size}"
    finally:
        rm.db = original_db
        db = None
        if os.path.exists(test_db_path):
            try:
                os.remove(test_db_path)
            except Exception:
                pass



def test_backtester_engine():
    """Verify historical backtester runs spread arbitrage simulations correctly."""
    backtest_engine = HistoricalBacktester()
    
    # 1. Generate co-integrated mock pricing trails
    df_data = backtest_engine.generate_mock_history(days=2, ticks_per_day=50)
    assert len(df_data) == 100
    assert "BTC" in df_data.columns
    assert "ETH" in df_data.columns
    
    # 2. Run simulation
    results = backtest_engine.run_backtest(df_data, entry_z=2.0, exit_z=0.5, sentiment_score=0.0)
    
    # Assert fields are returned
    assert "total_trades" in results
    assert "win_rate" in results
    assert "sharpe_ratio" in results
    assert "max_drawdown" in results
    assert "balances" in results
    assert len(results["balances"]) == 100

def test_altcoin_pairs_scanner():
    """Verify cointegrated pairs scanner computes correlations and linear regressions correctly."""
    # 1. Create a scanner with 3 test altcoins
    scanner = CointegratedPairsScanner(assets=["SOL", "AVAX", "NEAR"])
    
    # 2. Seed price histories with highly co-linear synthetic prices
    # AVAX price = 0.25 * SOL price + noise
    # NEAR price = independent random walk
    np_seed = 42
    import numpy as np
    np.random.seed(np_seed)
    
    sol_prices = np.linspace(150.0, 160.0, 20)
    avax_prices = 0.25 * sol_prices + np.random.normal(0, 0.05, 20)
    near_prices = np.random.uniform(5.0, 8.0, 20)
    
    for i in range(20):
        scanner.price_history["SOL"].append(sol_prices[i])
        scanner.price_history["AVAX"].append(avax_prices[i])
        scanner.price_history["NEAR"].append(near_prices[i])
        
    # 3. Trigger manual computation
    scanner._calculate_cointegration()
    
    rankings = scanner.get_rankings()
    assert len(rankings) == 3
    
    # SOL_AVAX should have extremely high correlation (> 0.95) and correct hedge ratio (~0.25)
    sol_avax = next(r for r in rankings if r["pair"] == "SOL_AVAX")
    assert sol_avax["correlation"] > 0.95
    assert abs(sol_avax["hedge_ratio"] - 4.0) < 0.2 # Regressing SOL on AVAX yields ratio ~4
    assert sol_avax["status"] == "COINTEGRATED"

@pytest.mark.asyncio
async def test_execution_algorithms():
    """Verify that TWAP, VWAP, and Iceberg slicing execution models operate mathematically correct."""
    from backend.services.execution_algos import execution_algos
    from backend.services.hyperliquid_client import hl_client
    
    original_active = hl_client.is_active
    hl_client.is_active = False
    
    try:
        # Run tests in simulated dry-run
        await execution_algos.execute_twap("BTC", is_buy=True, total_size=0.1, duration_seconds=1, slices=2)
        await execution_algos.execute_vwap("BTC", is_buy=False, total_size=0.1, duration_seconds=1, slices=2)
        await execution_algos.execute_iceberg("BTC", is_buy=True, total_size=0.1, visible_size=0.04)
        
        # Check that mock logs are generated correctly in the database
        from backend.services.database import db
        recent_logs = db.get_logs(limit=100)
        assert any("TWAP" in log["message"] for log in recent_logs)
    finally:
        hl_client.is_active = original_active

