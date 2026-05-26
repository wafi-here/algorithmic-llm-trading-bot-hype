import sqlite3
import threading
import os
import json
from datetime import datetime
from backend.config import Config

class DatabaseManager:
    def __init__(self, db_path="trading_bot.db"):
        self.db_path = db_path
        self._local = threading.local()
        self._initialize_db()

    def _get_connection(self):
        conn = getattr(self._local, 'conn', None)
        if conn is None:
            conn = sqlite3.connect(self.db_path, timeout=30.0)
            conn.row_factory = sqlite3.Row
            self._local.conn = conn
        return conn

    def close(self):
        """Clean up the thread-local database connection."""
        conn = getattr(self._local, 'conn', None)
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
            self._local.conn = None

    def _initialize_db(self):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            
            # Table: Active Positions & Balance logs
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS position_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                    coin TEXT,
                    side TEXT,
                    size REAL,
                    entry_px REAL,
                    unrealized_pnl REAL
                )
            """)
            
            # Table: Executed Trades
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS executed_trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                    coin TEXT,
                    side TEXT,
                    size REAL,
                    price REAL,
                    pnl REAL,
                    cloid TEXT
                )
            """)
            
            # Table: Scraped News and Sentiment Scores
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS news_sentiment (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                    title TEXT,
                    source TEXT,
                    url TEXT,
                    raw_content TEXT,
                    sentiment_score REAL,
                    summary TEXT
                )
            """)
            
            # Table: Z-Score spreads for historical plotting
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS zscore_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                    asset_a TEXT,
                    asset_b TEXT,
                    price_a REAL,
                    price_b REAL,
                    spread REAL,
                    zscore REAL
                )
            """)
            
            # Table: System State / Logs
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS system_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                    level TEXT,
                    message TEXT
                )
            """)
            
            conn.commit()

    def log_system(self, level, message):
        print(f"[{level}] {message}")
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO system_logs (level, message) VALUES (?, ?)",
                (level, message)
            )
            conn.commit()

    def record_trade(self, coin, side, size, price, pnl=0.0, cloid=""):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO executed_trades (coin, side, size, price, pnl, cloid) VALUES (?, ?, ?, ?, ?, ?)",
                (coin, side, size, price, pnl, cloid)
            )
            conn.commit()

    def record_zscore(self, asset_a, asset_b, price_a, price_b, spread, zscore):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO zscore_history (asset_a, asset_b, price_a, price_b, spread, zscore) VALUES (?, ?, ?, ?, ?, ?)",
                (asset_a, asset_b, price_a, price_b, spread, zscore)
            )
            conn.commit()

    def record_sentiment(self, title, source, url, raw_content, sentiment_score, summary=""):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO news_sentiment (title, source, url, raw_content, sentiment_score, summary) VALUES (?, ?, ?, ?, ?, ?)",
                (title, source, url, raw_content, sentiment_score, summary)
            )
            conn.commit()

    def get_latest_zscores(self, limit=100):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM zscore_history ORDER BY timestamp DESC LIMIT ?", (limit,))
            rows = cursor.fetchall()
            return [dict(r) for r in rows]

    def get_recent_trades(self, limit=50):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM executed_trades ORDER BY timestamp DESC LIMIT ?", (limit,))
            rows = cursor.fetchall()
            return [dict(r) for r in rows]

    def get_latest_sentiment(self, limit=10):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM news_sentiment ORDER BY timestamp DESC LIMIT ?", (limit,))
            rows = cursor.fetchall()
            return [dict(r) for r in rows]
            
    def get_logs(self, limit=100):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM system_logs ORDER BY timestamp DESC LIMIT ?", (limit,))
            rows = cursor.fetchall()
            return [dict(r) for r in rows]

    def prune_stale_data(self, days_threshold=2):
        """Prunes historical Z-scores and system logs older than the threshold to save disk space."""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                # Prune Z-Score History
                cursor.execute(
                    "DELETE FROM zscore_history WHERE timestamp < datetime('now', ?)",
                    (f"-{days_threshold} days",)
                )
                pruned_zscores = cursor.rowcount
                
                # Prune System Logs
                cursor.execute(
                    "DELETE FROM system_logs WHERE timestamp < datetime('now', ?)",
                    (f"-{days_threshold} days",)
                )
                pruned_logs = cursor.rowcount
                
                conn.commit()
                if pruned_zscores > 0 or pruned_logs > 0:
                    self.log_system("PRUNE", f"Database pruned. Removed {pruned_zscores} zscore ticks and {pruned_logs} system log rows.")
                return True
        except Exception as e:
            print(f"[ERROR] Failed to prune database: {str(e)}")
            return False

    def get_trade_performance_stats(self, limit=30):
        """Calculates rolling performance statistics for Kelly Criterion position sizing."""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT pnl FROM executed_trades WHERE pnl IS NOT NULL AND pnl != 0.0 ORDER BY timestamp DESC LIMIT ?",
                    (limit,)
                )
                rows = cursor.fetchall()
                if not rows:
                    return {"win_rate": 0.5, "payoff_ratio": 1.0, "total_trades": 0}
                
                pnls = [float(r["pnl"]) for r in rows]
                wins = [p for p in pnls if p > 0.0]
                losses = [p for p in pnls if p < 0.0]
                
                win_count = len(wins)
                total_trades = len(pnls)
                
                win_rate = win_count / total_trades if total_trades > 0 else 0.5
                
                avg_win = sum(wins) / win_count if win_count > 0 else 1.0
                avg_loss = abs(sum(losses) / len(losses)) if len(losses) > 0 else 1.0
                
                payoff_ratio = avg_win / avg_loss if avg_loss > 0.0 else 1.0
                
                return {
                    "win_rate": win_rate,
                    "payoff_ratio": payoff_ratio,
                    "total_trades": total_trades
                }
        except Exception as e:
            self.log_system("ERROR", f"Failed to compute trade statistics: {str(e)}")
            return {"win_rate": 0.5, "payoff_ratio": 1.0, "total_trades": 0}

db = DatabaseManager()

