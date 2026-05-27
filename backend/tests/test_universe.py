import time
import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from backend.services.universe_manager import UniverseManagerService

@pytest.mark.asyncio
async def test_universe_fallback_when_offline():
    um = UniverseManagerService()
    um.last_update_time = 0.0
    with patch('backend.services.universe_manager.hl_client') as mock_hl:
        mock_hl.is_active = False
        mock_hl.info = None
        coins = await um.update_universe()
        assert coins == ["BTC", "ETH", "SOL"]

@pytest.mark.asyncio
async def test_universe_filters_liquidity():
    um = UniverseManagerService()
    um.last_update_time = 0.0
    mock_meta = {"universe": [
        {"name": "GOOD_VOL", "szDecimals": 2},
        {"name": "LOW_VOL", "szDecimals": 2},
        {"name": "WIDE_SPREAD", "szDecimals": 2}
    ]}
    mock_ctxs = [
        {"dayNtlVlm": "3000000", "midPx": "100.0", "impactPxs": ["99.98", "100.02"]}, # Spread = 0.04%
        {"dayNtlVlm": "1000000", "midPx": "100.0", "impactPxs": ["99.98", "100.02"]}, # Low vol
        {"dayNtlVlm": "5000000", "midPx": "100.0", "impactPxs": ["99.00", "101.00"]}  # Spread = 2%
    ]
    with patch('backend.services.universe_manager.hl_client') as mock_hl:
        mock_hl.is_active = True
        mock_info = MagicMock()
        mock_info.meta_and_asset_ctxs.return_value = [mock_meta, mock_ctxs]
        mock_hl.info = mock_info
        coins = await um.update_universe()
        assert coins == ["GOOD_VOL"]
        
        # Verify metadata was cached for all coins with valid data
        good_meta = um.get_asset_metadata("GOOD_VOL")
        assert good_meta["sz_decimals"] == 2
        assert good_meta["mid_px"] == 100.0
        assert good_meta["volume_24h"] == 3000000.0
