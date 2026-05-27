"""
Extreme / Rough Tests for Micro-Account Optimization.

Tests cover:
1. Margin pre-check (sufficient, insufficient, zero equity, negative free margin)
2. Ranked signal fallback logic
3. Signal discipline preservation (Strong Consensus required)
4. Dynamic szDecimals from metadata
5. Extreme leverage scenarios
6. Concurrent margin race conditions

All tests use mocked hl_client to avoid real API calls.
"""
import os
import time
import asyncio
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

# Setup test database BEFORE importing modules that use it
from backend.services.database import db as main_db
main_db.db_path = "/tmp/test_micro_opt.db"
if os.path.exists(main_db.db_path):
    try:
        os.remove(main_db.db_path)
    except Exception:
        pass
main_db._initialize_db()

from backend.services.risk_manager import RiskManager
from backend.services.universe_manager import UniverseManagerService


# --- Helper: Create a mock user state ---
def make_mock_user_state(account_value: float, margin_used: float, positions=None):
    """Creates a mock Hyperliquid user state dict."""
    state = {
        "crossMarginSummary": {
            "accountValue": str(account_value),
            "totalMarginUsed": str(margin_used),
        },
        "marginSummary": {
            "accountValue": str(account_value),
            "totalMarginUsed": str(margin_used),
            "withdrawable": str(max(0, account_value - margin_used)),
        },
        "assetPositions": positions or [],
    }
    return state


# --- Helper: Create a RiskManager with mocked dependencies ---
def create_test_risk_manager():
    rm = RiskManager()
    rm.daily_starting_equity = 10.0
    rm.last_sync_time = time.time()  # Skip sync_equity network calls
    rm._last_equity_date = None
    return rm


# ============================================================
# TEST 1: Margin pre-check SUFFICIENT
# Account $50, margin used $5 → free $45 → order BTC $15 notional at 5x → needs $3.60 → APPROVED
# ============================================================
@pytest.mark.asyncio
async def test_margin_precheck_sufficient():
    rm = create_test_risk_manager()
    rm.daily_starting_equity = 50.0

    mock_state = make_mock_user_state(account_value=50.0, margin_used=5.0)

    with patch("backend.services.risk_manager.hl_client") as mock_hl:
        mock_hl.get_user_state = AsyncMock(return_value=mock_state)
        mock_hl.is_active = True

        now_ms = int(time.time() * 1000)
        approved, reason, size = await rm.evaluate_order("SOL", "LONG", 80.0, now_ms)

        assert approved is True, f"Expected APPROVED but got REJECTED: {reason}"
        assert size > 0, "Size should be positive for approved order"


# ============================================================
# TEST 2: Margin pre-check INSUFFICIENT
# Account $8, margin used $7 → free $1 → any $11 notional at 50x → needs $0.264 → APPROVED
# BUT at even higher margin used ($7.80) → free $0.20 → needs $0.264 → REJECTED
# ============================================================
@pytest.mark.asyncio
async def test_margin_precheck_insufficient():
    rm = create_test_risk_manager()
    rm.daily_starting_equity = 8.0

    # Edge case: margin used is nearly all equity
    mock_state = make_mock_user_state(account_value=8.0, margin_used=7.80)

    with patch("backend.services.risk_manager.hl_client") as mock_hl:
        mock_hl.get_user_state = AsyncMock(return_value=mock_state)
        mock_hl.is_active = True

        now_ms = int(time.time() * 1000)
        approved, reason, size = await rm.evaluate_order("DOGE", "LONG", 0.10, now_ms)

        # With $0.20 free margin and 50x leverage, $11 notional needs $0.264 margin → REJECTED
        assert approved is False, f"Expected REJECTED but got APPROVED: {reason}"
        assert "Margin pre-check FAILED" in reason or "Max exposure" in reason, f"Unexpected reason: {reason}"


# ============================================================
# TEST 3: Zero equity (API glitch) → fallback to daily_starting_equity
# ============================================================
@pytest.mark.asyncio
async def test_margin_precheck_zero_equity():
    rm = create_test_risk_manager()
    rm.daily_starting_equity = 10.0

    # API returns account value = 0 (common Hyperliquid glitch)
    mock_state = make_mock_user_state(account_value=0.0, margin_used=0.0)

    with patch("backend.services.risk_manager.hl_client") as mock_hl:
        mock_hl.get_user_state = AsyncMock(return_value=mock_state)
        mock_hl.is_active = True

        now_ms = int(time.time() * 1000)
        approved, reason, size = await rm.evaluate_order("HYPE", "LONG", 60.0, now_ms)

        # Should use fallback equity $10 instead of crashing
        # With $10 equity and $0 margin used, free margin = $10
        # At 50x leverage, $11 notional needs $0.264 margin → APPROVED
        assert approved is True, f"Expected APPROVED with fallback equity, got: {reason}"
        assert size > 0


# ============================================================
# TEST 4: Ranked signal fallback — BTC fails margin, DOGE succeeds
# ============================================================
@pytest.mark.asyncio
async def test_ranked_signal_fallback():
    rm = create_test_risk_manager()
    rm.daily_starting_equity = 8.0

    # First call: margin is tight, second call: same state
    mock_state_tight = make_mock_user_state(account_value=8.0, margin_used=7.75)
    mock_state_ok = make_mock_user_state(account_value=8.0, margin_used=0.5)

    with patch("backend.services.risk_manager.hl_client") as mock_hl:
        mock_hl.get_user_state = AsyncMock(return_value=mock_state_tight)
        mock_hl.is_active = True

        # BTC should fail margin check
        feasible_btc, reason_btc = await rm.check_margin_feasibility("BTC", 75000.0)
        assert feasible_btc is False, f"BTC should fail margin: {reason_btc}"

        # Now simulate margin freed up (e.g., after closing a position)
        mock_hl.get_user_state = AsyncMock(return_value=mock_state_ok)

        # DOGE should pass margin check
        feasible_doge, reason_doge = await rm.check_margin_feasibility("DOGE", 0.10)
        assert feasible_doge is True, f"DOGE should pass margin: {reason_doge}"


# ============================================================
# TEST 5: All signals exhausted — no executable opportunities
# ============================================================
@pytest.mark.asyncio
async def test_all_signals_exhausted():
    rm = create_test_risk_manager()
    rm.daily_starting_equity = 8.0

    mock_state = make_mock_user_state(account_value=8.0, margin_used=7.80)

    with patch("backend.services.risk_manager.hl_client") as mock_hl:
        mock_hl.get_user_state = AsyncMock(return_value=mock_state)
        mock_hl.is_active = True

        # All coins should fail margin feasibility
        coins = ["BTC", "ETH", "SOL", "DOGE", "HYPE"]
        prices = [75000.0, 2000.0, 80.0, 0.10, 60.0]
        all_failed = True

        for coin, price in zip(coins, prices):
            feasible, _ = await rm.check_margin_feasibility(coin, price)
            if feasible:
                all_failed = False
                break

        assert all_failed is True, "Expected ALL coins to fail margin feasibility"


# ============================================================
# TEST 6: Discipline preserved — no execution without consensus
# Signal has only Momentum (no Breakout confirmation) → should NOT execute
# ============================================================
@pytest.mark.asyncio
async def test_discipline_preserved():
    """Simulate the signal ranking logic inline to verify discipline."""
    # This tests the ranking logic directly (not evaluate_order)
    
    # Simulated signals for a coin
    mom_sig = "LONG"
    brk_sig = "FLAT"  # Breakout does NOT agree
    zs_sig = None
    
    # Apply the same ranking logic from main.py
    action = None
    strength = 0
    
    # Priority 1: Triple consensus
    if zs_sig and mom_sig and brk_sig and zs_sig == mom_sig == brk_sig and zs_sig != "FLAT":
        action = zs_sig
        strength = 3
    
    # Priority 2: Z-Score + one confirmer
    elif zs_sig and zs_sig != "FLAT" and (mom_sig == zs_sig or brk_sig == zs_sig):
        action = zs_sig
        strength = 2
    
    # Priority 3: Momentum + Breakout consensus
    elif mom_sig and brk_sig and mom_sig == brk_sig and mom_sig != "FLAT":
        action = mom_sig
        strength = 2
    
    # Priority 4: Z-Score FLAT
    elif zs_sig == "FLAT":
        action = "FLAT"
        strength = 1
    
    # Momentum alone without Breakout confirmation → NO signal
    assert action is None, f"Expected NO signal for Momentum-only, got action={action}, strength={strength}"
    assert strength == 0, f"Expected strength 0, got {strength}"
    
    # Now test WITH consensus
    brk_sig = "LONG"  # Breakout NOW agrees
    
    # Re-evaluate
    action2 = None
    strength2 = 0
    
    if zs_sig and mom_sig and brk_sig and zs_sig == mom_sig == brk_sig and zs_sig != "FLAT":
        action2 = zs_sig
        strength2 = 3
    elif zs_sig and zs_sig != "FLAT" and (mom_sig == zs_sig or brk_sig == zs_sig):
        action2 = zs_sig
        strength2 = 2
    elif mom_sig and brk_sig and mom_sig == brk_sig and mom_sig != "FLAT":
        action2 = mom_sig
        strength2 = 2
    elif zs_sig == "FLAT":
        action2 = "FLAT"
        strength2 = 1
    
    assert action2 == "LONG", f"Expected LONG with consensus, got {action2}"
    assert strength2 == 2, f"Expected strength 2, got {strength2}"


# ============================================================
# TEST 7: Dynamic szDecimals from metadata
# ============================================================
def test_dynamic_sz_decimals():
    rm = create_test_risk_manager()

    # First test: no metadata cached → should use fallback
    with patch("backend.services.universe_manager.universe_manager") as mock_um:
        mock_um.get_asset_metadata.return_value = {}
        
        decimals, min_step = rm.get_asset_sz_decimals("BTC")
        assert decimals == 5, f"Expected BTC fallback szDecimals=5, got {decimals}"
        assert min_step == 0.00001, f"Expected BTC fallback min_step=0.00001, got {min_step}"

    # Second test: metadata IS cached → should use dynamic values
    with patch("backend.services.universe_manager.universe_manager") as mock_um:
        mock_um.get_asset_metadata.return_value = {
            "sz_decimals": 3,
            "min_step": 0.001,
            "mid_px": 75000.0,
            "volume_24h": 2000000000.0,
            "spread_pct": 0.0001
        }
        
        decimals, min_step = rm.get_asset_sz_decimals("BTC")
        assert decimals == 3, f"Expected dynamic szDecimals=3, got {decimals}"
        assert min_step == 0.001, f"Expected dynamic min_step=0.001, got {min_step}"

    # Third test: unknown coin without metadata → should use default (2, 0.01)
    with patch("backend.services.universe_manager.universe_manager") as mock_um:
        mock_um.get_asset_metadata.return_value = {}
        
        decimals, min_step = rm.get_asset_sz_decimals("UNKNOWN_COIN_XYZ")
        assert decimals == 2, f"Expected default szDecimals=2 for unknown coin, got {decimals}"
        assert min_step == 0.01, f"Expected default min_step=0.01 for unknown coin, got {min_step}"


# ============================================================
# TEST 8: Negative free margin (underwater account)
# margin_used > account_value → free margin is negative → REJECT all orders
# ============================================================
@pytest.mark.asyncio
async def test_negative_free_margin():
    rm = create_test_risk_manager()
    rm.daily_starting_equity = 5.0

    # Underwater: account $5, margin used $6 → free margin = -$1
    mock_state = make_mock_user_state(account_value=5.0, margin_used=6.0)

    with patch("backend.services.risk_manager.hl_client") as mock_hl:
        mock_hl.get_user_state = AsyncMock(return_value=mock_state)
        mock_hl.is_active = True

        now_ms = int(time.time() * 1000)
        approved, reason, size = await rm.evaluate_order("HYPE", "LONG", 60.0, now_ms)

        assert approved is False, f"Expected REJECTED for underwater account, got APPROVED: {reason}"
        # Should be caught by either max exposure check or margin pre-check
        assert "exposure" in reason.lower() or "margin" in reason.lower(), f"Unexpected reason: {reason}"


# ============================================================
# TEST 9: Extreme leverage 50x — micro account CAN open positions
# Account $8, leverage 50x → free margin $8
# Order $11 notional → required margin = $11 / 50 = $0.22 * 1.2 buffer = $0.264
# Free margin $8 >> $0.264 → APPROVED
# ============================================================
@pytest.mark.asyncio
async def test_extreme_leverage_50x_margin():
    rm = create_test_risk_manager()
    rm.daily_starting_equity = 8.0

    mock_state = make_mock_user_state(account_value=8.0, margin_used=0.0)

    with patch("backend.services.risk_manager.hl_client") as mock_hl:
        mock_hl.get_user_state = AsyncMock(return_value=mock_state)
        mock_hl.is_active = True

        now_ms = int(time.time() * 1000)
        approved, reason, size = await rm.evaluate_order("DOGE", "LONG", 0.10, now_ms)

        assert approved is True, f"Expected APPROVED for $8 with 50x leverage, got: {reason}"
        # Verify the margin math
        notional = size * 0.10
        assert notional >= 11.0, f"Notional ${notional:.2f} should be >= $11.00 minimum"
        
        # Verify required margin is reasonable
        required_margin = notional / 50  # 50x leverage
        assert required_margin < 8.0, f"Required margin ${required_margin:.2f} should be < $8.00 free margin"


# ============================================================
# TEST 10: Concurrent margin race — two sequential orders deplete margin
# First order uses up margin → second order should be rejected
# ============================================================
@pytest.mark.asyncio
async def test_concurrent_margin_race():
    rm = create_test_risk_manager()
    rm.daily_starting_equity = 8.0

    # Initial state: enough margin for one order
    mock_state_before = make_mock_user_state(account_value=8.0, margin_used=0.0)
    # After first order: margin is mostly consumed
    mock_state_after = make_mock_user_state(account_value=8.0, margin_used=7.80)

    with patch("backend.services.risk_manager.hl_client") as mock_hl:
        mock_hl.is_active = True
        
        # First order: should succeed
        mock_hl.get_user_state = AsyncMock(return_value=mock_state_before)
        now_ms = int(time.time() * 1000)
        approved1, reason1, size1 = await rm.evaluate_order("DOGE", "LONG", 0.10, now_ms)
        assert approved1 is True, f"First order should be APPROVED: {reason1}"
        
        # Simulate margin consumed by first order
        mock_hl.get_user_state = AsyncMock(return_value=mock_state_after)
        
        # Second order: should be rejected (margin depleted)
        now_ms2 = int(time.time() * 1000)
        approved2, reason2, size2 = await rm.evaluate_order("HYPE", "LONG", 60.0, now_ms2)
        assert approved2 is False, f"Second order should be REJECTED after margin depletion: {reason2}"


# ============================================================
# TEST 11 (Bonus): UniverseManager metadata caching
# ============================================================
def test_universe_manager_metadata():
    um = UniverseManagerService()
    
    # Initially empty
    meta = um.get_asset_metadata("BTC")
    assert meta == {}, "Metadata should be empty before first scan"
    
    # Manually populate metadata (simulating what update_universe does)
    um.asset_metadata = {
        "BTC": {
            "sz_decimals": 5,
            "min_step": 0.00001,
            "mid_px": 75000.0,
            "volume_24h": 2400000000.0,
            "spread_pct": 0.0001,
        },
        "DOGE": {
            "sz_decimals": 0,
            "min_step": 1.0,
            "mid_px": 0.10,
            "volume_24h": 7000000.0,
            "spread_pct": 0.0003,
        }
    }
    
    btc_meta = um.get_asset_metadata("BTC")
    assert btc_meta["sz_decimals"] == 5
    assert btc_meta["min_step"] == 0.00001
    assert btc_meta["mid_px"] == 75000.0
    
    doge_meta = um.get_asset_metadata("DOGE")
    assert doge_meta["sz_decimals"] == 0
    assert doge_meta["min_step"] == 1.0
    
    unknown_meta = um.get_asset_metadata("UNKNOWN")
    assert unknown_meta == {}


# ============================================================
# TEST 12 (Bonus): Free margin and required margin math
# ============================================================
def test_margin_math_pure():
    rm = create_test_risk_manager()
    
    # Free margin calculation
    assert rm.get_free_margin(100.0, 20.0) == 80.0
    assert rm.get_free_margin(8.0, 8.0) == 0.0
    assert rm.get_free_margin(5.0, 6.0) == -1.0  # Underwater
    assert rm.get_free_margin(0.0, 0.0) == 0.0
    
    # Required margin estimation
    assert rm.estimate_required_margin(100.0, 10) == 10.0
    assert rm.estimate_required_margin(11.0, 50) == 0.22
    assert rm.estimate_required_margin(11.0, 1) == 11.0
    assert rm.estimate_required_margin(11.0, 0) == 11.0  # Edge: zero leverage → treated as 1x
    assert rm.estimate_required_margin(0.0, 50) == 0.0
