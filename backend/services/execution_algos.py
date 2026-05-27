import asyncio
import numpy as np
from collections import deque
from backend.services.database import db
from backend.services.hyperliquid_client import hl_client


class ExecutionAlgos:
    def __init__(self):
        # Per-coin price history for volatility estimation
        self._price_history: dict[str, deque] = {}

    def calculate_dynamic_slippage(self, coin: str, size: float, is_buy: bool, market_state: dict = None) -> float:
        """
        Dynamic Slippage Model inspired by Lean's VolumeShareSlippageModel.
        
        Instead of a fixed 0.5% buffer, computes adaptive slippage based on:
        1. Spread width: wider spread = more slippage (direct market cost)
        2. Volatility: higher recent price variance = more slippage buffer
        3. Size impact: larger orders relative to book depth = more price impact
        
        Formula: slippage = max(floor, base_spread + vol_component + size_impact)
        
        Returns: slippage as a fraction (e.g., 0.003 = 0.3%)
        """
        if market_state is None:
            from backend.services.orderbook_tracker import tracker
            market_state = tracker.get_market_state(coin)
        
        mid = market_state.get("mid", 0.0)
        best_bid = market_state.get("best_bid", mid * 0.999)
        best_ask = market_state.get("best_ask", mid * 1.001)
        
        if mid <= 0:
            return 0.005  # Fallback to 0.5% if no data
        
        # 1. Spread component: half-spread as base cost
        spread = (best_ask - best_bid) / mid if best_ask > best_bid else 0.001
        spread_component = spread / 2.0  # Half-spread is the expected crossing cost
        
        # 2. Volatility component: rolling 20-tick price std / mid
        if coin not in self._price_history:
            self._price_history[coin] = deque(maxlen=30)
        self._price_history[coin].append(mid)
        
        vol_component = 0.0
        if len(self._price_history[coin]) >= 5:
            prices = np.array(self._price_history[coin])
            returns_std = np.std(np.diff(prices) / prices[:-1]) if len(prices) > 1 else 0.0
            vol_component = min(0.005, returns_std * 2.0)  # Cap vol contribution at 0.5%
        
        # 3. Size impact: Lean-style coefficient × (size_notional / estimated_book_depth)
        # For crypto perps, typical L2 depth near top-of-book is ~$50k-$500k
        size_notional = size * mid
        estimated_depth = 100000.0  # Conservative estimate for Hyperliquid L2
        size_ratio = min(1.0, size_notional / estimated_depth)
        size_impact = 0.001 * (size_ratio ** 0.5)  # Square root impact model
        
        # Total slippage with floor and cap
        total_slippage = spread_component + vol_component + size_impact
        
        # HL IOC orders act as Market Orders. To prevent "Order could not immediately match" 
        # rejections due to micro-second websocket lag, we need a wider limit price buffer.
        # Hyperliquid's matching engine guarantees best execution (price priority), 
        # so this is just the worst acceptable price boundary, not the price we actually pay.
        total_slippage = max(0.01, min(0.05, total_slippage * 2.0))  # Floor 1.0%, Cap 5.0%
        
        return total_slippage

    def compute_exec_price(self, price: float, is_buy: bool, slippage: float) -> float:
        """Computes execution price with slippage applied in the correct direction."""
        if is_buy:
            return price * (1.0 + slippage)
        else:
            return price * (1.0 - slippage)

    async def execute_twap(self, coin: str, is_buy: bool, total_size: float, duration_seconds: int = 30, slices: int = 3):
        """
        Executes a Time-Weighted Average Price (TWAP) execution algorithm.
        Slices a large order into multiple intervals to reduce market impact.
        """
        slice_size = total_size / slices
        interval = duration_seconds / slices
        
        db.log_system("EXECUTION_ALGO", f"Initiating TWAP order for {coin} | Total Size: {total_size} | Slices: {slices} | Interval: {interval}s")
        
        for idx in range(slices):
            # Fetch latest price
            from backend.services.orderbook_tracker import tracker
            market_state = tracker.get_market_state(coin)
            price = market_state.get("mid", 0.0)
            
            if price == 0.0:
                price = 67000.0 if coin == "BTC" else 3500.0
                
            db.log_system("EXECUTION_ALGO", f"TWAP Slice {idx+1}/{slices} | Placing size: {slice_size:.4f} at price: ${price:.2f}")
            
            # Dynamic slippage (replaces hardcoded 0.5%)
            slippage = self.calculate_dynamic_slippage(coin, slice_size, is_buy, market_state)
            exec_price = self.compute_exec_price(price, is_buy, slippage)
            
            await hl_client.place_order(coin, is_buy, slice_size, exec_price)
            
            if idx < slices - 1:
                await asyncio.sleep(interval)
                
        db.log_system("EXECUTION_ALGO", f"TWAP order execution completed successfully for {coin}.")

    async def execute_vwap(self, coin: str, is_buy: bool, total_size: float, duration_seconds: int = 30, slices: int = 3):
        """
        Executes a Volume-Weighted Average Price (VWAP) execution algorithm.
        Weights execution sizing based on expected volume distribution profiles.
        """
        # Simulated volume weights: e.g. 50% in first slice, 20% in middle slice, 30% in last slice
        volume_weights = [0.5, 0.2, 0.3]
        if slices != len(volume_weights):
            volume_weights = [1.0 / slices] * slices
            
        interval = duration_seconds / slices
        
        db.log_system("EXECUTION_ALGO", f"Initiating VWAP order for {coin} | Total Size: {total_size} | Slices: {slices} | Interval: {interval}s")
        
        for idx in range(slices):
            slice_weight = volume_weights[idx]
            slice_size = total_size * slice_weight
            
            # Fetch latest price
            from backend.services.orderbook_tracker import tracker
            market_state = tracker.get_market_state(coin)
            price = market_state.get("mid", 0.0)
            
            if price == 0.0:
                price = 67000.0 if coin == "BTC" else 3500.0
                
            db.log_system("EXECUTION_ALGO", f"VWAP Slice {idx+1}/{slices} (Weight: {slice_weight*100:.1f}%) | Sizing: {slice_size:.4f} at: ${price:.2f}")
            
            slippage = self.calculate_dynamic_slippage(coin, slice_size, is_buy, market_state)
            exec_price = self.compute_exec_price(price, is_buy, slippage)
            await hl_client.place_order(coin, is_buy, slice_size, exec_price)
            
            if idx < slices - 1:
                await asyncio.sleep(interval)
                
        db.log_system("EXECUTION_ALGO", f"VWAP order execution completed successfully for {coin}.")

    async def execute_iceberg(self, coin: str, is_buy: bool, total_size: float, visible_size: float):
        """
        Executes a simulated Iceberg Order algorithm.
        Reveals only a small fraction (visible_size) of the total size in the orderbook.
        """
        db.log_system("EXECUTION_ALGO", f"Initiating Iceberg order for {coin} | Total Size: {total_size} | Visible Slice: {visible_size}")
        
        remaining_size = total_size
        slice_idx = 1
        
        while remaining_size > 0.0:
            current_slice = min(visible_size, remaining_size)
            
            # Fetch latest price
            from backend.services.orderbook_tracker import tracker
            market_state = tracker.get_market_state(coin)
            price = market_state.get("mid", 0.0)
            
            if price == 0.0:
                price = 67000.0 if coin == "BTC" else 3500.0
                
            db.log_system("EXECUTION_ALGO", f"Iceberg Slice {slice_idx} | Placing size: {current_slice:.4f} at: ${price:.2f} (Remaining: {remaining_size-current_slice:.4f})")
            
            slippage = self.calculate_dynamic_slippage(coin, current_slice, is_buy, market_state)
            exec_price = self.compute_exec_price(price, is_buy, slippage)
            await hl_client.place_order(coin, is_buy, current_slice, exec_price)
            
            remaining_size -= current_slice
            slice_idx += 1
            
            if remaining_size > 0.0:
                # Sleep briefly to simulate order completion before placing the next slice
                await asyncio.sleep(2)
                
        db.log_system("EXECUTION_ALGO", f"Iceberg order completed successfully for {coin}.")

    async def execute_market_making(self, coin: str, signals: dict, size: float):
        """
        Actively manages resting Market Maker limit orders dynamically.
        """
        if not signals:
            return
            
        if not hasattr(self, 'active_mm_orders'):
            self.active_mm_orders = {}
            
        if coin not in self.active_mm_orders:
            self.active_mm_orders[coin] = {"bid_oid": None, "ask_oid": None}
            
        # 1. Circuit Breaker Check
        if signals.get("adverse_selection_halt", False):
            db.log_system("EXECUTION_MM", f"Adverse Selection Halted on {coin}. Cancelling all resting MM orders.")
            if self.active_mm_orders[coin]["bid_oid"]:
                await hl_client.cancel_order(coin, self.active_mm_orders[coin]["bid_oid"])
                self.active_mm_orders[coin]["bid_oid"] = None
            if self.active_mm_orders[coin]["ask_oid"]:
                await hl_client.cancel_order(coin, self.active_mm_orders[coin]["ask_oid"])
                self.active_mm_orders[coin]["ask_oid"] = None
            return

        optimal_bid = signals.get("bid_price", 0.0)
        optimal_ask = signals.get("ask_price", 0.0)
        
        if optimal_bid == 0.0 or optimal_ask == 0.0:
            return

        open_orders = await hl_client.get_open_orders(coin)
        open_oids = [o.get("oid") for o in open_orders]
        
        # 2. Check and manage Bid
        current_bid_oid = self.active_mm_orders[coin]["bid_oid"]
        needs_new_bid = True
        
        if current_bid_oid and current_bid_oid in open_oids:
            # Find the order
            order_data = next((o for o in open_orders if o.get("oid") == current_bid_oid), None)
            if order_data:
                current_price = float(order_data.get("limitPx", 0.0))
                drift = abs(current_price - optimal_bid) / optimal_bid
                if drift < 0.005: # 0.5% drift tolerance
                    needs_new_bid = False
                else:
                    await hl_client.cancel_order(coin, current_bid_oid)
                    
        if needs_new_bid:
            res = await hl_client.place_order(coin, is_buy=True, size=size, price=optimal_bid)
            if res.get("status") == "ok":
                # Extract OID from response if available
                new_oid = res.get("response", {}).get("data", {}).get("statuses", [{}])[0].get("resting", {}).get("oid")
                if not new_oid: # fallback for mock
                    new_oid = res.get("response", {}).get("id")
                self.active_mm_orders[coin]["bid_oid"] = new_oid

        # 3. Check and manage Ask
        current_ask_oid = self.active_mm_orders[coin]["ask_oid"]
        needs_new_ask = True
        
        if current_ask_oid and current_ask_oid in open_oids:
            order_data = next((o for o in open_orders if o.get("oid") == current_ask_oid), None)
            if order_data:
                current_price = float(order_data.get("limitPx", 0.0))
                drift = abs(current_price - optimal_ask) / optimal_ask
                if drift < 0.005: # 0.5% drift tolerance
                    needs_new_ask = False
                else:
                    await hl_client.cancel_order(coin, current_ask_oid)
                    
        if needs_new_ask:
            res = await hl_client.place_order(coin, is_buy=False, size=size, price=optimal_ask)
            if res.get("status") == "ok":
                new_oid = res.get("response", {}).get("data", {}).get("statuses", [{}])[0].get("resting", {}).get("oid")
                if not new_oid:
                    new_oid = res.get("response", {}).get("id")
                self.active_mm_orders[coin]["ask_oid"] = new_oid

    async def execute_grid_trading(self, coin: str, signals: dict, base_size: float):
        """
        Actively manages a live Grid Trading Limit array.
        """
        buy_levels = signals.get("buy_levels", [])
        sell_levels = signals.get("sell_levels", [])
        
        if not buy_levels or not sell_levels:
            return
            
        if not hasattr(self, 'active_grid_orders'):
            self.active_grid_orders = {}
            
        if coin not in self.active_grid_orders:
            self.active_grid_orders[coin] = []
            
        # Fetch current open orders
        open_orders = await hl_client.get_open_orders(coin)
        open_oids = [o.get("oid") for o in open_orders]
        
        # Verify grid integrity
        active_grid_oids = [oid for oid in self.active_grid_orders[coin] if oid in open_oids]
        
        # Grid integrity check: reset if any successfully placed order got filled, or if we have no active grid orders at all
        should_reset = False
        if not self.active_grid_orders[coin]:
            # No orders tracked yet, place the initial grid
            should_reset = True
        elif len(active_grid_oids) < len(self.active_grid_orders[coin]):
            # An order was filled!
            should_reset = True
            
        if should_reset:
            db.log_system("EXECUTION_GRID", f"Grid integrity breached for {coin} (fills detected or empty). Resetting Grid array.")
            
            # Cancel remaining old grid orders
            for oid in active_grid_oids:
                await hl_client.cancel_order(coin, oid)
                
            self.active_grid_orders[coin] = []
            
            # Place new grid
            for level in buy_levels:
                size = base_size * level["size"]
                res = await hl_client.place_order(coin, is_buy=True, size=size, price=level["price"])
                if res.get("status") == "ok":
                    new_oid = res.get("response", {}).get("data", {}).get("statuses", [{}])[0].get("resting", {}).get("oid")
                    if new_oid:
                        self.active_grid_orders[coin].append(new_oid)
                    elif res.get("response", {}).get("id"):
                        self.active_grid_orders[coin].append(res.get("response", {}).get("id"))
                        
            for level in sell_levels:
                size = base_size * level["size"]
                res = await hl_client.place_order(coin, is_buy=False, size=size, price=level["price"])
                if res.get("status") == "ok":
                    new_oid = res.get("response", {}).get("data", {}).get("statuses", [{}])[0].get("resting", {}).get("oid")
                    if new_oid:
                        self.active_grid_orders[coin].append(new_oid)
                    elif res.get("response", {}).get("id"):
                        self.active_grid_orders[coin].append(res.get("response", {}).get("id"))

execution_algos = ExecutionAlgos()
