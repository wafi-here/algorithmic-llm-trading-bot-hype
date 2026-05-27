import time
import math
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

    async def evaluate_order(self, coin: str, side: str, price: float, timestamp_ms: float) -> tuple[bool, str, float]:
        """
        Validates whether an order complies with the strict risk boundaries.
        Includes margin pre-check to avoid sending doomed orders to the exchange.
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
            
            # Clamp Kelly sizing between 0.5% (minimum) and Config.MAX_EXPOSURE_PCT (maximum)
            risk_fraction = max(0.005, min(self.max_exposure_pct, half_kelly))
            db.log_system("RISK", f"Kelly calculated fraction: {kelly_f:.4f} (Half-Kelly: {half_kelly:.4f}). Clamped Alloc: {risk_fraction*100:.2f}%")
        else:
            # Under-sampled history fallback: Use standard 1% fixed fractional sizing
            risk_fraction = self.risk_per_trade_pct
            db.log_system("RISK", f"Under-sampled history ({total_trades} trades). Using default fixed fractional alloc: {risk_fraction*100:.2f}%")
            
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

# Singleton instance
risk_manager = RiskManager()

