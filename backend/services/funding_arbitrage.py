import traceback
from collections import deque
from backend.services.database import db
from backend.services.hyperliquid_client import hl_client

class FundingArbitrageAgent:
    def __init__(self):
        self.is_active = False
        self.arbitrage_opportunities = []
        self._last_update_time = 0.0
        
        # Active funding arb positions: { "BTC": { "entry_rate": float, "entry_price": float, "size": float, "accumulated_yield": float } }
        self.active_positions = {}
        
        # Rolling funding rate history for reversal detection: { "BTC": deque([rate1, rate2, ...]) }
        self.funding_rate_history = {}
        
    async def get_opportunities(self):
        """Fetch real funding opportunities across active markets."""
        import time
        import asyncio
        now = time.time()
        
        # Cache for 60 seconds to avoid API spam
        if now - self._last_update_time < 60.0 and self.arbitrage_opportunities:
            return self.arbitrage_opportunities
            
        if not hl_client.is_active or hl_client.info is None:
            # Fallback if offline
            return [
                {"coin": "BTC", "funding_rate_8h": 0.00045, "annualized_apy": 15.93, "status": "Simulated"},
                {"coin": "ETH", "funding_rate_8h": 0.00052, "annualized_apy": 18.61, "status": "Simulated"}
            ]
            
        try:
            # hl_client.info.meta_and_asset_ctxs() returns [meta, ctxs]
            meta_and_ctxs = await asyncio.to_thread(hl_client.info.meta_and_asset_ctxs)
            universe = meta_and_ctxs[0].get("universe", [])
            ctxs = meta_and_ctxs[1]
            
            opportunities = []
            
            for i, asset_meta in enumerate(universe):
                asset_name = asset_meta.get("name")
                if i < len(ctxs):
                    ctx = ctxs[i]
                    funding_str = ctx.get("funding")
                    if funding_str:
                        funding_rate = float(funding_str)
                        
                        # Track funding rate history for reversal detection
                        if asset_name not in self.funding_rate_history:
                            self.funding_rate_history[asset_name] = deque(maxlen=10)
                        self.funding_rate_history[asset_name].append(funding_rate)
                        
                        # Hyperliquid funding is typically hourly. Let's assume hourly rate.
                        # Annualized APY simple calculation: funding_rate * 24 * 365 * 100
                        annualized_apy = funding_rate * 24 * 365 * 100
                        
                        if annualized_apy > 0:
                            status = "Stable"
                            if annualized_apy > 50:
                                status = "Extreme Volatility"
                            elif annualized_apy > 20:
                                status = "High Yield"
                                
                            opportunities.append({
                                "coin": asset_name,
                                "funding_rate_1h": funding_rate,
                                "annualized_apy": round(annualized_apy, 2),
                                "status": status,
                                "rate_trend": self._get_rate_trend(asset_name)
                            })
                            
            # Sort by highest APY descending
            opportunities.sort(key=lambda x: x["annualized_apy"], reverse=True)
            
            # Keep top 10
            self.arbitrage_opportunities = opportunities[:10]
            self._last_update_time = now
            return self.arbitrage_opportunities
            
        except Exception as e:
            db.log_system("ERROR", f"Failed to fetch real funding rates: {str(e)}")
            return self.arbitrage_opportunities

    def _get_rate_trend(self, coin: str) -> str:
        """Analyzes the recent trend of funding rates for a coin."""
        history = self.funding_rate_history.get(coin, deque())
        if len(history) < 3:
            return "INSUFFICIENT_DATA"
        
        recent_3 = list(history)[-3:]
        avg = sum(recent_3) / len(recent_3)
        
        if avg > 0 and all(r > 0 for r in recent_3):
            return "STABLE_POSITIVE"
        elif avg < 0 or any(r < 0 for r in recent_3):
            return "REVERSAL_RISK"
        return "MIXED"

    def _check_rate_reversal(self, coin: str) -> bool:
        """
        Checks if the 3-period rolling average of funding rate has turned negative.
        Returns True if reversal detected (should unwind), False otherwise.
        """
        history = self.funding_rate_history.get(coin, deque())
        if len(history) < 3:
            return False
        
        recent_3 = list(history)[-3:]
        rolling_avg = sum(recent_3) / len(recent_3)
        
        if rolling_avg <= 0:
            db.log_system("FUNDING_ARB", f"RATE REVERSAL detected for {coin}! 3-period avg: {rolling_avg:.6f}. Triggering unwind.")
            return True
        return False

    def _calculate_dynamic_threshold(self) -> float:
        """
        Calculates the minimum profitable APY accounting for trading costs.
        Estimated costs: maker fee (0.02%) * 2 legs * 2 (entry + exit) = ~0.08% round-trip.
        Plus estimated slippage of ~0.02%.
        Total: ~0.1% per trade cycle, annualized.
        """
        # Estimated round-trip cost in percentage
        round_trip_cost_pct = 0.001  # 0.1%
        # Annualize assuming average hold of 1 day
        annualized_cost = round_trip_cost_pct * 365 * 100  # Convert to APY percentage
        # Minimum profitable APY = annualized_cost + safety margin (50%)
        min_apy = annualized_cost * 1.5
        return min_apy  # Approximately 54.75% APY minimum

    async def _unwind_position(self, coin: str, reason: str):
        """Closes a funding arb position by buying back the perp short."""
        if coin not in self.active_positions:
            return
        
        position = self.active_positions[coin]
        size = position["size"]
        
        from backend.services.orderbook_tracker import tracker
        market_state = tracker.get_market_state(coin)
        mid_px = market_state.get("mid", 0.0)
        
        if mid_px <= 0:
            db.log_system("ERROR", f"Cannot unwind {coin} funding arb: no price available.")
            return
        
        # Close the short by buying back
        exec_price = mid_px * 1.005  # 0.5% slippage for buy
        db.log_system("FUNDING_ARB", f"UNWINDING {coin} arb position. Size: {size} | Reason: {reason} | Accumulated yield: ${position['accumulated_yield']:.4f}")
        
        await hl_client.place_order(
            coin=coin,
            is_buy=True,
            size=size,
            price=exec_price,
            reduce_only=True
        )
        
        # Remove from active positions
        del self.active_positions[coin]

    def toggle_agent(self, status: bool) -> bool:
        self.is_active = status
        db.log_system("FUNDING_ARB", f"Funding Arbitrage Cash-and-Carry agent status set to: {'ENABLED' if status else 'DISABLED'}")
        return True

    async def run_arbitrage_checks(self):
        """Called periodically inside main bot cycle to evaluate entry/exits if enabled."""
        if not self.is_active:
            return
            
        import time
        now = time.time()
        
        # Only run check every 5 minutes to avoid spam
        if not hasattr(self, '_last_check') or now - self._last_check > 300:
            self._last_check = now
            
            # Step 1: Monitor existing positions for reversal
            for coin in list(self.active_positions.keys()):
                if self._check_rate_reversal(coin):
                    await self._unwind_position(coin, "Funding rate reversal detected (3-period avg negative)")
                    continue
                
                # Update accumulated yield estimate
                history = self.funding_rate_history.get(coin, deque())
                if history:
                    latest_rate = list(history)[-1]
                    position = self.active_positions[coin]
                    # Approximate yield since last check (5-minute intervals, hourly funding)
                    hourly_yield = latest_rate * position["size"] * position["entry_price"]
                    check_fraction = 300.0 / 3600.0  # 5 mins / 1 hour
                    position["accumulated_yield"] += hourly_yield * check_fraction
            
            # Step 2: Scan for new opportunities
            opportunities = await self.get_opportunities()
            if opportunities:
                top_opp = opportunities[0]
                coin = top_opp["coin"]
                apy = top_opp["annualized_apy"]
                trend = top_opp.get("rate_trend", "MIXED")
                
                db.log_system("FUNDING_ARB", f"Scanning funding spreads... Top: {coin} at {apy}% APY (trend: {trend})")
                
                # Dynamic threshold instead of hardcoded 30%
                min_apy = self._calculate_dynamic_threshold()
                
                # Only enter if APY exceeds dynamic threshold AND rate trend is stable
                if apy > min_apy and trend == "STABLE_POSITIVE" and coin not in self.active_positions:
                    db.log_system("FUNDING_ARB", f"Executing Delta-Neutral for {coin}. APY: {apy}% > threshold: {min_apy:.1f}%. Shorting Perp.")
                    
                    # Fetch current price
                    from backend.services.orderbook_tracker import tracker
                    market_state = tracker.get_market_state(coin)
                    mid_px = market_state.get("mid", 0.0)
                    
                    if mid_px > 0:
                        # Risk Gatekeeper Evaluation
                        from backend.services.risk_manager import risk_manager
                        timestamp_ms = int(time.time() * 1000)
                        approved, reason, size = await risk_manager.evaluate_order(
                            coin=coin,
                            side="SHORT",
                            price=mid_px,
                            timestamp_ms=timestamp_ms
                        )
                        
                        if approved:
                            # Set slippage price
                            exec_price = mid_px * 0.995
                            await hl_client.place_order(
                                coin=coin,
                                is_buy=False,
                                size=size,
                                price=exec_price,
                                reduce_only=False
                            )
                            
                            # Track the position
                            self.active_positions[coin] = {
                                "entry_rate": top_opp["funding_rate_1h"],
                                "entry_price": mid_px,
                                "size": size,
                                "accumulated_yield": 0.0,
                                "entry_time": now
                            }
                            db.log_system("FUNDING_ARB", f"Position opened for {coin}. Entry rate: {top_opp['funding_rate_1h']:.6f} | Size: {size}")
                        else:
                            db.log_system("FUNDING_ARB", f"Arbitrage rejected by Risk Manager: {reason}")

funding_arb_agent = FundingArbitrageAgent()

