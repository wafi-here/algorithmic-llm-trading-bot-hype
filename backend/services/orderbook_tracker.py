import asyncio
import json
import traceback
import websockets
import threading
import numpy as np
from collections import deque
from backend.config import Config
from backend.services.database import db

class OrderBookTracker:
    def __init__(self, coins=["BTC", "ETH"]):
        self.coins = coins
        self.ws_url = Config.WS_URL
        
        # State: { "BTC": { "bids": {px: sz}, "asks": {px: sz}, "mid": float, "spread": float, "imbalance": float } }
        self.books = {
            coin: {"bids": {}, "asks": {}, "mid": 0.0, "spread": 0.0, "imbalance": 0.0}
            for coin in coins
        }
        
        # Rolling imbalance history for Z-Score normalization (microstructure signal)
        # Stores last 50 raw imbalance values per coin to compute a normalized Z-Score
        self.imbalance_history = {
            coin: deque(maxlen=50) for coin in coins
        }
        
        self._running = False
        self._loop = None
        self._thread = None
        self._lock = threading.Lock()

    def start(self):
        """Starts the tracker thread in the background."""
        if self._running:
            return
            
        self._running = True
        self._thread = threading.Thread(target=self._run_thread, daemon=True)
        self._thread.start()
        db.log_system("INFO", f"OrderBookTracker background thread started targeting: {self.coins}")

    def stop(self):
        self._running = False
        if self._loop:
            self._loop.call_soon_threadsafe(self._loop.stop)

    def update_target_coins(self, new_coins_list):
        """Dynamically updates the active subscription universe."""
        with self._lock:
            # Add missing
            for coin in new_coins_list:
                if coin not in self.coins:
                    self.coins.append(coin)
                    self.books[coin] = {"bids": {}, "asks": {}, "mid": 0.0, "spread": 0.0, "imbalance": 0.0}
                    self.imbalance_history[coin] = deque(maxlen=50)
                    if hasattr(self, '_active_ws') and self._active_ws and self._loop:
                        # Schedule subscription in async loop
                        msg = json.dumps({"method": "subscribe", "subscription": {"type": "l2Book", "coin": coin}})
                        asyncio.run_coroutine_threadsafe(self._active_ws.send(msg), self._loop)
                        db.log_system("WS", f"Dynamically subscribed to l2Book for {coin}")
            
            # Note: We keep old coins in self.books memory so StrategyEngine doesn't crash if it iterates late, 
            # but we don't strictly unsubscribe to keep it simple, or we can just stop tracking them in updates.

    def _run_thread(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self._connect_and_listen())

    async def _connect_and_listen(self):
        backoff = 1
        while self._running:
            try:
                db.log_system("WS", f"Connecting to WebSocket: {self.ws_url}")
                async with websockets.connect(self.ws_url) as ws:
                    self._active_ws = ws
                    backoff = 1 # Reset backoff upon successful connection
                    
                    # Send subscriptions
                    with self._lock:
                        coins_to_sub = self.coins.copy()
                    
                    for coin in coins_to_sub:
                        subscribe_msg = {
                            "method": "subscribe",
                            "subscription": {"type": "l2Book", "coin": coin}
                        }
                        await ws.send(json.dumps(subscribe_msg))
                        db.log_system("WS", f"Subscribed to l2Book for {coin}")
                    
                    # Keepalive ping sender task
                    ping_task = asyncio.create_task(self._send_pings(ws))
                    
                    # Listen for messages
                    while self._running:
                        msg_str = await ws.recv()
                        msg = json.loads(msg_str)
                        
                        # Handle subscriptions responses
                        channel = msg.get("channel")
                        if channel == "l2Book":
                            data = msg.get("data", {})
                            self._process_l2book(data)
                            
            except Exception as e:
                db.log_system("ERROR", f"WebSocket error: {str(e)}. Reconnecting in {backoff}s...")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60) # Exponential backoff

    async def _send_pings(self, ws):
        """Send keepalive ping every 45 seconds to prevent timeout."""
        while self._running:
            try:
                await asyncio.sleep(45)
                await ws.send(json.dumps({"method": "ping"}))
            except Exception:
                break

    def _process_l2book(self, data):
        coin = data.get("coin")
        if coin not in self.books:
            return
            
        levels = data.get("levels", [])
        if not levels or len(levels) < 2:
            return
            
        # Parse bids and asks from data
        # levels is [ bids_list, asks_list ]
        bids_raw = levels[0]
        asks_raw = levels[1]
        
        bids = {float(level["px"]): float(level["sz"]) for level in bids_raw}
        asks = {float(level["px"]): float(level["sz"]) for level in asks_raw}
        
        if not bids or not asks:
            return
            
        # Calculate best prices
        best_bid = max(bids.keys())
        best_ask = min(asks.keys())
        
        mid = (best_bid + best_ask) / 2.0
        spread = best_ask - best_bid
        
        # Calculate orderbook imbalance (Bid liquidity vs Ask liquidity for top 5 levels)
        bid_volume = sum(bids[px] for px in sorted(bids.keys(), reverse=True)[:5])
        ask_volume = sum(asks[px] for px in sorted(asks.keys())[:5])
        
        imbalance = 0.0
        if (bid_volume + ask_volume) > 0:
            imbalance = (bid_volume - ask_volume) / (bid_volume + ask_volume)
        
        # Compute Z-Score normalized imbalance for microstructure signal
        # This transforms the noisy raw OBI into a statistically meaningful deviation metric
        imbalance_zscore = 0.0
        if coin not in self.imbalance_history:
            self.imbalance_history[coin] = deque(maxlen=50)
        self.imbalance_history[coin].append(imbalance)
        
        if len(self.imbalance_history[coin]) >= 10:
            imb_array = np.array(self.imbalance_history[coin])
            imb_mean = np.mean(imb_array)
            imb_std = np.std(imb_array)
            if imb_std > 0.0001:
                imbalance_zscore = (imbalance - imb_mean) / imb_std
            
        # Update memory state under thread lock
        with self._lock:
            self.books[coin] = {
                "bids": bids,
                "asks": asks,
                "mid": mid,
                "spread": spread,
                "imbalance": imbalance,
                "imbalance_zscore": imbalance_zscore,
                "best_bid": best_bid,
                "best_ask": best_ask
            }

    def get_market_state(self, coin):
        """Returns the current tracked memory state for a given coin. Fallbacks to mock prices if offline."""
        with self._lock:
            state = self.books.get(coin, {}).copy()
            
        if not state or state.get("mid") == 0.0:
            # Fallback to realistic mock prices for testing if WS not connected
            mock_prices = {"BTC": 67250.0, "ETH": 3520.0, "DOGE": 0.17, "SUI": 3.50, "SOL": 170.0, "NEAR": 5.0, "AVAX": 35.0}
            mid = mock_prices.get(coin, 100.0)
            return {
                "mid": mid,
                "spread": mid * 0.0001,
                "imbalance": 0.0,
                "imbalance_zscore": 0.0,
                "best_bid": mid - (mid * 0.00005),
                "best_ask": mid + (mid * 0.00005),
                "is_mock": True
            }
        return state


# Singleton instance tracking BTC, ETH, DOGE, and SUI
tracker = OrderBookTracker(coins=["BTC", "ETH", "DOGE", "SUI"])
