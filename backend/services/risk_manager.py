import time
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
        self.daily_starting_equity = 10000.0 # Will fetch from user state dynamically
        self.last_sync_time = 0.0

    def sync_equity(self):
        """Fetches latest wallet equity and resets starting equity if daily cycle has passed."""
        now = time.time()
        # Sync every 10 minutes
        if now - self.last_sync_time > 600:
            user_state = hl_client.get_user_state()
            if user_state and "marginSummary" in user_state:
                summary = user_state["marginSummary"]
                current_equity = float(summary.get("accountValue", 10000.0))
                self.daily_starting_equity = current_equity
                self.last_sync_time = now
                db.log_system("RISK", f"Synced Starting Equity: ${self.daily_starting_equity:.2f}")

    def evaluate_order(self, coin: str, side: str, price: float, timestamp_ms: float) -> tuple[bool, str, float]:
        """
        Validates whether an order complies with the strict risk boundaries.
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
        self.sync_equity()

        # Fetch live state
        user_state = hl_client.get_user_state()
        if not user_state:
            return False, "Failed to fetch user state from Hyperliquid", 0.0
            
        margin_summary = user_state.get("marginSummary", {})
        account_value = float(margin_summary.get("accountValue", 10000.0))
        margin_used = float(margin_summary.get("totalMarginUsed", 0.0))
        
        # 3. Daily Drawdown Circuit Breaker
        current_drawdown = (account_value - self.daily_starting_equity) / self.daily_starting_equity
        if current_drawdown <= -self.daily_drawdown_limit:
            self.is_halted = True
            hl_client.cancel_all_orders()
            db.log_system("CRITICAL", f"DAILY DRAWDOWN LIMIT BREACHED: {current_drawdown*100:.2f}%. Activating emergency HALT.")
            return False, "Daily drawdown limit breached, circuit breaker activated!", 0.0
            
        # 4. Max Exposure Check
        # Reject new entries if total margin used exceeds 20% of account equity
        is_exit = side == "FLAT"
        if not is_exit and (margin_used / account_value) >= self.max_exposure_pct:
            return False, f"Max exposure limit reached! Active Margin Ratio: {margin_used/account_value*100:.2f}%", 0.0

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
            
        risk_amount = account_value * risk_fraction
        
        # Sizing relative to price (position size = risk_amount / price)
        calculated_size = risk_amount / price
        
        # Establish sensible asset size boundaries
        if coin == "BTC":
            calculated_size = max(0.0001, round(calculated_size, 4))
        elif coin == "ETH":
            calculated_size = max(0.001, round(calculated_size, 3))
        else:
            calculated_size = max(0.01, round(calculated_size, 2))
            
        return True, "Order approved by risk gatekeeper", calculated_size

    def trigger_emergency_kill(self):
        """Manual activation of kill switch."""
        self.is_halted = True
        hl_client.cancel_all_orders()
        db.log_system("EMERGENCY", "User manually triggered the global EMERGENCY HALT (Kill Switch). Bot is locked.")
        return True

    def reset_halt(self):
        """Manual release of the halt condition."""
        self.is_halted = False
        db.log_system("RISK", "Risk halt manually released. Trading bot enabled.")
        return True

# Singleton instance
risk_manager = RiskManager()
