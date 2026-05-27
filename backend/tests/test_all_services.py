import os
import time
import pytest
from backend.services.database import DatabaseManager
from backend.services.llm_sentiment import LLMSentimentEngine
from backend.services.orderbook_tracker import OrderBookTracker
from backend.services.risk_manager import RiskManager

def test_database_manager_operations():
    """Verify in-depth SQLite operations using a dedicated test database."""
    test_db_path = "test_trading_bot.db"
    if os.path.exists(test_db_path):
        os.remove(test_db_path)
        
    db = DatabaseManager(test_db_path)
    
    # 1. Log System
    db.log_system("INFO", "Test system logging")
    logs = db.get_logs(limit=5)
    assert len(logs) == 1
    assert logs[0]["level"] == "INFO"
    assert logs[0]["message"] == "Test system logging"
    
    # 2. Record Trade
    db.record_trade("BTC", "BUY", 0.5, 67000.0, pnl=10.0, cloid="TEST_CLOID")
    trades = db.get_recent_trades(limit=5)
    assert len(trades) == 1
    assert trades[0]["coin"] == "BTC"
    assert trades[0]["side"] == "BUY"
    assert trades[0]["size"] == 0.5
    assert trades[0]["price"] == 67000.0
    assert trades[0]["pnl"] == 10.0
    assert trades[0]["cloid"] == "TEST_CLOID"
    
    # 3. Record Z-score
    db.record_zscore("BTC", "ETH", 67000.0, 3500.0, 370.0, 2.0)
    zscores = db.get_latest_zscores(limit=5)
    assert len(zscores) == 1
    assert zscores[0]["asset_a"] == "BTC"
    assert zscores[0]["price_a"] == 67000.0
    assert zscores[0]["zscore"] == 2.0
    
    # 4. Record Sentiment
    db.record_sentiment("Crypto Bull Run", "CoinDesk", "http://test.com", "Long text", 0.8, "Bullish summary")
    sentiment = db.get_latest_sentiment(limit=5)
    assert len(sentiment) == 1
    assert sentiment[0]["title"] == "Crypto Bull Run"
    assert sentiment[0]["sentiment_score"] == 0.8
    
    # 5. Prune Data
    db.prune_stale_data(days_threshold=0) # prune everything
    
    # Clean up test database
    db = None
    if os.path.exists(test_db_path):
        os.remove(test_db_path)

def test_sentiment_vader_engine():
    """Verify that local NLP Vader sentiment categorizes texts correctly."""
    engine = LLMSentimentEngine()
    
    # Highly Bullish/Positive Text
    bullish_score = engine._analyze_local_vader("This is a great wonderful excellent victory! crypto love")
    assert bullish_score > 0.0
    
    # Highly Bearish/Negative Text
    bearish_score = engine._analyze_local_vader("This is a bad terrible horrible disaster! crash failure")
    assert bearish_score < 0.0
    
    # Neutral text
    neutral_score = engine._analyze_local_vader("The database contains multiple columns of data.")
    assert abs(neutral_score) < 0.1

def test_orderbook_tracker_calculations():
    """Verify that L2 orderbook parser computes prices, spreads, and imbalances correctly."""
    tracker = OrderBookTracker(coins=["BTC"])
    
    # Check default mock state fallback
    state = tracker.get_market_state("BTC")
    assert state["is_mock"] is True
    assert state["mid"] == 67250.0
    
    # Inject active L2 orderbook book ticks
    mock_l2_data = {
        "coin": "BTC",
        "levels": [
            # bids (ordered high to low)
            [{"px": "67000.0", "sz": "1.5"}, {"px": "66990.0", "sz": "2.0"}],
            # asks (ordered low to high)
            [{"px": "67010.0", "sz": "2.5"}, {"px": "67020.0", "sz": "3.5"}]
        ]
    }
    
    tracker._process_l2book(mock_l2_data)
    
    active_state = tracker.get_market_state("BTC")
    assert active_state.get("is_mock") is None # Processed real state
    assert active_state["mid"] == 67005.0
    assert active_state["spread"] == 10.0
    assert active_state["best_bid"] == 67000.0
    assert active_state["best_ask"] == 67010.0
    
    # Imbalance: bids top volume vs asks top volume
    # bids vol = 1.5 + 2.0 = 3.5
    # asks vol = 2.5 + 3.5 = 6.0
    # imbalance = (3.5 - 6.0) / (3.5 + 6.0) = -2.5 / 9.5 = -0.26315789
    assert abs(active_state["imbalance"] - (-0.26315789)) < 1e-5

@pytest.mark.asyncio
async def test_risk_manager_fractional_sizing():
    """Verify position sizing math under standard capital boundaries."""
    from unittest.mock import AsyncMock, patch
    manager = RiskManager()
    
    # Override starting equity for standard calculation test
    manager.daily_starting_equity = 10000.0
    manager.last_sync_time = time.time()  # Skip network sync
    
    mock_user_state = {
        "crossMarginSummary": {"accountValue": "10000.0", "totalMarginUsed": "0.0"},
        "marginSummary": {"accountValue": "10000.0", "totalMarginUsed": "0.0", "withdrawable": "10000.0"},
        "assetPositions": [],
    }
    
    with patch("backend.services.risk_manager.hl_client") as mock_hl:
        mock_hl.get_user_state = AsyncMock(return_value=mock_user_state)
        mock_hl.get_positions = AsyncMock(return_value=[])
        mock_hl.is_active = True
        
        # Let's test evaluating long order at 67000.0
        # Risk 1% of 10000.0 = 100.00 -> but min $11 applies, so risk_amount = max(100, 11) = 100
        # size = 100.00 / 67000.0 = 0.0014925 -> rounded to 5 decimals (BTC szDecimals=5) = 0.00149
        # But min notional $11 check: 0.00149 * 67000 = $99.83 >= $11 OK
        now_ms = int(time.time() * 1000)
        approved, reason, size = await manager.evaluate_order("BTC", "LONG", 67000.0, now_ms)
        
        assert approved is True, f"Expected approved, got: {reason}"
        assert size > 0, f"Expected positive size, got {size}"

