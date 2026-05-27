import math
import numpy as np
import pandas as pd
from collections import deque
from backend.config import Config
from backend.services.database import db
from backend.services.orderbook_tracker import tracker


class StrategyEngine:
    def __init__(self, asset_a="BTC", asset_b="ETH", window_size=120):
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
        # FIX F1: These buffers are the SINGLE source of truth.
        # Only calculate_signals() appends to them. All other methods READ only.
        self.price_buffers = {}

        # Regime detection state (ADX cache per coin)
        self._adx_cache = {}

        self.current_zscore = 0.0
        self.current_spread = 0.0
        self.latest_sentiment = 0.0  # From LLM news engine
        self.latest_sentiment_confidence = 0.5

    @property
    def spread_buffer(self):
        active_key = f"{self.asset_a}_{self.asset_b}"
        if active_key not in self.spread_buffers:
            self.spread_buffers[active_key] = deque(maxlen=self.window_size)
        return self.spread_buffers[active_key]

    def update_sentiment(self, score: float, confidence: float = 0.5):
        """Update active narrative sentiment multiplier and confidence."""
        self.latest_sentiment = score
        self.latest_sentiment_confidence = confidence
        db.log_system("STRATEGY", f"Updated strategy sentiment score to: {score:.2f} | Confidence: {confidence:.2f}")

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
                "hedge_ratio": 1.0  # Fallback 1:1 since we don't have historical cointegration model
            })
        self.pairs = new_pairs

    def _ensure_price_buffer(self, coin: str):
        """Ensures a price buffer exists for a coin with the correct maxlen."""
        if coin not in self.price_buffers:
            self.price_buffers[coin] = deque(maxlen=200)

    def _append_price(self, coin: str, price: float):
        """
        FIX F1: Single authoritative price append.
        Only called from calculate_signals(). All other strategy methods READ only.
        """
        self._ensure_price_buffer(coin)
        self.price_buffers[coin].append(price)

    def _calculate_adx(self, coin: str, period: int = 14) -> float:
        """
        S1: Average Directional Index (ADX) — Regime Filter.

        ADX measures trend STRENGTH regardless of direction.
        - ADX > 25: trending market → favor momentum, suppress mean-reversion
        - ADX < 20: ranging market → favor mean-reversion, suppress momentum
        - 20-25: transition zone → allow both with reduced confidence

        Uses Wilder's smoothing method per J. Welles Wilder (1978).
        Computes on rolling price buffer data (no additional API calls).
        """
        self._ensure_price_buffer(coin)
        buf = list(self.price_buffers[coin])

        # Need at least 2*period+1 data points for reliable ADX
        min_required = 2 * period + 1
        if len(buf) < min_required:
            return 0.0  # Insufficient data — no regime signal

        highs = np.array(buf)
        # Approximate high/low from close prices using adjacent ticks
        # In real HFT you'd use actual OHLC, but for 30s ticks this is reasonable
        closes = np.array(buf)

        # True Range approximation from closes
        tr_list = []
        plus_dm_list = []
        minus_dm_list = []

        for i in range(1, len(closes)):
            high_diff = closes[i] - closes[i-1]  # Approximate +DM
            low_diff = closes[i-1] - closes[i]    # Approximate -DM
            tr = abs(closes[i] - closes[i-1])      # Simplified TR

            plus_dm = max(high_diff, 0.0) if high_diff > low_diff else 0.0
            minus_dm = max(low_diff, 0.0) if low_diff > high_diff else 0.0

            tr_list.append(tr)
            plus_dm_list.append(plus_dm)
            minus_dm_list.append(minus_dm)

        if len(tr_list) < period:
            return 0.0

        # Wilder's smoothing (exponential with alpha = 1/period)
        alpha = 1.0 / period

        smoothed_tr = sum(tr_list[:period])
        smoothed_plus_dm = sum(plus_dm_list[:period])
        smoothed_minus_dm = sum(minus_dm_list[:period])

        dx_values = []

        for i in range(period, len(tr_list)):
            smoothed_tr = smoothed_tr - (smoothed_tr / period) + tr_list[i]
            smoothed_plus_dm = smoothed_plus_dm - (smoothed_plus_dm / period) + plus_dm_list[i]
            smoothed_minus_dm = smoothed_minus_dm - (smoothed_minus_dm / period) + minus_dm_list[i]

            if smoothed_tr > 0:
                plus_di = (smoothed_plus_dm / smoothed_tr) * 100
                minus_di = (smoothed_minus_dm / smoothed_tr) * 100
            else:
                plus_di = 0.0
                minus_di = 0.0

            di_sum = plus_di + minus_di
            if di_sum > 0:
                dx = abs(plus_di - minus_di) / di_sum * 100
            else:
                dx = 0.0
            dx_values.append(dx)

        if len(dx_values) < period:
            return 0.0

        # Smooth DX to get ADX
        adx = sum(dx_values[:period]) / period
        for i in range(period, len(dx_values)):
            adx = (adx * (period - 1) + dx_values[i]) / period

        # Cache the result
        self._adx_cache[coin] = adx
        return adx

    def get_regime(self, coin: str) -> str:
        """
        Returns the current market regime for a coin based on ADX.
        - 'TRENDING': ADX > 25 — favor momentum, avoid mean-reversion
        - 'RANGING': ADX < 20 — favor mean-reversion, avoid momentum
        - 'TRANSITION': ADX 20-25 — both allowed with reduced confidence
        """
        adx = self._adx_cache.get(coin, 0.0)
        if adx > 25.0:
            return "TRENDING"
        elif adx < 20.0:
            return "RANGING"
        return "TRANSITION"

    def calculate_signals(self):
        """
        Runs mathematical Z-score analysis on latest orderbook ticks.
        Integrates LLM sentiment to skew entry thresholds.

        FIX F1: This method is the ONLY place that appends prices to buffers.
        S3: Uses log-price transforms for spread calculation.
        S1: Computes ADX regime filter for all universe coins.

        Returns: A dictionary containing signals: { "BTC": "LONG"|"SHORT"|"FLAT"|None, "ETH": ... }
        """
        # Update spread buffers for ALL pairs to keep them hot and populated
        for pair in self.pairs:
            a = pair["asset_a"]
            b = pair["asset_b"]

            state_a = tracker.get_market_state(a)
            state_b = tracker.get_market_state(b)

            price_a = state_a.get("mid", 0.0)
            price_b = state_b.get("mid", 0.0)

            if price_a > 0.0 and price_b > 0.0:
                # FIX F1: Single authoritative append — only here
                self._append_price(a, price_a)
                self._append_price(b, price_b)

                # S1: Compute ADX regime filter for both assets
                self._calculate_adx(a)
                self._calculate_adx(b)

                # OPTIMIZATION 1: Dynamic Hedge Ratio (Rolling OLS Beta)
                # Replaces static hardcoded ratios to prevent cointegration breakdown
                buf_a = list(self.price_buffers[a])
                buf_b = list(self.price_buffers[b])
                min_len = min(len(buf_a), len(buf_b))

                if min_len >= 10:
                    arr_a = np.array(buf_a[-min_len:])
                    arr_b = np.array(buf_b[-min_len:])
                    var_b = np.var(arr_b)
                    if var_b > 0:
                        cov = np.cov(arr_a, arr_b)[0][1]
                        pair["hedge_ratio"] = cov / var_b
                        if a == self.asset_a and b == self.asset_b:
                            self.hedge_ratio = pair["hedge_ratio"]

                ratio = pair["hedge_ratio"]

                # S3: Log-price transform for spread calculation
                # log(A) - ratio * log(B) stabilizes variance across price scales
                if price_a > 0 and price_b > 0:
                    spread = math.log(price_a) - (ratio * math.log(price_b))
                else:
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

        # S3: Log-price spread
        if price_a > 0 and price_b > 0:
            spread = math.log(price_a) - (self.hedge_ratio * math.log(price_b))
        else:
            spread = price_a - (self.hedge_ratio * price_b)

        # Only append spread if it wasn't already appended as part of standard pairs list
        if custom_key not in [f"{p['asset_a']}_{p['asset_b']}" for p in self.pairs]:
            self.spread_buffers[custom_key].append(spread)

        self.current_spread = spread

        active_buffer = self.spread_buffers[custom_key]
        # P3: Increased minimum from 5 to 20 for statistically meaningful Z-scores
        if len(active_buffer) < 20:
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

        # S4: Higher base entry thresholds for more reliable signals
        entry_short = 2.5
        entry_long = -2.5
        exit_flat = 0.5

        # Skew entry thresholds dynamically based on LLM Sentiment (Optional Narrative Edge)
        # Bullish sentiment (> 0.3) makes us more eager to LONG and less eager to SHORT
        if self.latest_sentiment > 0.3:
            entry_long = -2.0   # Lower Z-score threshold for Longs (easier to trigger)
            entry_short = 3.0   # Raise Z-score threshold for Shorts (harder to trigger)
            db.log_system("STRATEGY", f"Sentiment is Bullish ({self.latest_sentiment:.2f}). Skewing Z-score thresholds to: Long {entry_long} | Short {entry_short}")
        # Bearish sentiment (< -0.3) makes us more eager to SHORT and less eager to LONG
        elif self.latest_sentiment < -0.3:
            entry_short = 2.0   # Lower Z-score threshold for Shorts (easier to trigger)
            entry_long = -3.0   # Raise Z-score threshold for Longs (harder to trigger)
            db.log_system("STRATEGY", f"Sentiment is Bearish ({self.latest_sentiment:.2f}). Skewing Z-score thresholds to: Long {entry_long} | Short {entry_short}")

        # S1: Regime filter — suppress mean-reversion entries in trending markets
        regime_a = self.get_regime(self.asset_a)
        regime_b = self.get_regime(self.asset_b)

        signals = {self.asset_a: None, self.asset_b: None}

        # If either asset is in a strong trend, suppress Z-score entries
        # (Z-score mean-reversion is only valid in ranging/mean-reverting markets)
        if regime_a == "TRENDING" or regime_b == "TRENDING":
            adx_a = self._adx_cache.get(self.asset_a, 0)
            adx_b = self._adx_cache.get(self.asset_b, 0)
            db.log_system("STRATEGY_REGIME", f"Trending regime detected (ADX: {self.asset_a}={adx_a:.1f}, {self.asset_b}={adx_b:.1f}). Suppressing Z-score mean-reversion entries.")
            # Still allow FLAT signals for exit
            if abs(zscore) < exit_flat:
                signals[self.asset_a] = "FLAT"
                signals[self.asset_b] = "FLAT"
            return {
                "zscore": zscore,
                "spread": spread,
                "mean": mean_spread,
                "std": std_spread,
                "signals": signals
            }

        # Logic Evaluation (only in RANGING or TRANSITION regimes)
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
        Multi-Timeframe Rate-of-Change (ROC) Momentum Strategy.
        Replaces single SMA crossover with consensus across 3 timeframes
        to eliminate whipsaw false signals. ROC directly measures return
        velocity, which is the actual quantity of interest.

        FIX F1: This method READS from price_buffers only — no append.
        S1: Suppressed in RANGING regime (ADX < 20).
        """
        # S1: Regime filter — suppress momentum in ranging markets
        # Compute ADX if not already cached (e.g., universe-only coins not in pairs)
        if coin not in self._adx_cache:
            self._calculate_adx(coin)
        regime = self.get_regime(coin)
        if regime == "RANGING":
            return None  # Mean-reverting market — momentum signals are noise

        state = tracker.get_market_state(coin)
        price = state.get("mid", 0.0)
        if price == 0.0:
            return None

        # FIX F1: READ only — do NOT append here.
        # Price was already appended in calculate_signals().
        # For coins not in pairs (universe coins), append if not yet present this cycle.
        self._ensure_price_buffer(coin)
        buf = list(self.price_buffers[coin])

        # If buffer is empty (coin not in any pair), append the price here as a one-time fallback
        if not buf or buf[-1] != price:
            # This coin wasn't updated by calculate_signals() — it's a universe-only coin
            self._append_price(coin, price)
            buf = list(self.price_buffers[coin])

        if len(buf) < 40:
            # Not enough data for the slowest ROC(40) lookback
            return None

        # Rate-of-Change: (price_now - price_n_ago) / price_n_ago
        # Measures the velocity of price movement at each timeframe
        roc_fast = (buf[-1] - buf[-5]) / buf[-5] if buf[-5] != 0 else 0.0    # 5-tick ROC
        roc_mid = (buf[-1] - buf[-15]) / buf[-15] if buf[-15] != 0 else 0.0  # 15-tick ROC
        roc_slow = (buf[-1] - buf[-40]) / buf[-40] if buf[-40] != 0 else 0.0 # 40-tick ROC

        # OPTIMIZATION: Z-Score Regime Filter (Oversold/Overbought Blocker)
        window = buf[-40:]
        mean_px = float(np.mean(window))
        std_px = float(np.std(window))
        price_zscore = (price - mean_px) / std_px if std_px > 0 else 0.0

        # OPTIMIZATION 2: Momentum Acceleration Filter (Second Derivative)
        # Prevent buying into exhausting/decelerating trends.
        # Compare raw price velocity (absolute return per tick):
        #   delta_fast = (price_now - price_5ago) / 5
        #   delta_mid  = (price_now - price_15ago) / 15
        # If fast velocity >= 80% of mid velocity, trend is not decelerating sharply.
        # NOTE: We use raw deltas, not percentage ROC, because ROC velocity mathematically
        # always decelerates for any monotonic trend (even exponential), making ROC-based
        # acceleration filters impossible to pass for sustained moves.
        delta_fast = (buf[-1] - buf[-5]) / 5.0     # Raw return per tick (fast)
        delta_mid = (buf[-1] - buf[-15]) / 15.0    # Raw return per tick (mid)
        
        if roc_fast > 0 and roc_mid > 0 and roc_slow > 0:
            if delta_mid == 0 or abs(delta_fast) / abs(delta_mid) >= 0.8:  # Not decelerating sharply
                if price_zscore > 2.0:
                    db.log_system("STRATEGY_MOMENTUM", f"Blocked LONG on {coin}: Overbought (Z={price_zscore:.2f})")
                    return None
                db.log_system("STRATEGY_MOMENTUM", f"ROC consensus LONG on {coin} | Fast:{roc_fast:.4f} Mid:{roc_mid:.4f} Slow:{roc_slow:.4f}")
                return "LONG"
        elif roc_fast < 0 and roc_mid < 0 and roc_slow < 0:
            if delta_mid == 0 or abs(delta_fast) / abs(delta_mid) >= 0.8:  # Not decelerating sharply
                if price_zscore < -2.0:
                    db.log_system("STRATEGY_MOMENTUM", f"Blocked SHORT on {coin}: Oversold (Z={price_zscore:.2f})")
                    return None
                db.log_system("STRATEGY_MOMENTUM", f"ROC consensus SHORT on {coin} | Fast:{roc_fast:.4f} Mid:{roc_mid:.4f} Slow:{roc_slow:.4f}")
                return "SHORT"

        return None  # No consensus or exhausting trend = no signal

    def calculate_volatility_breakout(self, coin: str) -> str:
        """
        Volatility Breakout Strategy (Bollinger Bands).
        Triggers execution when price breaks above or below volatility bands.

        FIX F1: This method READS from price_buffers only — no append.
        S3: Uses log-price for band calculation to stabilize variance.
        """
        state = tracker.get_market_state(coin)
        price = state.get("mid", 0.0)
        if price == 0.0:
            return None

        # FIX F1: READ only — do NOT append here.
        self._ensure_price_buffer(coin)
        buf = list(self.price_buffers[coin])
        if len(buf) < 20:
            return None

        # S3: Use log-prices for Bollinger Bands to stabilize variance across price scales
        log_prices = [math.log(p) for p in buf[-20:] if p > 0]
        if len(log_prices) < 20:
            return None

        log_price = math.log(price) if price > 0 else 0.0

        mean_lp = float(np.mean(log_prices))
        std_lp = float(np.std(log_prices))
        if std_lp == 0.0:
            std_lp = 0.0001

        # OPTIMIZATION 3: Volatility-Adjusted Bollinger Bands (VABB)
        # Dynamically scale the multiplier based on the Coefficient of Variation
        cv = std_lp / abs(mean_lp) if abs(mean_lp) > 0 else 0

        # Base multiplier is 2.0. In wild regimes (CV > 0.005), expand to 3.0 to prevent whipsaw
        # In quiet regimes (CV < 0.001), shrink to 1.5 to catch micro-breakouts
        if cv > 0.005:
            multiplier = 3.0
        elif cv < 0.001:
            multiplier = 1.5
        else:
            ratio = (cv - 0.001) / 0.004
            multiplier = 1.5 + (ratio * 1.5)

        upper_band = mean_lp + multiplier * std_lp
        lower_band = mean_lp - multiplier * std_lp

        if log_price >= upper_band:
            db.log_system("STRATEGY_BREAKOUT", f"Log-price {log_price:.4f} broke Upper Band {upper_band:.4f} (mult={multiplier:.2f}) on {coin}. Signal: LONG")
            return "LONG"
        elif log_price <= lower_band:
            db.log_system("STRATEGY_BREAKOUT", f"Log-price {log_price:.4f} broke Lower Band {lower_band:.4f} (mult={multiplier:.2f}) on {coin}. Signal: SHORT")
            return "SHORT"
        return "FLAT"

    def calculate_orderbook_imbalance_signal(self, coin: str) -> str:
        """
        Orderbook Imbalance (OBI) Microstructure Signal.
        Uses Z-Score normalized order book imbalance to detect
        statistically significant buy/sell pressure anomalies.

        Academic backing: Cartea et al., "Algorithmic and High-Frequency Trading" (2015).
        OBI Z-Score > 2.0 indicates extreme buy pressure (LONG signal).
        OBI Z-Score < -2.0 indicates extreme sell pressure (SHORT signal).
        """
        state = tracker.get_market_state(coin)
        imbalance_zscore = state.get("imbalance_zscore", 0.0)

        # Only generate signal on statistically extreme imbalance deviations
        if imbalance_zscore > 2.0:
            db.log_system("STRATEGY_OBI", f"OBI Z-Score {imbalance_zscore:.2f} > 2.0 on {coin}. Extreme buy pressure. Signal: LONG")
            return "LONG"
        elif imbalance_zscore < -2.0:
            db.log_system("STRATEGY_OBI", f"OBI Z-Score {imbalance_zscore:.2f} < -2.0 on {coin}. Extreme sell pressure. Signal: SHORT")
            return "SHORT"
        return None  # No extreme imbalance = no signal

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
        grid_interval_pct = 0.005  # 0.5% spacing

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
        imbalance = state.get("imbalance", 0.0)  # Imbalance from L2

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
            "adverse_selection_halt": abs(imbalance) > 0.90  # Adverse Selection circuit breaker
        }


# Singleton instance
strategy_engine = StrategyEngine(asset_a="DOGE", asset_b="SUI")
