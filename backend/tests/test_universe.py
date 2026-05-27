from backend.services.universe_manager import universe_manager
import pytest
from unittest.mock import patch, MagicMock

def test_universe_fallback_when_offline():
    universe_manager.last_update_time = 0.0
    with patch('backend.services.hyperliquid_client.hl_client.is_active', False):
        coins = universe_manager.update_universe()
        assert coins == ["BTC", "ETH", "SOL"]

def test_universe_filters_liquidity():
    # Mock hl_client data
    universe_manager.last_update_time = 0.0
    mock_meta = {"universe": [{"name": "GOOD_VOL"}, {"name": "LOW_VOL"}, {"name": "WIDE_SPREAD"}]}
    mock_ctxs = [
        {"dayNtlVlm": "3000000", "midPx": "100.0", "impactPxs": ["99.98", "100.02"]}, # Spread = 0.04%
        {"dayNtlVlm": "1000000", "midPx": "100.0", "impactPxs": ["99.98", "100.02"]}, # Low vol
        {"dayNtlVlm": "5000000", "midPx": "100.0", "impactPxs": ["99.00", "101.00"]}  # Spread = 2%
    ]
    with patch('backend.services.hyperliquid_client.hl_client.is_active', True):
        with patch('backend.services.hyperliquid_client.hl_client.info') as mock_info:
            mock_info.meta_and_asset_ctxs.return_value = [mock_meta, mock_ctxs]
            coins = universe_manager.update_universe()
            assert coins == ["GOOD_VOL"]
