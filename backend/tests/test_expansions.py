import os
import time
import pytest
import pandas as pd
from backend.services.database import DatabaseManager
from backend.services.risk_manager import RiskManager
from backend.services.backtester import HistoricalBacktester
from backend.services.pairs_scanner import CointegratedPairsScanner

def test_kelly_criterion_sizing():
    """Verify Kelly Criterion computes mathematically correct sizing fractions from rolling performance statistics."""
    # 1. Setup temporary test database
    test_db_path = "test_kelly_bot.db"
    if os.path.exists(test_db_path):
        os.remove(test_db_path)
        
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
    
    # Mock database to point to our test database manager
    import backend.services.risk_manager as rm
    original_db = rm.db
    rm.db = db  # Temporary hot-patch
    
    try:
        now_ms = int(time.time() * 1000)
        approved, reason, size = manager.evaluate_order("BTC", "LONG", 60000.0, now_ms)
        
        # Balance = 10000.0 (mock)
        # Clamped Alloc = min(Config.MAX_EXPOSURE_PCT, Half-Kelly) = min(0.20, 0.25) = 0.20 (20%)
        # Risk Amount = 10000 * 0.20 = 2000.0
        # Size = 2000.0 / 60000.0 = 0.033333... -> rounded for BTC is 0.0333
        assert approved is True
        assert size == 0.0333
    finally:
        rm.db = original_db
        db = None
        if os.path.exists(test_db_path):
            os.remove(test_db_path)

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
