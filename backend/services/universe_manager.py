import time
from backend.services.database import db
from backend.services.hyperliquid_client import hl_client

class UniverseManagerService:
    def __init__(self):
        self.active_universe = ["BTC", "ETH", "SOL"] # Fallback defaults
        self.last_update_time = 0.0
        # Cached asset metadata from Hyperliquid API (szDecimals, midPx, volume)
        # Populated during universe scan so other components can use it without extra API calls
        self.asset_metadata = {}

    async def update_universe(self, max_coins=10):
        """
        Queries Hyperliquid API for all listed asset contexts, filters by volume
        and spread, and selects the top N most liquid coins for active tracking.
        Also caches asset metadata (szDecimals, midPx, volume) for all listed coins.
        """
        import asyncio
        now = time.time()
        # Prevent spamming: only update once an hour max unless forced
        if now - self.last_update_time < 3600.0 and self.last_update_time != 0.0:
            return self.active_universe

        if not hl_client.is_active or hl_client.info is None:
            db.log_system("UNIVERSE", "Hyperliquid client inactive, keeping default universe.")
            return self.active_universe

        try:
            db.log_system("UNIVERSE", "Executing Stage 1 Scan: Liquidity & Depth Bounding...")
            meta_and_ctxs = await asyncio.to_thread(hl_client.info.meta_and_asset_ctxs)
            universe = meta_and_ctxs[0].get("universe", [])
            ctxs = meta_and_ctxs[1]

            valid_coins = []
            new_metadata = {}

            for i, asset_meta in enumerate(universe):
                asset_name = asset_meta.get("name")
                sz_decimals = asset_meta.get("szDecimals", 2)
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

                    # Cache metadata for ALL listed coins (not just valid ones)
                    # so risk_manager can look up szDecimals for any coin
                    min_step = 10**(-sz_decimals) if sz_decimals > 0 else 1.0
                    new_metadata[asset_name] = {
                        "sz_decimals": sz_decimals,
                        "min_step": min_step,
                        "mid_px": mid_px,
                        "volume_24h": volume,
                        "spread_pct": spread_pct
                    }

                    # Apply Constraints:
                    # Volume > $2,000,000
                    # Spread < 0.05% (0.0005)
                    if volume > 2000000.0 and spread_pct < 0.0005:
                        valid_coins.append({
                            "coin": asset_name,
                            "volume": volume,
                            "spread_pct": spread_pct
                        })

            # Update cached metadata if we got any results
            if new_metadata:
                self.asset_metadata = new_metadata
                db.log_system("UNIVERSE", f"Asset metadata cache updated: {len(new_metadata)} coins indexed.")

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

    def get_asset_metadata(self, coin: str) -> dict:
        """Returns cached metadata for a specific coin.
        Returns: dict with keys sz_decimals, min_step, mid_px, volume_24h, spread_pct
        or empty dict if coin not found in cache.
        """
        return self.asset_metadata.get(coin, {})

    def get_active_universe(self):
        """Returns the currently active universe of liquid coins."""
        return self.active_universe

universe_manager = UniverseManagerService()
