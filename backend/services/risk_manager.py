import time
import math
import numpy as np
from backend.config import Config
from backend.services.database import db
from backend.services.hyperliquid_client import hl_client

class RiskManager:
    def __init__(self):
        self.daily_drawdown_limit = Config.DAILY_DRAWDOWN_LIMIT_PCT
        self.max_exposure_pct = Config.MAX_EXPOSURE_PCT
        self.risk_per_trade_pct = Config.RISK_PER_TRADE_PCT
        self.latency_limit_ms = Config.CIRCUIT_BREAKER_LATENCY_MS
        
        # Internal state to manage circuit breakers
        self.is_halted = False
        self.daily_starting_equity = 7.42 # Will fetch from user state dynamically
        self.last_sync_time = 0.0
        
        # Cached account state for margin pre-checks (updated during sync_equity)
        self._cached_account_value = 0.0
        self._cached_margin_used = 0.0
        self._cached_leverage = 50  # Default for micro accounts

    async def sync_equity(self):
        """Fetches latest wallet equity. Only resets daily_starting_equity at true day boundaries (midnight UTC)."""
        now = time.time()
        # Sync every 10 minutes
        if now - self.last_sync_time > 600:
            user_state = await hl_client.get_user_state()
            if user_state and "marginSummary" in user_state:
                summary = user_state["marginSummary"]
                current_equity = float(summary.get("accountValue", 7.42))
                # Guard against zero equity from API (funds may be in spot wallet)
                if current_equity <= 0:
                    current_equity = max(self.daily_starting_equity, 7.42)

                # Only reset daily_starting_equity at actual day boundaries or on first sync
                from datetime import datetime, timezone
                current_date = datetime.now(timezone.utc).date()
                if not hasattr(self, '_last_equity_date') or self._last_equity_date != current_date:
                    self.daily_starting_equity = current_equity
                    self._last_equity_date = current_date
                    db.log_system("RISK", f"New trading day detected. Daily Starting Equity set to: ${self.daily_starting_equity:.2f}")

                self.last_sync_time = now
                db.log_system("RISK", f"Equity sync completed. Current: ${current_equity:.2f} | Daily Start: ${self.daily_starting_equity:.2f}")

    def get_free_margin(self, account_value: float, margin_used: float) -> float:
        """Calculates the free (available) margin from account value and margin used.
        Returns: Free margin amount in USD. Can be negative if underwater.
        """
        return account_value - margin_used

    def estimate_required_margin(self, notional_value: float, leverage: int) -> float:
        """Estimates the margin that will be locked for a given order.
        Args:
            notional_value: Total notional value of the order in USD (size * price)
            leverage: Active leverage multiplier for the coin
        Returns: Estimated margin requirement in USD.
        """
        if leverage <= 0:
            leverage = 1
        return notional_value / leverage

    def get_asset_sz_decimals(self, coin: str) -> tuple[int, float]:
        """Gets the size decimal precision and minimum step size for a coin.
        Uses dynamic metadata from UniverseManager if available, falls back to hardcoded defaults.
        Returns: Tuple (sz_decimals, min_step_size)
        """
        # Try dynamic lookup from cached universe metadata first
        from backend.services.universe_manager import universe_manager
        meta = universe_manager.get_asset_metadata(coin)
        if meta:
            return (meta["sz_decimals"], meta["min_step"])
        
        # Hardcoded fallback for when metadata hasn't been fetched yet (first few cycles)
        fallback_specs = {
            "BTC": (5, 0.00001),
            "ETH": (4, 0.0001),
            "DOGE": (0, 1.0),
            "SUI": (1, 0.1),
            "SOL": (2, 0.01),
            "NEAR": (1, 0.1),
            "AVAX": (2, 0.01),
            "HYPE": (2, 0.01),
            "WLD": (1, 0.1),
            "XRP": (0, 1.0),
            "INJ": (1, 0.1),
            "LINK": (1, 0.1),
            "ARB": (1, 0.1),
            "BNB": (3, 0.001),
            "TAO": (3, 0.001),
        }
        return fallback_specs.get(coin, (2, 0.01))

    async def evaluate_order(self, coin: str, side: str, price: float, timestamp_ms: float, confidence: float = 1.0) -> tuple[bool, str, float]:
        """
        Stacked Risk Management Pipeline (Lean-inspired).
        
        Validates an order through sequential gatekeepers. Each gate can reject
        or adjust the order. The output of gate N feeds into gate N+1.
        
        Pipeline:
        1. Circuit Breaker (halt check)
        2. Stale Signal Check (latency)
        3. Daily Drawdown Guard
        4. Max Exposure Per Coin (NEW — no single coin > 30%)
        5. Correlation Guard (NEW — reduce size for correlated positions)
        6. Kelly Sizing with confidence modulation
        7. Margin Pre-Check
        
        Args:
            confidence: Insight confidence (0.0-1.0) from the InsightManager.
                       Used to modulate position size — high confidence = larger position.
        
        Returns: Tuple (is_approved: bool, reason: str, calculated_size: float)
        """
        # 1. Halted Circuit Breaker Check
        if self.is_halted:
            return False, "Bot is currently HALTED due to risk limit breach", 0.0
            
        # 2. Latency / Stale Signal Check
        current_time_ms = int(time.time() * 1000)
        latency = current_time_ms - timestamp_ms
        if latency > self.latency_limit_ms:
            return False, f"Signal rejected: Stale signal latency is {latency}ms (Limit is {self.latency_limit_ms}ms)", 0.0

        # Sync equity balances
        await self.sync_equity()

        # Fetch live state
        user_state = await hl_client.get_user_state()
        if not user_state:
            return False, "Failed to fetch user state from Hyperliquid", 0.0
            
        # Check both cross and isolated margin summaries
        cross_summary = user_state.get("crossMarginSummary", {})
        isolated_summary = user_state.get("marginSummary", {})
        
        cross_val = float(cross_summary.get("accountValue", 0.0))
        isolated_val = float(isolated_summary.get("accountValue", 0.0))
        
        if cross_val > 0 or isolated_val == 0:
            margin_summary = cross_summary
        else:
            margin_summary = isolated_summary
            
        account_value = float(margin_summary.get("accountValue", 7.42))
        margin_used = float(margin_summary.get("totalMarginUsed", 0.0))
        
        # Guard: If API returns 0 for account value, use daily_starting_equity fallback
        if account_value <= 0:
            account_value = max(self.daily_starting_equity, 7.42)
            db.log_system("RISK", f"Account value reported as 0. Using fallback equity: ${account_value:.2f}")
        
        # Cache these values for external pre-check queries
        self._cached_account_value = account_value
        self._cached_margin_used = margin_used
        
        # Determine active leverage for this account tier
        if account_value < 50.0:
            active_leverage = 50
        elif account_value < 500.0:
            active_leverage = 20
        else:
            active_leverage = 5
        self._cached_leverage = active_leverage
        
        # 3. Daily Drawdown Circuit Breaker
        if self.daily_starting_equity > 0:
            current_drawdown = (account_value - self.daily_starting_equity) / self.daily_starting_equity
            if current_drawdown <= -self.daily_drawdown_limit:
                self.is_halted = True
                await hl_client.cancel_all_orders()
                db.log_system("CRITICAL", f"DAILY DRAWDOWN LIMIT BREACHED: {current_drawdown*100:.2f}%. Activating emergency HALT.")
                return False, "Daily drawdown limit breached, circuit breaker activated!", 0.0
            
        # 4. Max Exposure Check
        # Adjust max exposure limit dynamically for small accounts to allow meeting L1 minimums
        active_max_exposure = self.max_exposure_pct
        if account_value < 50.0:
            active_max_exposure = 0.85 # Allow up to 85% margin usage for micro accounts
            
        # Reject new entries if total margin used exceeds max_exposure of account equity
        is_exit = side == "FLAT"
        if not is_exit and account_value > 0 and (margin_used / account_value) >= active_max_exposure:
            return False, f"Max exposure limit reached! Active Margin Ratio: {margin_used/account_value*100:.2f}% (Limit: {active_max_exposure*100:.1f}%)", 0.0

        # 4b. GATE: Max Exposure Per Coin (Lean-inspired per-security risk limit)
        # Prevents concentration risk — no single coin should use more than 30% of portfolio
        if not is_exit:
            coin_exposure_ok, coin_exposure_reason = await self._check_per_coin_exposure(coin, price, account_value)
            if not coin_exposure_ok:
                return False, coin_exposure_reason, 0.0

        # 4c. GATE: Correlation Guard (Lean-inspired cross-position risk)
        # If the new position is highly correlated with existing positions, reduce size
        correlation_multiplier = 1.0
        if not is_exit:
            correlation_multiplier = await self._correlation_guard(coin)

        if is_exit:
            # Flat/Exit order has no risk limit checks as it reduces risk
            return True, "Exit order approved", 1.0

        # 5. Dynamic Position Sizing (Kelly Criterion with Half-Kelly Cushion)
        # Fetch rolling performance history
        stats = db.get_trade_performance_stats(limit=30)
        total_trades = stats.get("total_trades", 0)
        
        if total_trades >= 5:
            # We have enough history to calculate Kelly parameters
            win_rate = stats["win_rate"]
            payoff_ratio = stats["payoff_ratio"]
            
            # Kelly Formula: f = W - (1-W)/R
            kelly_f = win_rate - ((1.0 - win_rate) / payoff_ratio if payoff_ratio > 0 else 0.0)
            
            # Apply Half-Kelly for risk conservation/cushioning
            half_kelly = kelly_f * 0.5
            
            # Calculate adaptive uncertainty penalty
            uncertainty = self._calculate_uncertainty_index(stats, account_value)
            
            # Apply uncertainty-adjusted Kelly: shrinks allocation in dangerous conditions
            # When uncertainty = 0 → identical to standard Half-Kelly
            # When uncertainty = 1 → allocation shrinks to floor (0.5%)
            adjusted_kelly = half_kelly * (1.0 - uncertainty)
            
            # NEW: Confidence modulation from Insight system
            # Higher-confidence insights get up to 100% of Kelly allocation
            # Lower-confidence insights get proportionally less
            adjusted_kelly *= max(0.3, confidence)  # Floor at 30% of Kelly even for low confidence
            
            # NEW: Correlation guard reduction
            adjusted_kelly *= correlation_multiplier
            
            # Clamp Kelly sizing between 0.5% (minimum) and Config.MAX_EXPOSURE_PCT (maximum)
            risk_fraction = max(0.005, min(self.max_exposure_pct, adjusted_kelly))
            db.log_system("RISK", 
                f"Kelly: {kelly_f:.4f} | Half: {half_kelly:.4f} | Uncertainty: {uncertainty:.3f} | "
                f"Confidence: {confidence:.2f} | CorrMult: {correlation_multiplier:.2f} | "
                f"Adaptive: {adjusted_kelly:.4f} | Clamped: {risk_fraction*100:.2f}%")
        else:
            # Under-sampled history fallback: Use standard 1% fixed fractional sizing
            risk_fraction = self.risk_per_trade_pct * max(0.3, confidence) * correlation_multiplier
            risk_fraction = max(0.005, risk_fraction)
            db.log_system("RISK", f"Under-sampled history ({total_trades} trades). Fixed fractional alloc: {risk_fraction*100:.2f}% (conf={confidence:.2f})")
            
        # Hyperliquid requires a strict minimum order value of $10 notional.
        # We enforce $11 here to give it a safety buffer.
        risk_amount = account_value * risk_fraction
        risk_amount = max(risk_amount, 11.0)
        
        # Sizing relative to price (position size = risk_amount / price)
        if price <= 0:
            return False, "Invalid price (zero or negative)", 0.0
        calculated_size = risk_amount / price
        
        # Dynamic size rounding using API metadata (or fallback)
        decimals, min_size = self.get_asset_sz_decimals(coin)
        
        # Initial rounded size
        rounded_size = round(calculated_size, decimals)
        if rounded_size < min_size:
            rounded_size = min_size
            
        # Ensure notional value is strictly >= $11.00 to satisfy Hyperliquid's minimum limit
        if rounded_size * price < 11.0:
            required_size = 11.0 / price
            step = 10 ** (-decimals) if decimals > 0 else 1.0
            steps = math.ceil(required_size / step)
            rounded_size = round(steps * step, decimals)
        
        # 6. MARGIN PRE-CHECK (NEW)
        # Estimate if the account has enough free margin to support this order
        # This prevents sending doomed orders that the exchange will reject
        notional_value = rounded_size * price
        required_margin = self.estimate_required_margin(notional_value, active_leverage)
        free_margin = self.get_free_margin(account_value, margin_used)
        
        # Apply 20% safety buffer to avoid borderline rejections
        margin_with_buffer = required_margin * 1.2
        
        if free_margin < margin_with_buffer:
            return False, (
                f"Margin pre-check FAILED for {coin}. "
                f"Free margin: ${free_margin:.2f}, Required (with 20% buffer): ${margin_with_buffer:.2f}. "
                f"Notional: ${notional_value:.2f}, Leverage: {active_leverage}x. "
                f"Skipping to avoid exchange rejection."
            ), 0.0
            
        return True, "Order approved by risk gatekeeper", rounded_size

    async def check_margin_feasibility(self, coin: str, price: float, min_notional: float = 11.0) -> tuple[bool, str]:
        """Quick margin feasibility check without full risk evaluation.
        Used by the main loop to pre-filter coins before running full evaluate_order().
        
        Args:
            coin: The coin symbol
            price: Current mid price
            min_notional: Minimum notional value for the order (default $11)
        
        Returns: Tuple (is_feasible: bool, reason: str)
        """
        user_state = await hl_client.get_user_state()
        if not user_state:
            return False, "Cannot fetch user state"
        
        cross_summary = user_state.get("crossMarginSummary", {})
        isolated_summary = user_state.get("marginSummary", {})
        
        cross_val = float(cross_summary.get("accountValue", 0.0))
        isolated_val = float(isolated_summary.get("accountValue", 0.0))
        
        if cross_val > 0 or isolated_val == 0:
            margin_summary = cross_summary
        else:
            margin_summary = isolated_summary
        
        account_value = float(margin_summary.get("accountValue", 0.0))
        margin_used = float(margin_summary.get("totalMarginUsed", 0.0))
        
        if account_value <= 0:
            account_value = max(self.daily_starting_equity, 7.42)
        
        # Determine leverage
        if account_value < 50.0:
            leverage = 50
        elif account_value < 500.0:
            leverage = 20
        else:
            leverage = 5
        
        free_margin = self.get_free_margin(account_value, margin_used)
        required_margin = self.estimate_required_margin(min_notional, leverage)
        margin_with_buffer = required_margin * 1.2
        
        if free_margin < margin_with_buffer:
            return False, (
                f"Insufficient free margin for {coin}: "
                f"${free_margin:.2f} available, ${margin_with_buffer:.2f} needed"
            )
        
        return True, f"Margin feasible for {coin}: ${free_margin:.2f} free, ${margin_with_buffer:.2f} needed"

    async def trigger_emergency_kill(self):
        """Manual activation of kill switch."""
        self.is_halted = True
        await hl_client.cancel_all_orders()
        db.log_system("EMERGENCY", "User manually triggered the global EMERGENCY HALT (Kill Switch). Bot is locked.")
        return True

    def reset_halt(self):
        """Manual release of the halt condition."""
        self.is_halted = False
        db.log_system("RISK", "Risk halt manually released. Trading bot enabled.")
        return True

    def _calculate_uncertainty_index(self, stats: dict, current_equity: float) -> float:
        """
        Calculates an adaptive uncertainty index (0 to 1) that shrinks Kelly allocation
        when market conditions or account state indicate elevated risk.
        
        Combines three orthogonal risk factors:
        1. PnL Volatility: High variance in recent trade outcomes → low confidence in edge estimate
        2. Sample Confidence: Few historical trades → unreliable win-rate estimate (Bayesian shrinkage)
        3. Drawdown Proximity: Closer to daily drawdown limit → defensive sizing
        
        Returns: float between 0.0 (low uncertainty, full Kelly) and 1.0 (max uncertainty, minimum size)
        """
        uncertainty_components = []
        
        # Factor 1: PnL Volatility (normalized coefficient of variation)
        # High PnL variance means the edge is unstable
        pnl_list = self._get_recent_pnls(limit=30)
        if len(pnl_list) >= 5:
            pnl_array = np.array(pnl_list)
            pnl_mean = np.mean(np.abs(pnl_array))
            pnl_std = np.std(pnl_array)
            if pnl_mean > 0:
                # Coefficient of variation: std/mean, clamped to [0, 1]
                cv = min(1.0, pnl_std / pnl_mean)
                uncertainty_components.append(cv * 0.4)  # 40% weight
            else:
                uncertainty_components.append(0.2)  # Moderate uncertainty if no PnL variance
        else:
            uncertainty_components.append(0.3)  # High uncertainty with few samples
        
        # Factor 2: Sample Confidence (1/sqrt(n) Bayesian shrinkage)
        # Fewer trades → less reliable win-rate → more uncertainty
        total_trades = stats.get("total_trades", 0)
        if total_trades > 0:
            sample_uncertainty = min(1.0, 1.0 / math.sqrt(total_trades))
            uncertainty_components.append(sample_uncertainty * 0.3)  # 30% weight
        else:
            uncertainty_components.append(0.3)  # Max sample uncertainty
        
        # Factor 3: Drawdown Proximity
        # As current drawdown approaches the daily limit, uncertainty increases
        if self.daily_starting_equity > 0 and current_equity > 0:
            current_drawdown = (self.daily_starting_equity - current_equity) / self.daily_starting_equity
            current_drawdown = max(0.0, current_drawdown)  # Only positive drawdowns
            drawdown_proximity = min(1.0, current_drawdown / self.daily_drawdown_limit)
            uncertainty_components.append(drawdown_proximity * 0.3)  # 30% weight
        else:
            uncertainty_components.append(0.0)
        
        # Aggregate: sum of weighted components (already weighted above)
        total_uncertainty = min(1.0, sum(uncertainty_components))
        return total_uncertainty

    def _get_recent_pnls(self, limit: int = 30) -> list:
        """Fetches recent trade PnLs from database for uncertainty calculation."""
        try:
            trades = db.get_recent_trades(limit=limit)
            return [float(t.get("pnl", 0.0)) for t in trades if t.get("pnl") is not None and float(t.get("pnl", 0.0)) != 0.0]
        except Exception:
            return []

    async def _check_per_coin_exposure(self, coin: str, price: float, account_value: float) -> tuple[bool, str]:
        """
        GATE: Max Exposure Per Coin (Lean MaximumSectorExposureRiskManagementModel).
        
        Prevents concentration risk by ensuring no single coin's position value
        exceeds MAX_EXPOSURE_PER_COIN_PCT of total portfolio value.
        
        Returns: (is_ok, reason)
        """
        max_coin_exposure = Config.MAX_EXPOSURE_PER_COIN_PCT
        
        # Check existing position for this coin
        positions = await hl_client.get_positions()
        coin_positions = [p for p in positions if p.get("coin") == coin]
        
        if not coin_positions:
            return True, ""  # No existing position, OK
        
        # Calculate current exposure for this coin
        existing_notional = 0.0
        for p in coin_positions:
            pos_size = abs(float(p.get("szi", 0.0)))
            pos_entry = float(p.get("entryPx", price))
            existing_notional += pos_size * pos_entry
        
        coin_exposure_pct = existing_notional / account_value if account_value > 0 else 1.0
        
        if coin_exposure_pct >= max_coin_exposure:
            return False, (
                f"Per-coin exposure limit reached for {coin}. "
                f"Current: {coin_exposure_pct*100:.1f}% (Limit: {max_coin_exposure*100:.1f}%). "
                f"Existing notional: ${existing_notional:.2f}"
            )
        
        return True, ""

    async def _correlation_guard(self, coin: str) -> float:
        """
        GATE: Correlation Guard (Lean stacked risk pattern).
        
        Checks if the new coin is highly correlated (>0.80) with any existing 
        positions. If so, returns a multiplier < 1.0 to reduce position size,
        preventing hidden concentration in correlated assets.
        
        Returns: size_multiplier (1.0 = no reduction, 0.5 = 50% reduction)
        """
        try:
            from backend.services.trailing_stop import trailing_stop_manager
            
            tracked = trailing_stop_manager.positions
            if not tracked:
                return 1.0  # No existing positions, no correlation risk
            
            # Check correlation with each existing position via pairs scanner
            from backend.services.pairs_scanner import scanner as pairs_scanner
            rankings = pairs_scanner.get_rankings()
            
            if not rankings:
                return 1.0  # No correlation data available
            
            for existing_coin in tracked.keys():
                if existing_coin == coin:
                    return 1.0  # Same coin, handled by per-coin exposure check
                
                # Look up correlation between coin and existing_coin
                pair_key_1 = f"{coin}_{existing_coin}"
                pair_key_2 = f"{existing_coin}_{coin}"
                
                for r in rankings:
                    pair = r.get("pair", "")
                    if pair == pair_key_1 or pair == pair_key_2:
                        correlation = abs(r.get("correlation", 0.0))
                        if correlation > 0.80:
                            db.log_system("RISK_CORRELATION",
                                f"High correlation detected: {coin} ↔ {existing_coin} "
                                f"(r={correlation:.3f}). Reducing size by 50%."
                            )
                            return 0.5  # 50% size reduction for correlated entry
            
            return 1.0  # No high correlations found
            
        except Exception:
            return 1.0  # Safe fallback: no reduction on error

# Singleton instance
risk_manager = RiskManager()

