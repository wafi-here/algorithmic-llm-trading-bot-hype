import traceback
from backend.services.database import db
from backend.services.hyperliquid_client import hl_client

class FundingArbitrageAgent:
    def __init__(self):
        self.is_active = False
        self.arbitrage_opportunities = []
        self._last_update_time = 0.0
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
                                "status": status
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
            
            opportunities = await self.get_opportunities()
            if opportunities:
                top_opp = opportunities[0]
                coin = top_opp["coin"]
                apy = top_opp["annualized_apy"]
                
                db.log_system("FUNDING_ARB", f"Scanning funding spreads... Top opportunity: {coin} at {apy}% APY")
                
                if apy > 30.0:  # Threshold to execute
                    # Execute Delta-Neutral trade (Simulate Spot, Real Perp Short)
                    db.log_system("FUNDING_ARB", f"Executing Delta-Neutral Cash & Carry for {coin}. Buying Spot (Simulated), Shorting Perp (Live).")
                    
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
                        else:
                            db.log_system("FUNDING_ARB", f"Arbitrage rejected by Risk Manager: {reason}")

funding_arb_agent = FundingArbitrageAgent()
