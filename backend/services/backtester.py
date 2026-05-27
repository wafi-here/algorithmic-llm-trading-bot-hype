import numpy as np
import pandas as pd
from datetime import datetime, timedelta

class HistoricalBacktester:
    def __init__(self):
        pass

    def generate_mock_history(self, days=5, ticks_per_day=288):
        """Generates highly realistic co-integrated synthetic prices for backtesting simulations."""
        np.random.seed(42)
        total_ticks = days * ticks_per_day
        
        # Base asset B (e.g. ETH) starts at 3500 and does a random walk
        eth_prices = [3500.0]
        for _ in range(total_ticks - 1):
            eth_prices.append(eth_prices[-1] + np.random.normal(0, 5.0))
            
        # Co-integrated asset A (BTC) spread has mean-reversion around hedge ratio 19.0
        btc_prices = []
        spread_mean = 370.0
        spread = spread_mean
        
        for eth in eth_prices:
            # Spread follows Ornstein-Uhlenbeck mean reverting process
            spread = spread + 0.15 * (spread_mean - spread) + np.random.normal(0, 8.0)
            btc_prices.append(19.0 * eth + spread)
            
        times = [datetime.now() - timedelta(minutes=5 * i) for i in range(total_ticks)][::-1]
        
        df = pd.DataFrame({
            "timestamp": times,
            "BTC": btc_prices,
            "ETH": eth_prices
        })
        return df

    def run_backtest(self, df_data: pd.DataFrame, entry_z=2.0, exit_z=0.5, sentiment_score=0.0, window=30):
        """
        Simulates statistical arbitrage pairs trading strategy performance.
        Returns: Dict containing performance summaries and chronological balance curve.
        """
        btc = df_data["BTC"].values
        eth = df_data["ETH"].values
        timestamps = df_data["timestamp"].tolist()
        
        hedge_ratio = 19.0
        
        spreads = btc - (hedge_ratio * eth)
        
        # Compute rolling stats in pandas
        df_spreads = pd.Series(spreads)
        rolling_mean = df_spreads.rolling(window=window, min_periods=5).mean()
        rolling_std = df_spreads.rolling(window=window, min_periods=5).std().fillna(0.0001)
        
        zscores = ((df_spreads - rolling_mean) / rolling_std).fillna(0.0).values
        
        # Apply sentiment skew logic
        entry_long = -entry_z
        entry_short = entry_z
        
        if sentiment_score > 0.3:
            entry_long = -1.5
            entry_short = 2.5
        elif sentiment_score < -0.3:
            entry_short = 1.5
            entry_long = -2.5
            
        # State indicators
        position = "FLAT" # "LONG" (Long BTC, Short ETH), "SHORT" (Short BTC, Long ETH), or "FLAT"
        entry_spread = 0.0
        
        initial_balance = 10000.0
        balance = initial_balance
        balances_history = []
        trades = []
        
        # We assume 1% allocation risk sizing
        risk_per_trade = 100.0 # $100 allocated
        
        for i in range(len(df_data)):
            z = zscores[i]
            s = spreads[i]
            t = timestamps[i]
            
            pnl = 0.0
            
            if position == "FLAT":
                if z > entry_short:
                    position = "SHORT"
                    entry_spread = s
                    # Record entry trade
                    trades.append({
                        "timestamp": t.isoformat() if isinstance(t, datetime) else str(t),
                        "side": "SHORT_PAIR",
                        "spread": s,
                        "zscore": z,
                        "pnl": 0.0
                    })
                elif z < entry_long:
                    position = "LONG"
                    entry_spread = s
                    # Record entry trade
                    trades.append({
                        "timestamp": t.isoformat() if isinstance(t, datetime) else str(t),
                        "side": "LONG_PAIR",
                        "spread": s,
                        "zscore": z,
                        "pnl": 0.0
                    })
            elif position == "LONG":
                # Exit when Z-Score goes back near mean
                if z > -exit_z:
                    position = "FLAT"
                    # Profit: spread went up (reverted to mean)
                    pnl = (s - entry_spread) * (risk_per_trade / eth[i])
                    balance += pnl
                    trades.append({
                        "timestamp": t.isoformat() if isinstance(t, datetime) else str(t),
                        "side": "FLAT_PAIR",
                        "spread": s,
                        "zscore": z,
                        "pnl": pnl
                    })
            elif position == "SHORT":
                # Exit when Z-Score goes back near mean
                if z < exit_z:
                    position = "FLAT"
                    # Profit: spread went down (reverted to mean)
                    pnl = (entry_spread - s) * (risk_per_trade / eth[i])
                    balance += pnl
                    trades.append({
                        "timestamp": t.isoformat() if isinstance(t, datetime) else str(t),
                        "side": "FLAT_PAIR",
                        "spread": s,
                        "zscore": z,
                        "pnl": pnl
                    })
                    
            balances_history.append({
                "timestamp": t.isoformat() if isinstance(t, datetime) else str(t),
                "balance": balance,
                "zscore": z,
                "spread": s
            })
            
        # Calculate stats
        balances = [b["balance"] for b in balances_history]
        returns = np.diff(balances) / balances[:-1] if len(balances) > 1 else [0]
        
        sharpe = 0.0
        if len(returns) > 1 and np.std(returns) > 0:
            # Annualized Sharpe (assuming 5-min intervals: 288 * 365 = 105120 per year)
            sharpe = (np.mean(returns) / np.std(returns)) * np.sqrt(105120)
            
        # Max Drawdown
        peak = balances[0]
        max_dd = 0.0
        for b in balances:
            if b > peak:
                peak = b
            dd = (peak - b) / peak
            if dd > max_dd:
                max_dd = dd
                
        # Win rate
        flat_trades = [tr for tr in trades if tr["side"] == "FLAT_PAIR"]
        winning_trades = len([tr for tr in flat_trades if tr["pnl"] > 0.0])
        win_rate = winning_trades / len(flat_trades) if len(flat_trades) > 0 else 0.5
        
        return {
            "total_trades": len(flat_trades),
            "win_rate": win_rate,
            "sharpe_ratio": float(sharpe),
            "max_drawdown": float(max_dd),
            "final_balance": balance,
            "total_pnl": balance - initial_balance,
            "balances": balances_history,
            "trades": trades
        }

backtester = HistoricalBacktester()
