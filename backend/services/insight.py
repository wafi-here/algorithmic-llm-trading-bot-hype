"""
Structured Insight System.
Inspired by QuantConnect Lean's Alpha Insight framework.

Replaces bare string signals ("LONG"/"SHORT"/"FLAT") with structured Insight
objects containing direction, confidence, magnitude, period, and source.
The InsightManager aggregates and expires insights, producing weighted consensus.

Key Lean concepts implemented:
- Insights have a confidence score (0.0-1.0) and magnitude (expected % move)
- Insights expire after their period_seconds — stale signals auto-purge
- Consensus is computed by confidence-weighted voting across all active insights
- Position sizing can be modulated by consensus confidence
"""

import time
from dataclasses import dataclass, field
from backend.services.database import db


@dataclass
class Insight:
    """
    A structured trading signal with metadata.
    
    This is the Python equivalent of Lean's Insight class.
    Unlike bare strings, this carries the statistical confidence
    and expected magnitude that downstream systems need for
    optimal position sizing and risk management.
    """
    coin: str
    direction: str               # "LONG", "SHORT", or "FLAT"
    confidence: float            # 0.0 to 1.0 — statistical confidence
    magnitude: float             # Expected % price move (signed)
    period_seconds: int          # How long this insight is valid
    source: str                  # Which strategy generated it
    created_at: float = field(default_factory=time.time)
    
    @property
    def is_expired(self) -> bool:
        """Returns True if this insight has exceeded its validity period."""
        return (time.time() - self.created_at) > self.period_seconds
    
    @property
    def remaining_seconds(self) -> float:
        """Returns seconds until this insight expires."""
        return max(0.0, self.period_seconds - (time.time() - self.created_at))
    
    @property
    def direction_sign(self) -> int:
        """Returns +1 for LONG, -1 for SHORT, 0 for FLAT."""
        if self.direction == "LONG":
            return 1
        elif self.direction == "SHORT":
            return -1
        return 0
    
    def to_dict(self) -> dict:
        """Serializes for API/dashboard display."""
        return {
            "coin": self.coin,
            "direction": self.direction,
            "confidence": round(self.confidence, 4),
            "magnitude": round(self.magnitude, 6),
            "period_seconds": self.period_seconds,
            "source": self.source,
            "remaining_seconds": round(self.remaining_seconds),
            "is_expired": self.is_expired
        }


@dataclass
class InsightConsensus:
    """
    Aggregated consensus from multiple insights for a single coin.
    This replaces the integer-strength ranking system.
    """
    coin: str
    direction: str               # Consensus direction
    confidence: float            # Weighted average confidence
    magnitude: float             # Weighted average magnitude
    sources: list                # List of contributing strategy names
    n_insights: int              # Number of active insights
    
    @property
    def strength(self) -> float:
        """
        Continuous strength score replacing the old integer system.
        Range: 0.0 to ~4.0 (confidence × source count)
        """
        return self.confidence * self.n_insights


class InsightManager:
    """
    Manages the lifecycle of trading insights.
    
    Core responsibilities (mirroring Lean's InsightManager):
    1. Store active insights per coin
    2. Automatically expire stale insights
    3. Compute confidence-weighted consensus per coin
    4. Provide ranked signal list for the trading loop
    """
    
    def __init__(self):
        # Dict of coin -> list of active Insight objects
        self._insights: dict[str, list[Insight]] = {}
    
    def emit(self, insight: Insight) -> None:
        """
        Emits a new insight. If the same source already has an active
        insight for this coin, it replaces it (latest signal wins).
        """
        if insight.coin not in self._insights:
            self._insights[insight.coin] = []
        
        # Remove any existing insight from the same source for this coin
        self._insights[insight.coin] = [
            i for i in self._insights[insight.coin]
            if i.source != insight.source
        ]
        
        self._insights[insight.coin].append(insight)
    
    def expire_stale(self) -> int:
        """
        Removes all expired insights across all coins.
        Returns: number of insights expired.
        """
        expired_count = 0
        for coin in list(self._insights.keys()):
            before = len(self._insights[coin])
            self._insights[coin] = [
                i for i in self._insights[coin] if not i.is_expired
            ]
            expired_count += before - len(self._insights[coin])
            
            # Clean up empty lists
            if not self._insights[coin]:
                del self._insights[coin]
        
        return expired_count
    
    def get_active_insights(self, coin: str) -> list[Insight]:
        """Returns all non-expired insights for a given coin."""
        if coin not in self._insights:
            return []
        return [i for i in self._insights[coin] if not i.is_expired]
    
    def get_consensus(self, coin: str) -> InsightConsensus | None:
        """
        Computes the confidence-weighted consensus for a coin.
        
        Algorithm (inspired by Lean's portfolio construction weighting):
        1. Collect all active (non-expired) insights for the coin
        2. Compute weighted directional score: Σ(confidence_i × direction_sign_i)
        3. Determine consensus direction from the sign of the weighted score
        4. Aggregate confidence = |weighted_score| / n_insights
        5. Aggregate magnitude = confidence-weighted average of magnitudes
        
        Returns None if no active insights exist.
        """
        active = self.get_active_insights(coin)
        if not active:
            return None
        
        # Filter out FLAT signals for directional consensus
        directional = [i for i in active if i.direction != "FLAT"]
        flat_signals = [i for i in active if i.direction == "FLAT"]
        
        # If only FLAT signals exist, return FLAT consensus
        if not directional and flat_signals:
            avg_confidence = sum(i.confidence for i in flat_signals) / len(flat_signals)
            return InsightConsensus(
                coin=coin,
                direction="FLAT",
                confidence=avg_confidence,
                magnitude=0.0,
                sources=[i.source for i in flat_signals],
                n_insights=len(flat_signals)
            )
        
        if not directional:
            return None
        
        # Confidence-weighted directional voting
        weighted_score = sum(
            i.confidence * i.direction_sign for i in directional
        )
        total_confidence = sum(i.confidence for i in directional)
        
        # Consensus direction from weighted vote
        if weighted_score > 0:
            consensus_dir = "LONG"
        elif weighted_score < 0:
            consensus_dir = "SHORT"
        else:
            return None  # Perfect tie — no consensus
        
        # Aggregate confidence: how strongly the signals agree
        # normalized by number of signals (max 1.0 per signal)
        n = len(directional)
        consensus_confidence = min(1.0, abs(weighted_score) / n) if n > 0 else 0.0
        
        # Confidence-weighted magnitude
        if total_confidence > 0:
            consensus_magnitude = sum(
                i.confidence * abs(i.magnitude) for i in directional
            ) / total_confidence
        else:
            consensus_magnitude = 0.0
        
        # Determine which sources agree with consensus
        agreeing_sources = [
            i.source for i in directional
            if i.direction == consensus_dir
        ]
        
        return InsightConsensus(
            coin=coin,
            direction=consensus_dir,
            confidence=consensus_confidence,
            magnitude=consensus_magnitude,
            sources=agreeing_sources,
            n_insights=n
        )
    
    def get_ranked_signals(self, coins: set[str]) -> list[InsightConsensus]:
        """
        Returns consensus signals for all coins, ranked by strength.
        This replaces the old ranked_signals list in main.py.
        """
        # First purge expired insights
        self.expire_stale()
        
        signals = []
        for coin in coins:
            consensus = self.get_consensus(coin)
            if consensus and consensus.direction != "FLAT":
                signals.append(consensus)
        
        # Sort by strength descending (confidence × source count)
        signals.sort(key=lambda s: s.strength, reverse=True)
        return signals
    
    def get_flat_signals(self, coins: set[str]) -> list[InsightConsensus]:
        """Returns FLAT consensus signals for exit processing."""
        self.expire_stale()
        
        signals = []
        for coin in coins:
            consensus = self.get_consensus(coin)
            if consensus and consensus.direction == "FLAT":
                signals.append(consensus)
        
        return signals
    
    def clear_coin(self, coin: str) -> None:
        """Clears all insights for a coin (called after position exit)."""
        if coin in self._insights:
            del self._insights[coin]
    
    def get_all_active(self) -> list[dict]:
        """Returns all active insights for dashboard display."""
        self.expire_stale()
        result = []
        for coin, insights in self._insights.items():
            for i in insights:
                if not i.is_expired:
                    result.append(i.to_dict())
        return result


insight_manager = InsightManager()
