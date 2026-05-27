"""
Trailing Stop Risk Management Module.
Inspired by QuantConnect Lean's TrailingStopRiskManagementModel.

Provides per-position exit protection with:
- Trailing stop loss (tracks peak unrealized PnL, exits on drawdown from peak)
- Take-profit targets (auto-exit at profit threshold)
- Breakeven activation (moves stop to entry price after activation threshold)
- Time-based expiry (auto-exit stale positions)
"""

import time
from dataclasses import dataclass, field
from backend.config import Config
from backend.services.database import db


@dataclass
class TrackedPosition:
    """Represents a single tracked position with exit intelligence metadata."""
    coin: str
    side: str                     # "LONG" or "SHORT"
    entry_price: float
    size: float
    entry_time: float             # Unix timestamp
    
    # Peak tracking for trailing stop
    peak_unrealized_pnl: float = 0.0
    peak_price: float = 0.0       # Highest (LONG) or lowest (SHORT) price since entry
    
    # Exit parameters (set at entry time per Lean pattern)
    trailing_stop_pct: float = 0.05
    take_profit_pct: float = 0.10
    breakeven_activation_pct: float = 0.02
    max_hold_seconds: int = 21600  # 6 hours
    
    # State
    breakeven_activated: bool = False
    current_stop_price: float = 0.0  # Dynamically computed stop level


class TrailingStopManager:
    """
    Per-position trailing stop and exit intelligence manager.
    
    Inspired by Lean's TrailingStopRiskManagementModel, this tracks each
    active position's peak unrealized profit and generates exit signals when:
    1. Drawdown from peak exceeds trailing_stop_pct
    2. Profit reaches take_profit_pct
    3. Position held longer than max_hold_seconds
    
    The key insight from Lean: exit management must be separate from entry
    signals. A position should NEVER rely solely on strategy signals for exits.
    """
    
    def __init__(self):
        self.positions: dict[str, TrackedPosition] = {}
        
    def register_position(
        self,
        coin: str,
        side: str,
        entry_price: float,
        size: float,
        trailing_stop_pct: float = None,
        take_profit_pct: float = None,
        breakeven_activation_pct: float = None,
        max_hold_seconds: int = None
    ) -> None:
        """
        Registers a new position for trailing stop tracking.
        Called immediately after successful trade execution.
        
        Exit parameters default to Config values but can be overridden
        per-position (e.g., higher TP for high-confidence insights).
        """
        pos = TrackedPosition(
            coin=coin,
            side=side,
            entry_price=entry_price,
            size=size,
            entry_time=time.time(),
            peak_price=entry_price,
            trailing_stop_pct=trailing_stop_pct or Config.TRAILING_STOP_PCT,
            take_profit_pct=take_profit_pct or Config.TAKE_PROFIT_PCT,
            breakeven_activation_pct=breakeven_activation_pct or Config.BREAKEVEN_ACTIVATION_PCT,
            max_hold_seconds=max_hold_seconds or Config.MAX_HOLD_SECONDS
        )
        
        # Compute initial stop price
        if side == "LONG":
            pos.current_stop_price = entry_price * (1.0 - pos.trailing_stop_pct)
        else:
            pos.current_stop_price = entry_price * (1.0 + pos.trailing_stop_pct)
        
        self.positions[coin] = pos
        db.log_system("TRAILING_STOP", 
            f"Registered {side} position on {coin} @ ${entry_price:.2f} | "
            f"Stop: ${pos.current_stop_price:.2f} | TP: {pos.take_profit_pct*100:.1f}% | "
            f"MaxHold: {pos.max_hold_seconds}s"
        )
    
    def unregister_position(self, coin: str) -> None:
        """Removes a position from tracking (called after exit execution)."""
        if coin in self.positions:
            del self.positions[coin]
    
    def update_prices(self, current_prices: dict[str, float]) -> None:
        """
        Updates peak tracking for all monitored positions.
        Called every heartbeat cycle with the latest market prices.
        
        This implements the core Lean trailing stop logic:
        - For LONG: track highest price since entry, trail stop below it
        - For SHORT: track lowest price since entry, trail stop above it
        - Activate breakeven when profit reaches activation threshold
        """
        for coin, pos in self.positions.items():
            price = current_prices.get(coin, 0.0)
            if price <= 0.0:
                continue
            
            # Calculate current unrealized PnL percentage
            if pos.side == "LONG":
                pnl_pct = (price - pos.entry_price) / pos.entry_price
            else:
                pnl_pct = (pos.entry_price - price) / pos.entry_price
            
            # Update peak unrealized PnL
            if pnl_pct > pos.peak_unrealized_pnl:
                pos.peak_unrealized_pnl = pnl_pct
            
            # Update peak price (highest for LONG, lowest for SHORT)
            if pos.side == "LONG":
                if price > pos.peak_price:
                    pos.peak_price = price
                    # Trail the stop upward: stop = peak × (1 - trailing_pct)
                    new_stop = pos.peak_price * (1.0 - pos.trailing_stop_pct)
                    pos.current_stop_price = max(pos.current_stop_price, new_stop)
            else:  # SHORT
                if pos.peak_price == 0.0 or price < pos.peak_price:
                    pos.peak_price = price
                    # Trail the stop downward: stop = trough × (1 + trailing_pct)
                    new_stop = pos.peak_price * (1.0 + pos.trailing_stop_pct)
                    pos.current_stop_price = min(pos.current_stop_price, new_stop)
            
            # Breakeven activation: after reaching activation threshold,
            # move stop to entry price (guaranteed no-loss)
            if not pos.breakeven_activated and pnl_pct >= pos.breakeven_activation_pct:
                pos.breakeven_activated = True
                if pos.side == "LONG":
                    pos.current_stop_price = max(pos.current_stop_price, pos.entry_price)
                else:
                    pos.current_stop_price = min(pos.current_stop_price, pos.entry_price)
                db.log_system("TRAILING_STOP",
                    f"Breakeven activated on {coin} | PnL: {pnl_pct*100:.2f}% | "
                    f"Stop moved to entry: ${pos.entry_price:.2f}"
                )
    
    def check_exits(self, current_prices: dict[str, float]) -> list[dict]:
        """
        Checks all tracked positions for exit conditions.
        Returns a list of exit signals with reason and details.
        
        Exit conditions (checked in priority order):
        1. STOP_LOSS: price breached trailing stop level
        2. TAKE_PROFIT: unrealized PnL reached TP target
        3. TIME_EXPIRY: position held longer than max_hold_seconds
        """
        exits = []
        now = time.time()
        
        for coin, pos in list(self.positions.items()):
            price = current_prices.get(coin, 0.0)
            if price <= 0.0:
                continue
            
            # Calculate current PnL
            if pos.side == "LONG":
                pnl_pct = (price - pos.entry_price) / pos.entry_price
            else:
                pnl_pct = (pos.entry_price - price) / pos.entry_price
            
            exit_reason = None
            
            # 1. TRAILING STOP LOSS check
            if pos.side == "LONG" and price <= pos.current_stop_price:
                exit_reason = "TRAILING_STOP"
            elif pos.side == "SHORT" and price >= pos.current_stop_price:
                exit_reason = "TRAILING_STOP"
            
            # 2. TAKE PROFIT check
            if exit_reason is None and pnl_pct >= pos.take_profit_pct:
                exit_reason = "TAKE_PROFIT"
            
            # 3. TIME EXPIRY check
            hold_duration = now - pos.entry_time
            if exit_reason is None and hold_duration >= pos.max_hold_seconds:
                exit_reason = "TIME_EXPIRY"
            
            if exit_reason:
                exits.append({
                    "coin": coin,
                    "side": pos.side,
                    "size": pos.size,
                    "entry_price": pos.entry_price,
                    "current_price": price,
                    "pnl_pct": round(pnl_pct * 100, 2),
                    "reason": exit_reason,
                    "hold_seconds": round(hold_duration),
                    "peak_pnl_pct": round(pos.peak_unrealized_pnl * 100, 2),
                    "stop_price": pos.current_stop_price
                })
                
                db.log_system("TRAILING_STOP",
                    f"EXIT TRIGGERED on {coin} | Reason: {exit_reason} | "
                    f"Side: {pos.side} | PnL: {pnl_pct*100:.2f}% | "
                    f"Peak PnL: {pos.peak_unrealized_pnl*100:.2f}% | "
                    f"Hold: {hold_duration:.0f}s | Stop: ${pos.current_stop_price:.2f}"
                )
        
        return exits
    
    def get_tracked_positions(self) -> list[dict]:
        """Returns status of all tracked positions for dashboard display."""
        result = []
        now = time.time()
        for coin, pos in self.positions.items():
            result.append({
                "coin": coin,
                "side": pos.side,
                "entry_price": pos.entry_price,
                "size": pos.size,
                "peak_price": pos.peak_price,
                "peak_pnl_pct": round(pos.peak_unrealized_pnl * 100, 2),
                "current_stop_price": pos.current_stop_price,
                "breakeven_activated": pos.breakeven_activated,
                "trailing_stop_pct": pos.trailing_stop_pct,
                "take_profit_pct": pos.take_profit_pct,
                "hold_seconds": round(now - pos.entry_time),
                "max_hold_seconds": pos.max_hold_seconds
            })
        return result
    
    def has_position(self, coin: str) -> bool:
        """Returns True if a position is currently tracked for this coin."""
        return coin in self.positions


trailing_stop_manager = TrailingStopManager()
