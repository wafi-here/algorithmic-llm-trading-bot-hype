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
        
        # Buffer to keep rolling spreads in memory
        self.spread_buffer = deque(maxlen=window_size)
        
        # Keep track of active ratios
        self.hedge_ratio = 19.0 # Approximate baseline ratio BTC/ETH (e.g., 67000 / 3500)
        
        self.current_zscore = 0.0
        self.current_spread = 0.0
        self.latest_sentiment = 0.0 # From LLM news engine

    def update_sentiment(self, score: float):
        """Update active narrative sentiment multiplier."""
        self.latest_sentiment = score
        db.log_system("STRATEGY", f"Updated strategy sentiment score to: {score:.2f}")

    def calculate_signals(self):
        """
        Runs mathematical Z-score analysis on latest orderbook ticks.
        Integrates LLM sentiment to skew entry thresholds.
        Returns: A dictionary containing signals: { "BTC": "LONG"|"SHORT"|"FLAT"|None, "ETH": ... }
        """
        state_a = tracker.get_market_state(self.asset_a)
        state_b = tracker.get_market_state(self.asset_b)
        
        price_a = state_a.get("mid", 0.0)
        price_b = state_b.get("mid", 0.0)
        
        if price_a == 0.0 or price_b == 0.0:
            return {}
            
        # Re-calculate hedge ratio dynamically or keep fixed
        # Spread = PriceA - (Ratio * PriceB)
        spread = price_a - (self.hedge_ratio * price_b)
        self.spread_buffer.append(spread)
        self.current_spread = spread
        
        if len(self.spread_buffer) < 5:
            # Not enough data for Z-score standard deviation calculation
            return {}
            
        # Mathematical computations using numpy
        spread_array = np.array(self.spread_buffer)
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

# Singleton instance
strategy_engine = StrategyEngine(asset_a="BTC", asset_b="ETH")
