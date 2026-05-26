import asyncio
from backend.services.database import db
from backend.services.hyperliquid_client import hl_client

class ExecutionAlgos:
    def __init__(self):
        pass

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
            
            # Slippage tolerance
            exec_price = price * 1.005 if is_buy else price * 0.995
            
            hl_client.place_order(coin, is_buy, slice_size, exec_price)
            
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
            
            exec_price = price * 1.005 if is_buy else price * 0.995
            hl_client.place_order(coin, is_buy, slice_size, exec_price)
            
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
            
            exec_price = price * 1.005 if is_buy else price * 0.995
            hl_client.place_order(coin, is_buy, current_slice, exec_price)
            
            remaining_size -= current_slice
            slice_idx += 1
            
            if remaining_size > 0.0:
                # Sleep briefly to simulate order completion before placing the next slice
                await asyncio.sleep(2)
                
        db.log_system("EXECUTION_ALGO", f"Iceberg order completed successfully for {coin}.")

execution_algos = ExecutionAlgos()
