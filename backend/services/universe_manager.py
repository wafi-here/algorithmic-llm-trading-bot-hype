import time
from backend.services.database import db
from backend.services.hyperliquid_client import hl_client

class UniverseManagerService:
    def __init__(self):
        self.active_universe = ["BTC", "ETH", "SOL"] # Fallback defaults
        self.last_update_time = 0.0

    def update_universe(self, max_coins=10):
        """
        Queries Hyperliquid API for all listed asset contexts, filters by volume
        and spread, and selects the top N most liquid coins for active tracking.
        """
        now = time.time()
        # Prevent spamming: only update once an hour max unless forced
        if now - self.last_update_time < 3600.0 and self.last_update_time != 0.0:
            return self.active_universe

        if not hl_client.is_active or hl_client.info is None:
            db.log_system("UNIVERSE", "Hyperliquid client inactive, keeping default universe.")
            return self.active_universe

        try:
            db.log_system("UNIVERSE", "Executing Stage 1 Scan: Liquidity & Depth Bounding...")
            meta_and_ctxs = hl_client.info.meta_and_asset_ctxs()
            universe = meta_and_ctxs[0].get("universe", [])
            ctxs = meta_and_ctxs[1]

            valid_coins = []

            for i, asset_meta in enumerate(universe):
                asset_name = asset_meta.get("name")
                if i < len(ctxs):
                    ctx = ctxs[i]
                    
                    # Criteria 1: 24h Volume (dayNtlVlm)
                    vol_str = ctx.get("dayNtlVlm")
                    if not vol_str:
                        continue
                    volume = float(vol_str)

                    # Criteria 2: Effective Spread (from impactPxs)
                    impact_pxs = ctx.get("impactPxs")
                    mid_px_str = ctx.get("midPx")
                    
                    if not impact_pxs or len(impact_pxs) < 2 or not mid_px_str:
                        continue

                    bid_impact = float(impact_pxs[0])
                    ask_impact = float(impact_pxs[1])
                    mid_px = float(mid_px_str)

                    if mid_px == 0:
                        continue

                    spread_pct = abs(ask_impact - bid_impact) / mid_px

                    # Apply Constraints:
                    # Volume > $2,000,000
                    # Spread < 0.05% (0.0005)
                    if volume > 2000000.0 and spread_pct < 0.0005:
                        valid_coins.append({
                            "coin": asset_name,
                            "volume": volume,
                            "spread_pct": spread_pct
                        })

            if not valid_coins:
                db.log_system("WARNING", "Universe Manager found no coins matching strict criteria. Using fallback.")
                return self.active_universe

            # Sort by highest volume
            valid_coins.sort(key=lambda x: x["volume"], reverse=True)
            
            top_coins = [c["coin"] for c in valid_coins[:max_coins]]
            
            db.log_system("UNIVERSE", f"Scan complete. Found {len(valid_coins)} valid liquid coins. Selecting Top {len(top_coins)}: {top_coins}")
            
            self.active_universe = top_coins
            self.last_update_time = now
            return self.active_universe

        except Exception as e:
            db.log_system("ERROR", f"Failed to update universe dynamically: {str(e)}")
            return self.active_universe

    def get_active_universe(self):
        """Returns the currently active universe of liquid coins."""
        return self.active_universe

universe_manager = UniverseManagerService()
