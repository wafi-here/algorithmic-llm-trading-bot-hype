import pytest
import asyncio
from backend.services.execution_algos import execution_algos
from unittest.mock import patch, MagicMock

@pytest.mark.asyncio
async def test_execute_market_making_basic():
    coin = "BTC"
    signals = {"bid_price": 60000.0, "ask_price": 61000.0, "adverse_selection_halt": False}
    size = 0.1
    
    with patch("backend.services.hyperliquid_client.hl_client.get_open_orders", return_value=[]):
        with patch("backend.services.hyperliquid_client.hl_client.place_order") as mock_place:
            mock_place.return_value = {"status": "ok", "response": {"id": "123"}}
            
            await execution_algos.execute_market_making(coin, signals, size)
            
            assert mock_place.call_count == 2
            assert execution_algos.active_mm_orders[coin]["bid_oid"] == "123"
            assert execution_algos.active_mm_orders[coin]["ask_oid"] == "123"

@pytest.mark.asyncio
async def test_execute_grid_trading_basic():
    coin = "ETH"
    signals = {
        "buy_levels": [{"level": 1, "price": 3000.0, "size": 0.1}],
        "sell_levels": [{"level": 1, "price": 3100.0, "size": 0.1}]
    }
    size = 1.0
    
    with patch("backend.services.hyperliquid_client.hl_client.get_open_orders", return_value=[]):
        with patch("backend.services.hyperliquid_client.hl_client.place_order") as mock_place:
            mock_place.return_value = {"status": "ok", "response": {"id": "456"}}
            
            await execution_algos.execute_grid_trading(coin, signals, size)
            
            assert mock_place.call_count == 2
            assert len(execution_algos.active_grid_orders[coin]) == 2
