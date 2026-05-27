import numpy as np
import pandas as pd
from collections import deque
from backend.config import Config
from backend.services.database import db
from backend.services.orderbook_tracker import tracker

class StrategyEngine:
    def __init__(self, asset_a="BTC", asset_b="ETH", window_size=30):
        self.asset_a = asset_a
        self.asset_b = asset_b
        self.window_size = window_size
        
        # Candidate pairs list for hot tracking
        self.pairs = [
            {"asset_a": "BTC", "asset_b": "ETH", "hedge_ratio": 19.0},
            {"asset_a": "DOGE", "asset_b": "SUI", "hedge_ratio": 0.099}
        ]
        
        # Buffers to keep rolling spreads in memory for all pairs (prevents cold start when switching)
        self.spread_buffers = {
            "BTC_ETH": deque(maxlen=window_size),
            "DOGE_SUI": deque(maxlen=window_size),
            f"{asset_a}_{asset_b}": deque(maxlen=window_size)
        }
        
        # Sane hedge ratio defaults based on asset selection
        if asset_a == "BTC" and asset_b == "ETH":
            self.hedge_ratio = 19.0
        elif asset_a == "DOGE" and asset_b == "SUI":
            self.hedge_ratio = 0.099
        else:
            self.hedge_ratio = 0.049
            
        # Per-coin rolling price buffers for momentum and volatility strategies
        self.price_buffers = {}
        
        self.current_zscore = 0.0
        self.current_spread = 0.0
        self.latest_sentiment = 0.0 # From LLM news engine

    @property
    def spread_buffer(self):
        active_key = f"{self.asset_a}_{self.asset_b}"
        if active_key not in self.spread_buffers:
            self.spread_buffers[active_key] = deque(maxlen=self.window_size)
        return self.spread_buffers[active_key]

    def update_sentiment(self, score: float):
        """Update active narrative sentiment multiplier."""
        self.latest_sentiment = score
        db.log_system("STRATEGY", f"Updated strategy sentiment score to: {score:.2f}")

    def sync_with_universe(self, active_universe):
        """Reassigns pairs based on the dynamically provided liquidity universe."""
        if not active_universe or len(active_universe) < 2:
            return
            
        # Pair top 1 and top 2 as the primary pair
        new_a = active_universe[0]
        new_b = active_universe[1]
        
        if self.asset_a != new_a or self.asset_b != new_b:
            self.asset_a = new_a
            self.asset_b = new_b
            db.log_system("STRATEGY", f"Dynamic Universe Sync: Active primary pair switched to {new_a}/{new_b}")
            
        # Pair up the rest if possible
        new_pairs = []
        for i in range(0, len(active_universe) - 1, 2):
            new_pairs.append({
                "asset_a": active_universe[i],
                "asset_b": active_universe[i+1],
                "hedge_ratio": 1.0 # Fallback 1:1 since we don't have historical cointegration model
            })
        self.pairs = new_pairs

    def calculate_signals(self):
        """
        Runs mathematical Z-score analysis on latest orderbook ticks.
        Integrates LLM sentiment to skew entry thresholds.
        Returns: A dictionary containing signals: { "BTC": "LONG"|"SHORT"|"FLAT"|None, "ETH": ... }
        """
        import sys
        is_testing = "pytest" in sys.modules or "unittest" in sys.modules
        
        # Update spread buffers for ALL pairs to keep them hot and populated
        for pair in self.pairs:
            a = pair["asset_a"]
            b = pair["asset_b"]
            ratio = pair["hedge_ratio"]
            
            state_a = tracker.get_market_state(a)
            state_b = tracker.get_market_state(b)
            
            price_a = state_a.get("mid", 0.0)
            price_b = state_b.get("mid", 0.0)
            
            if price_a > 0.0 and price_b > 0.0:
                spread = price_a - (ratio * price_b)
                pair_key = f"{a}_{b}"
                if pair_key not in self.spread_buffers:
                    self.spread_buffers[pair_key] = deque(maxlen=self.window_size)
                self.spread_buffers[pair_key].append(spread)
                
        # Add the custom runtime pair from constructor if not present
        custom_key = f"{self.asset_a}_{self.asset_b}"
        if custom_key not in self.spread_buffers:
            self.spread_buffers[custom_key] = deque(maxlen=self.window_size)
            
        state_a = tracker.get_market_state(self.asset_a)
        state_b = tracker.get_market_state(self.asset_b)
        price_a = state_a.get("mid", 0.0)
        price_b = state_b.get("mid", 0.0)
        
        if price_a == 0.0 or price_b == 0.0:
            return {}
            
        spread = price_a - (self.hedge_ratio * price_b)
        
        # Only append spread if it wasn't already appended as part of standard pairs list
        if custom_key not in [f"{p['asset_a']}_{p['asset_b']}" for p in self.pairs]:
            self.spread_buffers[custom_key].append(spread)
            
        self.current_spread = spread
        
        active_buffer = self.spread_buffers[custom_key]
        if len(active_buffer) < 5:
            # Not enough data for Z-score standard deviation calculation
            return {}
            
        # Mathematical computations using numpy
        spread_array = np.array(active_buffer)
        mean_spread = np.mean(spread_array)
        std_spread = np.std(spread_array)
        
        # Avoid division by zero
        if std_spread == 0.0:
            std_spread = 0.0001
            
        zscore = (spread - mean_spread) / std_spread
        self.current_zscore = zscore
        
        # Record history to SQLite
        db.record_zscore(self.asset_a, self.asset_b, price_a, price_b, spread, zscore)
        
        # Base Entry/Exit Thresholds
        entry_short = 2.0
        entry_long = -2.0
        exit_flat = 0.5
        
        # Skew entry thresholds dynamically based on LLM Sentiment (Optional Narrative Edge)
        # Bullish sentiment (> 0.3) makes us more eager to LONG and less eager to SHORT
        if self.latest_sentiment > 0.3:
            entry_long = -1.5 # Lower Z-score threshold for Longs (easier to trigger)
            entry_short = 2.5 # Raise Z-score threshold for Shorts (harder to trigger)
            db.log_system("STRATEGY", f"Sentiment is Bullish ({self.latest_sentiment:.2f}). Skewing Z-score thresholds to: Long {entry_long} | Short {entry_short}")
        # Bearish sentiment (< -0.3) makes us more eager to SHORT and less eager to LONG
        elif self.latest_sentiment < -0.3:
            entry_short = 1.5 # Lower Z-score threshold for Shorts (easier to trigger)
            entry_long = -2.5 # Raise Z-score threshold for Longs (harder to trigger)
            db.log_system("STRATEGY", f"Sentiment is Bearish ({self.latest_sentiment:.2f}). Skewing Z-score thresholds to: Long {entry_long} | Short {entry_short}")
            
        signals = {self.asset_a: None, self.asset_b: None}
        
        # Logic Evaluation
        if zscore > entry_short:
            # Spread is high: Asset A is overvalued, Asset B is undervalued
            signals[self.asset_a] = "SHORT"
            signals[self.asset_b] = "LONG"
            db.log_system("STRATEGY", f"Z-Score {zscore:.2f} > {entry_short}. Signal Generated: SHORT {self.asset_a} / LONG {self.asset_b}")
            
        elif zscore < entry_long:
            # Spread is low: Asset A is undervalued, Asset B is overvalued
            signals[self.asset_a] = "LONG"
            signals[self.asset_b] = "SHORT"
            db.log_system("STRATEGY", f"Z-Score {zscore:.2f} < {entry_long}. Signal Generated: LONG {self.asset_a} / SHORT {self.asset_b}")
            
        elif abs(zscore) < exit_flat:
            # Spread reverted back to normal
            signals[self.asset_a] = "FLAT"
            signals[self.asset_b] = "FLAT"
            db.log_system("STRATEGY", f"Z-Score {zscore:.2f} near mean (< {exit_flat}). Signal Generated: FLAT (Close Positions)")
            
        return {
            "zscore": zscore,
            "spread": spread,
            "mean": mean_spread,
            "std": std_spread,
            "signals": signals
        }

    def calculate_momentum_signals(self, coin: str) -> str:
        """
        Time-Series Momentum Strategy (Trend Following).
        Calculates simple moving average crossovers (Fast 5 vs Slow 20 ticks).
        """
        state = tracker.get_market_state(coin)
        price = state.get("mid", 0.0)
        if price == 0.0:
            return None

        # Append current price to the per-coin rolling buffer
        if coin not in self.price_buffers:
            self.price_buffers[coin] = deque(maxlen=50)
        self.price_buffers[coin].append(price)

        buf = list(self.price_buffers[coin])
        if len(buf) < 20:
            # Not enough data for Slow SMA(20); cannot generate signal
            return None

        # Compute real Fast SMA (5) and Slow SMA (20) from rolling buffer
        sma_fast = float(np.mean(buf[-5:]))
        sma_slow = float(np.mean(buf[-20:]))

        if sma_fast > sma_slow:
            db.log_system("STRATEGY_MOMENTUM", f"Fast SMA > Slow SMA on {coin}. Signal: LONG")
            return "LONG"
        elif sma_fast < sma_slow:
            db.log_system("STRATEGY_MOMENTUM", f"Fast SMA < Slow SMA on {coin}. Signal: SHORT")
            return "SHORT"
        return "FLAT"

    def calculate_volatility_breakout(self, coin: str) -> str:
        """
        Volatility Breakout Strategy (Bollinger Bands).
        Triggers execution when price breaks above or below volatility bands.
        """
        state = tracker.get_market_state(coin)
        price = state.get("mid", 0.0)
        if price == 0.0:
            return None

        # Append current price to the per-coin rolling buffer
        if coin not in self.price_buffers:
            self.price_buffers[coin] = deque(maxlen=50)
        self.price_buffers[coin].append(price)

        buf = list(self.price_buffers[coin])
        if len(buf) < 20:
            # Not enough data for reliable Bollinger Band calculation
            return None

        # Bollinger Bands: Mean +/- 2.0 * Standard Deviation of price over 20 entries
        window = buf[-20:]
        mean_px = float(np.mean(window))
        std_px = float(np.std(window))
        if std_px == 0.0:
            std_px = 0.0001

        upper_band = mean_px + 2.0 * std_px
        lower_band = mean_px - 2.0 * std_px

        if price >= upper_band:
            db.log_system("STRATEGY_BREAKOUT", f"Price ${price:.2f} broke Upper Band ${upper_band:.2f} on {coin}. Signal: LONG (Breakout)")
            return "LONG"
        elif price <= lower_band:
            db.log_system("STRATEGY_BREAKOUT", f"Price ${price:.2f} broke Lower Band ${lower_band:.2f} on {coin}. Signal: SHORT (Breakout)")
            return "SHORT"
        return "FLAT"

    def calculate_grid_signals(self, coin: str) -> list[dict]:
        """
        Grid Trading Algorithm.
        Generates buy grid limits below price, and sell grids above.
        """
        state = tracker.get_market_state(coin)
        price = state.get("mid", 0.0)
        if price == 0.0:
            return []
            
        grid_levels = 5
        grid_interval_pct = 0.005 # 0.5% spacing
        
        buy_grids = []
        sell_grids = []
        
        for idx in range(1, grid_levels + 1):
            buy_px = price * (1.0 - (grid_interval_pct * idx))
            sell_px = price * (1.0 + (grid_interval_pct * idx))
            buy_grids.append({"level": idx, "price": buy_px, "size": 0.1})
            sell_grids.append({"level": idx, "price": sell_px, "size": 0.1})
            
        return {"buy_levels": buy_grids, "sell_levels": sell_grids}

    def calculate_market_making_signals(self, coin: str) -> dict:
        """
        Pure Market Making with dynamic Inventory skew and Adverse Selection protection.
        Skews bids and asks dynamically based on orderbook imbalance.
        """
        state = tracker.get_market_state(coin)
        price = state.get("mid", 0.0)
        imbalance = state.get("imbalance", 0.0) # Imbalance from L2
        
        if price == 0.0:
            return {}
            
        # Inventory Management: skew pricing to attract opposite trades if we hold too many positions
        # Standard Bid-Ask spread baseline: 0.1% offset
        bid_offset = 0.001
        ask_offset = 0.001
        
        # Order Book Imbalance adjustments
        if imbalance > 0.3:
            # Positive imbalance: more buying interest. Lower bid offset (bid higher)
            bid_offset -= 0.0003
            ask_offset += 0.0003
        elif imbalance < -0.3:
            # Negative imbalance: more selling interest. Lower ask offset (ask lower)
            ask_offset -= 0.0003
            bid_offset += 0.0003
            
        bid_px = price * (1.0 - bid_offset)
        ask_px = price * (1.0 + ask_offset)
        
        return {
            "coin": coin,
            "bid_price": bid_px,
            "ask_price": ask_px,
            "imbalance": imbalance,
            "adverse_selection_halt": abs(imbalance) > 0.90 # Adverse Selection circuit breaker
        }

# Singleton instance
strategy_engine = StrategyEngine(asset_a="DOGE", asset_b="SUI")

