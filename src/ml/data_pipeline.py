"""
Data Pipeline — Feature Extraction & Normalization
====================================================
Extracts training data from the platform's own health_metrics DB table,
converts raw observations into feature vectors, creates sliding-window
sequences, and handles normalization.

This is the bridge between the platform's database and PyTorch training.

Pipeline:
  DB (health_metrics) → raw observations → feature vectors → sequences → tensors
"""

import math
import json
import os
import logging
import numpy as np
from typing import Dict, List, Optional, Tuple, Any
from datetime import datetime

logger = logging.getLogger("mock_platform")


# ──────────────────────────────────────────────────────
# FEATURE EXTRACTION
# ──────────────────────────────────────────────────────

def compute_features(
    latency_ms: float,
    is_error: bool,
    response_size_bytes: int,
    recorded_at: datetime,
) -> np.ndarray:
    """
    Convert a single raw observation into a 5-dimensional feature vector.

    Features:
      [0] latency_ms          — raw (normalized later by FeatureScaler)
      [1] is_error            — binary (0.0 or 1.0)
      [2] response_size_bytes — raw (normalized later by FeatureScaler)
      [3] hour_sin            — sin(2π × hour / 24) — cyclical time encoding
      [4] hour_cos            — cos(2π × hour / 24) — cyclical time encoding

    Cyclical time encoding:
      Using sin/cos ensures that hour 23 is close to hour 0 in feature space.
      A linear encoding (hour=0, 1, 2, ..., 23) would make 0 and 23 seem
      maximally distant, which is wrong for time-of-day patterns.

    Args:
        latency_ms:          Request latency in milliseconds.
        is_error:            True if the status code was >= 400.
        response_size_bytes: Response body size in bytes.
        recorded_at:         Timestamp of the observation.

    Returns:
        NumPy array of shape (5,) with the feature values.
    """
    hour = recorded_at.hour + recorded_at.minute / 60.0  # Fractional hour
    hour_sin = math.sin(2.0 * math.pi * hour / 24.0)
    hour_cos = math.cos(2.0 * math.pi * hour / 24.0)

    return np.array([
        float(latency_ms),
        1.0 if is_error else 0.0,
        float(response_size_bytes),
        hour_sin,
        hour_cos,
    ], dtype=np.float32)


# ──────────────────────────────────────────────────────
# FEATURE SCALER
# ──────────────────────────────────────────────────────

class FeatureScaler:
    """
    Normalizes features using mean/std (StandardScaler equivalent).

    Only normalizes features 0 (latency) and 2 (response_size).
    Features 1 (is_error), 3 (hour_sin), and 4 (hour_cos) are
    already in bounded ranges and don't need normalization.

    Stores the learned mean/std so inference uses the SAME normalization
    as training — critical for correct anomaly scores.

    Persistence:
        Saved alongside the model weights as JSON so it survives restarts.
    """

    # Indices of features that need normalization
    _NORMALIZE_INDICES = [0, 2]  # latency_ms, response_size_bytes

    def __init__(self):
        self.means: Optional[np.ndarray] = None  # Shape: (n_features,)
        self.stds: Optional[np.ndarray] = None   # Shape: (n_features,)
        self._fitted = False

    def fit(self, data: np.ndarray) -> "FeatureScaler":
        """
        Learn mean and std from training data.

        Args:
            data: Array of shape (n_samples, n_features) — all feature vectors
                  concatenated.

        Returns:
            self (for chaining).
        """
        self.means = np.mean(data, axis=0)
        self.stds = np.std(data, axis=0)

        # Prevent division by zero for constant features
        self.stds[self.stds < 1e-8] = 1.0

        # Only apply normalization to selected features; reset others
        n_features = data.shape[1]
        for i in range(n_features):
            if i not in self._NORMALIZE_INDICES:
                self.means[i] = 0.0
                self.stds[i] = 1.0

        self._fitted = True
        logger.info(
            f"📊 FeatureScaler fitted: "
            f"latency μ={self.means[0]:.1f} σ={self.stds[0]:.1f}, "
            f"size μ={self.means[2]:.1f} σ={self.stds[2]:.1f}"
        )
        return self

    def transform(self, data: np.ndarray) -> np.ndarray:
        """
        Normalize data using learned mean/std.

        Args:
            data: Array of shape (n_samples, n_features) or (n_features,).

        Returns:
            Normalized array with same shape.
        """
        if not self._fitted:
            raise RuntimeError("FeatureScaler not fitted yet. Call fit() first.")
        return (data - self.means) / self.stds

    def fit_transform(self, data: np.ndarray) -> np.ndarray:
        """Convenience: fit and transform in one call."""
        self.fit(data)
        return self.transform(data)

    def save(self, path: str) -> None:
        """Save scaler parameters to JSON."""
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            json.dump({
                "means": self.means.tolist(),
                "stds": self.stds.tolist(),
            }, f, indent=2)
        logger.info(f"💾 FeatureScaler saved to {path}")

    def load(self, path: str) -> "FeatureScaler":
        """Load scaler parameters from JSON."""
        with open(path, "r") as f:
            data = json.load(f)
        self.means = np.array(data["means"], dtype=np.float32)
        self.stds = np.array(data["stds"], dtype=np.float32)
        self._fitted = True
        logger.info(f"📂 FeatureScaler loaded from {path}")
        return self

    @property
    def is_fitted(self) -> bool:
        return self._fitted


# ──────────────────────────────────────────────────────
# SEQUENCE CREATION
# ──────────────────────────────────────────────────────

def create_sequences(
    feature_vectors: np.ndarray,
    seq_len: int = 20,
) -> np.ndarray:
    """
    Create sliding-window sequences from a time-ordered array of feature vectors.

    For a sequence length of 20, given 100 observations, this produces
    81 overlapping windows: [0:20], [1:21], [2:22], ..., [80:100].

    Args:
        feature_vectors: Array of shape (n_samples, n_features), time-ordered.
        seq_len:         Number of timesteps per window.

    Returns:
        Array of shape (n_sequences, seq_len, n_features).
    """
    n_samples = len(feature_vectors)
    if n_samples < seq_len:
        return np.empty((0, seq_len, feature_vectors.shape[1]), dtype=np.float32)

    sequences = []
    for i in range(n_samples - seq_len + 1):
        sequences.append(feature_vectors[i:i + seq_len])

    return np.array(sequences, dtype=np.float32)


# ──────────────────────────────────────────────────────
# DATABASE EXTRACTION
# ──────────────────────────────────────────────────────

async def extract_training_data(
    min_observations_per_endpoint: int = 50,
    only_normal: bool = True,
) -> Dict[int, List[Dict[str, Any]]]:
    """
    Extract training data from the health_metrics DB table.

    Groups observations by endpoint_id, sorted by recorded_at.
    Optionally filters to only "normal" observations for training
    (health_score >= 80, no anomaly flags).

    Args:
        min_observations_per_endpoint: Skip endpoints with fewer observations.
        only_normal: If True, only include healthy observations for training.

    Returns:
        Dict mapping endpoint_id → list of observation dicts, sorted by time.
        Each dict has: latency_ms, is_error, response_size_bytes, recorded_at
    """
    from core.database import AsyncSessionLocal
    from core.models import HealthMetric
    from sqlalchemy import select

    result: Dict[int, List[Dict]] = {}

    async with AsyncSessionLocal() as session:
        query = select(HealthMetric).order_by(
            HealthMetric.endpoint_id,
            HealthMetric.recorded_at,
        )

        if only_normal:
            # Train only on normal traffic — the model learns what "normal" looks like
            query = query.where(
                HealthMetric.health_score >= 80.0,
                HealthMetric.latency_anomaly.is_(False),
                HealthMetric.error_spike.is_(False),
                HealthMetric.size_anomaly.is_(False),
            )

        res = await session.execute(query)
        rows = res.scalars().all()

        # Group by endpoint
        for row in rows:
            ep_id = row.endpoint_id
            if ep_id not in result:
                result[ep_id] = []
            result[ep_id].append({
                "latency_ms": row.latency_ms,
                "is_error": row.is_error,
                "response_size_bytes": row.response_size_bytes,
                "recorded_at": row.recorded_at,
            })

    # Filter out endpoints with too few observations
    result = {
        ep_id: obs
        for ep_id, obs in result.items()
        if len(obs) >= min_observations_per_endpoint
    }

    total_obs = sum(len(v) for v in result.values())
    logger.info(
        f"📊 Extracted {total_obs} observations from {len(result)} endpoints "
        f"(min {min_observations_per_endpoint} obs/endpoint, normal_only={only_normal})"
    )

    return result


def observations_to_features(
    observations: List[Dict[str, Any]],
) -> np.ndarray:
    """
    Convert a list of observation dicts to a feature matrix.

    Args:
        observations: List of dicts with latency_ms, is_error,
                      response_size_bytes, recorded_at.

    Returns:
        NumPy array of shape (n_observations, 5).
    """
    features = []
    for obs in observations:
        recorded_at = obs["recorded_at"]
        # Handle both datetime objects and strings
        if isinstance(recorded_at, str):
            try:
                recorded_at = datetime.fromisoformat(recorded_at)
            except ValueError:
                recorded_at = datetime.utcnow()

        feat = compute_features(
            latency_ms=obs["latency_ms"],
            is_error=obs["is_error"],
            response_size_bytes=obs["response_size_bytes"],
            recorded_at=recorded_at,
        )
        features.append(feat)

    return np.array(features, dtype=np.float32)


# ──────────────────────────────────────────────────────
# FULL PIPELINE
# ──────────────────────────────────────────────────────

async def build_training_dataset(
    seq_len: int = 20,
    min_observations: int = 50,
) -> Tuple[Optional[np.ndarray], Optional[FeatureScaler]]:
    """
    End-to-end pipeline: DB → features → normalize → sequences.

    Returns:
        (sequences, scaler) — or (None, None) if insufficient data.
        sequences shape: (n_sequences, seq_len, 5)
    """
    # Step 1: Extract from DB
    endpoint_data = await extract_training_data(
        min_observations_per_endpoint=min_observations,
        only_normal=True,
    )

    if not endpoint_data:
        logger.warning("⚠️ Not enough training data for LSTM. Need more traffic.")
        return None, None

    # Step 2: Convert to features (per endpoint)
    all_features = []
    for ep_id, observations in endpoint_data.items():
        features = observations_to_features(observations)
        all_features.append(features)

    # Step 3: Fit scaler on ALL data globally
    all_features_concat = np.concatenate(all_features, axis=0)
    scaler = FeatureScaler()
    scaler.fit(all_features_concat)

    # Step 4: Normalize and create sequences (per endpoint, then combine)
    all_sequences = []
    for features in all_features:
        normalized = scaler.transform(features)
        seqs = create_sequences(normalized, seq_len=seq_len)
        if len(seqs) > 0:
            all_sequences.append(seqs)

    if not all_sequences:
        logger.warning("⚠️ Not enough sequential data to create training windows.")
        return None, None

    combined = np.concatenate(all_sequences, axis=0)
    logger.info(
        f"✅ Training dataset built: {combined.shape[0]} sequences, "
        f"window={seq_len}, features={combined.shape[2]}"
    )

    return combined, scaler
