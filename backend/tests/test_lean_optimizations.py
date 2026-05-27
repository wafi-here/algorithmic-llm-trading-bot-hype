"""
Comprehensive tests for Lean-inspired Pareto optimizations.
Tests: Trailing Stop, Insight System, Dynamic Slippage, Stacked Risk Pipeline, Exit Intelligence.
"""
import pytest
import time
import numpy as np
from unittest.mock import patch, AsyncMock, MagicMock
from collections import deque


# ===========================================================================
# Component 1+5: Trailing Stop + Exit Intelligence Tests
# ===========================================================================

class TestTrailingStopManager:
    """Tests for per-position trailing stop tracking."""

    def test_register_and_track_long_position(self):
        """Registering a LONG position should set initial stop below entry."""
        from backend.services.trailing_stop import TrailingStopManager
        
        mgr = TrailingStopManager()
        mgr.register_position("BTC", "LONG", 60000.0, 0.1, trailing_stop_pct=0.05)
        
        assert "BTC" in mgr.positions
        pos = mgr.positions["BTC"]
        assert pos.current_stop_price == 60000.0 * 0.95  # 5% below entry
        assert pos.entry_price == 60000.0
        assert pos.side == "LONG"

    def test_register_and_track_short_position(self):
        """Registering a SHORT position should set initial stop above entry."""
        from backend.services.trailing_stop import TrailingStopManager
        
        mgr = TrailingStopManager()
        mgr.register_position("ETH", "SHORT", 3500.0, 1.0, trailing_stop_pct=0.05)
        
        pos = mgr.positions["ETH"]
        assert pos.current_stop_price == 3500.0 * 1.05  # 5% above entry

    def test_trailing_stop_trails_upward_for_long(self):
        """LONG trailing stop should move up as price increases."""
        from backend.services.trailing_stop import TrailingStopManager
        
        mgr = TrailingStopManager()
        mgr.register_position("BTC", "LONG", 60000.0, 0.1, trailing_stop_pct=0.05)
        
        initial_stop = mgr.positions["BTC"].current_stop_price
        
        # Price rises to 65000
        mgr.update_prices({"BTC": 65000.0})
        new_stop = mgr.positions["BTC"].current_stop_price
        
        assert new_stop > initial_stop, "Stop should trail upward"
        assert new_stop == 65000.0 * 0.95, "Stop should be 5% below new peak"

    def test_trailing_stop_never_moves_down_for_long(self):
        """LONG trailing stop should never decrease when price drops."""
        from backend.services.trailing_stop import TrailingStopManager
        
        mgr = TrailingStopManager()
        mgr.register_position("BTC", "LONG", 60000.0, 0.1, trailing_stop_pct=0.05)
        
        # Price rises
        mgr.update_prices({"BTC": 65000.0})
        peak_stop = mgr.positions["BTC"].current_stop_price
        
        # Price drops (but not to stop level)
        mgr.update_prices({"BTC": 63000.0})
        current_stop = mgr.positions["BTC"].current_stop_price
        
        assert current_stop == peak_stop, "Stop must not decrease"

    def test_stop_loss_triggers_exit(self):
        """Price breaching stop level should trigger TRAILING_STOP exit."""
        from backend.services.trailing_stop import TrailingStopManager
        
        mgr = TrailingStopManager()
        mgr.register_position("BTC", "LONG", 60000.0, 0.1, trailing_stop_pct=0.05)
        
        # Price drops below stop (60000 * 0.95 = 57000)
        exits = mgr.check_exits({"BTC": 56500.0})
        
        assert len(exits) == 1
        assert exits[0]["reason"] == "TRAILING_STOP"
        assert exits[0]["coin"] == "BTC"

    def test_take_profit_triggers_exit(self):
        """Price reaching TP target should trigger TAKE_PROFIT exit."""
        from backend.services.trailing_stop import TrailingStopManager
        
        mgr = TrailingStopManager()
        mgr.register_position("ETH", "LONG", 3000.0, 1.0, take_profit_pct=0.10)
        
        # Price rises 10% to 3300
        mgr.update_prices({"ETH": 3300.0})
        exits = mgr.check_exits({"ETH": 3300.0})
        
        assert len(exits) == 1
        assert exits[0]["reason"] == "TAKE_PROFIT"

    def test_time_expiry_triggers_exit(self):
        """Positions held beyond max_hold_seconds should trigger TIME_EXPIRY exit."""
        from backend.services.trailing_stop import TrailingStopManager
        
        mgr = TrailingStopManager()
        mgr.register_position("SOL", "LONG", 150.0, 5.0, max_hold_seconds=10)
        
        # Simulate position being old
        mgr.positions["SOL"].entry_time = time.time() - 11
        
        exits = mgr.check_exits({"SOL": 151.0})
        
        assert len(exits) == 1
        assert exits[0]["reason"] == "TIME_EXPIRY"

    def test_breakeven_activation(self):
        """Stop should move to entry price after breakeven activation threshold."""
        from backend.services.trailing_stop import TrailingStopManager
        
        mgr = TrailingStopManager()
        mgr.register_position("BTC", "LONG", 60000.0, 0.1, 
                             trailing_stop_pct=0.05, breakeven_activation_pct=0.02)
        
        initial_stop = mgr.positions["BTC"].current_stop_price
        assert initial_stop < 60000.0, "Initial stop below entry"
        
        # Price rises 2.5% (above 2% activation threshold)
        mgr.update_prices({"BTC": 61500.0})
        
        assert mgr.positions["BTC"].breakeven_activated == True
        assert mgr.positions["BTC"].current_stop_price >= 60000.0, "Stop should be at or above entry"

    def test_unregister_removes_position(self):
        """Unregistering should remove position from tracking."""
        from backend.services.trailing_stop import TrailingStopManager
        
        mgr = TrailingStopManager()
        mgr.register_position("BTC", "LONG", 60000.0, 0.1)
        assert mgr.has_position("BTC") == True
        
        mgr.unregister_position("BTC")
        assert mgr.has_position("BTC") == False

    def test_get_tracked_positions_api(self):
        """get_tracked_positions should return dashboard-ready data."""
        from backend.services.trailing_stop import TrailingStopManager
        
        mgr = TrailingStopManager()
        mgr.register_position("BTC", "LONG", 60000.0, 0.1)
        mgr.register_position("ETH", "SHORT", 3500.0, 1.0)
        
        positions = mgr.get_tracked_positions()
        assert len(positions) == 2
        assert all("current_stop_price" in p for p in positions)
        assert all("hold_seconds" in p for p in positions)


# ===========================================================================
# Component 2: Structured Insight System Tests
# ===========================================================================

class TestInsightSystem:
    """Tests for the Insight and InsightManager."""

    def test_insight_creation_and_properties(self):
        """Insight should have correct direction_sign and expiry status."""
        from backend.services.insight import Insight
        
        long = Insight("BTC", "LONG", 0.8, 0.02, 300, "Z-Score")
        assert long.direction_sign == 1
        assert long.is_expired == False
        
        short = Insight("ETH", "SHORT", 0.6, -0.01, 300, "ROC")
        assert short.direction_sign == -1
        
        flat = Insight("SOL", "FLAT", 0.5, 0.0, 300, "Z-Score")
        assert flat.direction_sign == 0

    def test_insight_expiry(self):
        """Insights should expire after their period_seconds."""
        from backend.services.insight import Insight
        
        insight = Insight("BTC", "LONG", 0.8, 0.02, 5, "Test")
        insight.created_at = time.time() - 10  # Created 10 seconds ago
        
        assert insight.is_expired == True
        assert insight.remaining_seconds == 0.0

    def test_emit_replaces_same_source(self):
        """Emitting from the same source should replace the previous insight."""
        from backend.services.insight import InsightManager, Insight
        
        mgr = InsightManager()
        mgr.emit(Insight("BTC", "LONG", 0.5, 0.01, 300, "Z-Score"))
        mgr.emit(Insight("BTC", "SHORT", 0.8, -0.01, 300, "Z-Score"))
        
        active = mgr.get_active_insights("BTC")
        assert len(active) == 1
        assert active[0].direction == "SHORT"

    def test_consensus_long_with_multiple_sources(self):
        """Multiple LONG insights should produce LONG consensus."""
        from backend.services.insight import InsightManager, Insight
        
        mgr = InsightManager()
        mgr.emit(Insight("BTC", "LONG", 0.9, 0.02, 300, "Z-Score"))
        mgr.emit(Insight("BTC", "LONG", 0.7, 0.015, 300, "ROC"))
        mgr.emit(Insight("BTC", "LONG", 0.6, 0.01, 300, "OBI"))
        
        consensus = mgr.get_consensus("BTC")
        assert consensus is not None
        assert consensus.direction == "LONG"
        assert consensus.confidence > 0.5
        assert len(consensus.sources) == 3
        assert consensus.strength > 1.0

    def test_consensus_no_agreement_returns_none(self):
        """Conflicting insights with equal weight should return None."""
        from backend.services.insight import InsightManager, Insight
        
        mgr = InsightManager()
        mgr.emit(Insight("BTC", "LONG", 0.5, 0.01, 300, "Z-Score"))
        mgr.emit(Insight("BTC", "SHORT", 0.5, -0.01, 300, "ROC"))
        
        consensus = mgr.get_consensus("BTC")
        assert consensus is None  # Perfect tie

    def test_consensus_majority_wins(self):
        """Direction with more confidence-weight should win consensus."""
        from backend.services.insight import InsightManager, Insight
        
        mgr = InsightManager()
        mgr.emit(Insight("BTC", "LONG", 0.9, 0.03, 300, "Z-Score"))
        mgr.emit(Insight("BTC", "LONG", 0.7, 0.02, 300, "ROC"))
        mgr.emit(Insight("BTC", "SHORT", 0.3, -0.005, 300, "OBI"))
        
        consensus = mgr.get_consensus("BTC")
        assert consensus.direction == "LONG"

    def test_expire_stale_removes_old_insights(self):
        """Expired insights should be purged."""
        from backend.services.insight import InsightManager, Insight
        
        mgr = InsightManager()
        # Create already-expired insight
        old = Insight("BTC", "LONG", 0.8, 0.02, 5, "Old")
        old.created_at = time.time() - 10
        mgr.emit(old)
        
        mgr.emit(Insight("ETH", "SHORT", 0.6, -0.01, 300, "Fresh"))
        
        count = mgr.expire_stale()
        assert count == 1
        assert mgr.get_active_insights("BTC") == []
        assert len(mgr.get_active_insights("ETH")) == 1

    def test_get_ranked_signals(self):
        """Ranked signals should be sorted by strength descending."""
        from backend.services.insight import InsightManager, Insight
        
        mgr = InsightManager()
        # High confidence with 2 sources
        mgr.emit(Insight("BTC", "LONG", 0.9, 0.03, 300, "Z-Score"))
        mgr.emit(Insight("BTC", "LONG", 0.8, 0.02, 300, "ROC"))
        
        # Low confidence with 1 source
        mgr.emit(Insight("ETH", "SHORT", 0.3, -0.005, 300, "Z-Score"))
        
        signals = mgr.get_ranked_signals({"BTC", "ETH"})
        assert len(signals) == 2
        assert signals[0].coin == "BTC"  # Higher strength first
        assert signals[0].strength > signals[1].strength

    def test_clear_coin_removes_all_insights(self):
        """Clearing a coin should remove all its insights."""
        from backend.services.insight import InsightManager, Insight
        
        mgr = InsightManager()
        mgr.emit(Insight("BTC", "LONG", 0.9, 0.03, 300, "Z-Score"))
        mgr.emit(Insight("BTC", "LONG", 0.7, 0.02, 300, "ROC"))
        
        mgr.clear_coin("BTC")
        assert mgr.get_active_insights("BTC") == []

    def test_flat_consensus(self):
        """FLAT-only insights should produce FLAT consensus."""
        from backend.services.insight import InsightManager, Insight
        
        mgr = InsightManager()
        mgr.emit(Insight("BTC", "FLAT", 0.8, 0.0, 300, "Z-Score"))
        
        consensus = mgr.get_consensus("BTC")
        assert consensus is not None
        assert consensus.direction == "FLAT"


# ===========================================================================
# Component 3: Dynamic Slippage Model Tests
# ===========================================================================

class TestDynamicSlippage:
    """Tests for the Lean VolumeShareSlippage-inspired model."""

    def test_slippage_has_floor_and_cap(self):
        """Slippage should be bounded between 0.05% and 1.5%."""
        from backend.services.execution_algos import ExecutionAlgos
        
        algos = ExecutionAlgos()
        state = {"mid": 60000.0, "best_bid": 59990.0, "best_ask": 60010.0}
        
        slippage = algos.calculate_dynamic_slippage("BTC", 0.001, True, state)
        
        assert 0.0005 <= slippage <= 0.015, f"Slippage {slippage} outside bounds"

    def test_wider_spread_increases_slippage(self):
        """Wider bid-ask spread should produce higher slippage."""
        from backend.services.execution_algos import ExecutionAlgos
        
        algos = ExecutionAlgos()
        
        # Tight spread
        tight_state = {"mid": 60000.0, "best_bid": 59999.0, "best_ask": 60001.0}
        tight_slippage = algos.calculate_dynamic_slippage("BTC", 0.001, True, tight_state)
        
        # Wide spread
        wide_state = {"mid": 60000.0, "best_bid": 59950.0, "best_ask": 60050.0}
        wide_slippage = algos.calculate_dynamic_slippage("BTC", 0.001, True, wide_state)
        
        assert wide_slippage > tight_slippage, "Wider spread should mean more slippage"

    def test_larger_orders_increase_slippage(self):
        """Larger order sizes should produce higher slippage (market impact)."""
        from backend.services.execution_algos import ExecutionAlgos
        
        algos = ExecutionAlgos()
        state = {"mid": 60000.0, "best_bid": 59990.0, "best_ask": 60010.0}
        
        small_slippage = algos.calculate_dynamic_slippage("BTC", 0.001, True, state)
        large_slippage = algos.calculate_dynamic_slippage("BTC", 1.0, True, state)
        
        assert large_slippage > small_slippage, "Larger order should have more slippage"

    def test_exec_price_buy_is_higher(self):
        """Buy execution price should be higher than mid (paying spread)."""
        from backend.services.execution_algos import ExecutionAlgos
        
        algos = ExecutionAlgos()
        exec_price = algos.compute_exec_price(60000.0, is_buy=True, slippage=0.003)
        assert exec_price > 60000.0

    def test_exec_price_sell_is_lower(self):
        """Sell execution price should be lower than mid (paying spread)."""
        from backend.services.execution_algos import ExecutionAlgos
        
        algos = ExecutionAlgos()
        exec_price = algos.compute_exec_price(60000.0, is_buy=False, slippage=0.003)
        assert exec_price < 60000.0

    def test_fallback_slippage_no_data(self):
        """With zero mid price, should fallback to 0.5%."""
        from backend.services.execution_algos import ExecutionAlgos
        
        algos = ExecutionAlgos()
        state = {"mid": 0.0}
        slippage = algos.calculate_dynamic_slippage("BTC", 0.001, True, state)
        assert slippage == 0.005


# ===========================================================================
# Component 4: Stacked Risk Pipeline Tests
# ===========================================================================

class TestStackedRiskPipeline:
    """Tests for the sequential risk gatekeepers."""

    @pytest.mark.asyncio
    async def test_confidence_modulates_position_size(self):
        """Higher confidence should produce larger position size."""
        from backend.services.risk_manager import RiskManager
        
        rm = RiskManager()
        rm.daily_starting_equity = 10000.0
        rm.last_sync_time = time.time()
        
        mock_user_state = {
            "crossMarginSummary": {"accountValue": "10000.0", "totalMarginUsed": "0.0"},
            "marginSummary": {"accountValue": "10000.0", "totalMarginUsed": "0.0"},
            "assetPositions": [],
        }
        
        with patch("backend.services.risk_manager.hl_client") as mock_hl:
            mock_hl.get_user_state = AsyncMock(return_value=mock_user_state)
            mock_hl.get_positions = AsyncMock(return_value=[])
            mock_hl.is_active = True
            mock_hl.cancel_all_orders = AsyncMock(return_value=True)
            
            now_ms = int(time.time() * 1000)
            
            # High confidence
            _, _, size_high = await rm.evaluate_order("BTC", "LONG", 60000.0, now_ms, confidence=0.95)
            
            # Low confidence
            _, _, size_low = await rm.evaluate_order("BTC", "LONG", 60000.0, now_ms, confidence=0.35)
            
            assert size_high > size_low, f"High confidence size {size_high} should exceed low {size_low}"

    @pytest.mark.asyncio
    async def test_per_coin_exposure_blocks_overconcentration(self):
        """Should block entry if coin already uses > 30% of portfolio."""
        from backend.services.risk_manager import RiskManager
        
        rm = RiskManager()
        rm.daily_starting_equity = 10000.0
        rm.last_sync_time = time.time()
        
        mock_user_state = {
            "crossMarginSummary": {"accountValue": "10000.0", "totalMarginUsed": "1000.0"},
            "marginSummary": {"accountValue": "10000.0", "totalMarginUsed": "1000.0"},
            "assetPositions": [],
        }
        
        # Existing position worth 35% of portfolio
        existing_positions = [{"coin": "BTC", "szi": "0.06", "entryPx": "60000.0"}]
        
        with patch("backend.services.risk_manager.hl_client") as mock_hl:
            mock_hl.get_user_state = AsyncMock(return_value=mock_user_state)
            mock_hl.get_positions = AsyncMock(return_value=existing_positions)
            mock_hl.is_active = True
            
            ok, reason = await rm._check_per_coin_exposure("BTC", 60000.0, 10000.0)
            assert ok == False, "Should block: 0.06 * 60000 = $3600 > 30% of $10000"
            assert "Per-coin exposure" in reason

    @pytest.mark.asyncio
    async def test_per_coin_exposure_allows_new_coin(self):
        """Should allow entry for a coin with no existing position."""
        from backend.services.risk_manager import RiskManager
        
        rm = RiskManager()
        
        with patch("backend.services.risk_manager.hl_client") as mock_hl:
            mock_hl.get_positions = AsyncMock(return_value=[])
            
            ok, reason = await rm._check_per_coin_exposure("ETH", 3500.0, 10000.0)
            assert ok == True

    @pytest.mark.asyncio
    async def test_correlation_guard_reduces_for_correlated(self):
        """Should return 0.5 multiplier for highly correlated positions."""
        from backend.services.risk_manager import RiskManager
        
        rm = RiskManager()
        
        with patch("backend.services.trailing_stop.trailing_stop_manager") as mock_tsm:
            from backend.services.trailing_stop import TrackedPosition
            mock_tsm.positions = {"ETH": MagicMock(spec=TrackedPosition)}
            
            with patch("backend.services.pairs_scanner.scanner") as mock_ps:
                mock_ps.get_rankings.return_value = [
                    {"pair": "BTC_ETH", "correlation": 0.85}
                ]
                
                multiplier = await rm._correlation_guard("BTC")
                assert multiplier == 0.5, "Should reduce by 50% for r > 0.80"

    @pytest.mark.asyncio
    async def test_correlation_guard_no_reduction_for_uncorrelated(self):
        """Should return 1.0 for uncorrelated positions."""
        from backend.services.risk_manager import RiskManager
        
        rm = RiskManager()
        
        with patch("backend.services.trailing_stop.trailing_stop_manager") as mock_tsm:
            from backend.services.trailing_stop import TrackedPosition
            mock_tsm.positions = {"SOL": MagicMock(spec=TrackedPosition)}
            
            with patch("backend.services.pairs_scanner.scanner") as mock_ps:
                mock_ps.get_rankings.return_value = [
                    {"pair": "BTC_SOL", "correlation": 0.30}
                ]
                
                multiplier = await rm._correlation_guard("BTC")
                assert multiplier == 1.0, "No reduction for low correlation"


# ===========================================================================
# Integration: Trailing Stop SHORT Tests
# ===========================================================================

class TestTrailingStopShort:
    """Tests for SHORT position trailing stop behavior."""

    def test_short_stop_trails_downward(self):
        """SHORT trailing stop should trail down as price drops."""
        from backend.services.trailing_stop import TrailingStopManager
        
        mgr = TrailingStopManager()
        mgr.register_position("ETH", "SHORT", 3500.0, 1.0, trailing_stop_pct=0.05)
        
        initial_stop = mgr.positions["ETH"].current_stop_price
        assert initial_stop == 3500.0 * 1.05  # 5% above entry
        
        # Price drops to 3200 (profit for short)
        mgr.update_prices({"ETH": 3200.0})
        new_stop = mgr.positions["ETH"].current_stop_price
        
        assert new_stop < initial_stop, "Stop should trail downward for SHORT"
        assert new_stop == 3200.0 * 1.05

    def test_short_stop_triggers_on_rise(self):
        """SHORT should be stopped out when price rises above stop."""
        from backend.services.trailing_stop import TrailingStopManager
        
        mgr = TrailingStopManager()
        mgr.register_position("ETH", "SHORT", 3500.0, 1.0, trailing_stop_pct=0.05)
        
        # Price rises above stop (3500 * 1.05 = 3675)
        exits = mgr.check_exits({"ETH": 3700.0})
        
        assert len(exits) == 1
        assert exits[0]["reason"] == "TRAILING_STOP"
        assert exits[0]["pnl_pct"] < 0  # Negative PnL for short when price rises

    def test_no_exit_when_price_within_range(self):
        """No exit should trigger when price is within acceptable range."""
        from backend.services.trailing_stop import TrailingStopManager
        
        mgr = TrailingStopManager()
        mgr.register_position("BTC", "LONG", 60000.0, 0.1, 
                             trailing_stop_pct=0.05, take_profit_pct=0.10,
                             max_hold_seconds=99999)
        
        # Price is within range (no stop, no TP)
        mgr.update_prices({"BTC": 62000.0})
        exits = mgr.check_exits({"BTC": 62000.0})
        
        assert len(exits) == 0, "No exit should trigger within range"
