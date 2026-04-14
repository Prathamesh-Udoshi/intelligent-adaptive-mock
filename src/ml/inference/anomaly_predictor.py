"""
Anomaly Predictor — Real-Time LSTM Inference
==============================================
Maintains per-endpoint sliding windows of recent observations and runs
the trained LSTM Autoencoder to detect multi-signal anomalies.

This module is the runtime counterpart of trainer.py. It:
  1. Loads the trained model, scaler, and threshold from disk
  2. Maintains a per-endpoint buffer of the last N observations
  3. On each new observation, runs inference and returns anomaly scores

Integration:
  Used by proxy.py (feed observations) and health_monitor.py (check predictions).

Graceful degradation:
  If no model is trained or PyTorch is not installed, all methods return None.
  The platform continues to work with Welford-only detection.
"""

import os
import json
import math
import logging
import numpy as np
from collections import defaultdict, deque
from typing import Dict, Optional, Any
from datetime import datetime

logger = logging.getLogger("mock_platform")

# ── Paths ──────────────────────────────────────────────────────────────────────
_BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # src/
_MODEL_DIR = os.path.join(_BASE_DIR, "..", "data", "ml_models", "lstm_anomaly")

_MODEL_WEIGHTS_PATH = os.path.join(_MODEL_DIR, "model.pt")
_SCALER_PATH = os.path.join(_MODEL_DIR, "scaler.json")
_METADATA_PATH = os.path.join(_MODEL_DIR, "metadata.json")


class AnomalyPredictor:
    """
    Real-time anomaly predictor using the trained LSTM Autoencoder.

    Maintains a sliding window buffer per endpoint. When enough observations
    accumulate (>= seq_len), runs the LSTM to compute a reconstruction error.
    If the error exceeds the learned threshold → anomaly detected.

    Thread safety:
        The predictor is used from async FastAPI handlers. The sliding window
        buffers use collections.deque which is thread-safe for append/read.
        Model inference is read-only (no gradient computation).

    Example:
        predictor = AnomalyPredictor()
        predictor.load_model()

        # After each proxied request:
        predictor.feed("/analyze", latency_ms=850, is_error=False,
                        response_size=2048, recorded_at=datetime.utcnow())

        result = predictor.predict("/analyze")
        # result = {
        #     "is_anomaly": True,
        #     "anomaly_score": 0.0342,
        #     "threshold": 0.0198,
        #     "confidence": 0.73,
        #     "model_status": "active",
        #     "buffer_size": 20
        # }
    """

    def __init__(self, model_dir: str = _MODEL_DIR):
        self._model_dir = model_dir
        self._model = None
        self._scaler = None
        self._threshold: float = 0.0
        self._metadata: Dict = {}
        self._seq_len: int = 20
        self._loaded = False

        # Per-endpoint sliding window buffers
        # Maps endpoint_path → deque of feature vectors (np.ndarray of shape (5,))
        self._buffers: Dict[str, deque] = defaultdict(lambda: deque(maxlen=self._seq_len))

        # Per-endpoint recent prediction cache (avoid re-computing on every call)
        self._prediction_cache: Dict[str, Dict[str, Any]] = {}

        # Stats for monitoring
        self._total_predictions = 0
        self._total_anomalies = 0

        # Try to load model on init
        self.load_model()

    def load_model(self) -> bool:
        """
        Load trained model, scaler, and threshold from disk.

        Returns:
            True if model loaded successfully, False otherwise.
        """
        try:
            import torch
            from ml.lstm_model import LSTMAutoencoder
            from ml.data_pipeline import FeatureScaler
        except ImportError as e:
            logger.info(f"ℹ️ LSTM dependencies not available ({e}). ML anomaly detection disabled.")
            return False

        # Check all required files exist
        if not all(os.path.exists(p) for p in [_MODEL_WEIGHTS_PATH, _SCALER_PATH, _METADATA_PATH]):
            logger.info(
                "ℹ️ No trained LSTM model found. "
                "ML anomaly detection will activate after first training run."
            )
            return False

        try:
            # Load metadata
            with open(_METADATA_PATH, "r") as f:
                self._metadata = json.load(f)

            self._threshold = self._metadata.get("threshold", 0.01)
            self._seq_len = self._metadata.get("seq_len", 20)

            # Rebuild model with saved config
            self._model = LSTMAutoencoder(
                n_features=self._metadata.get("n_features", 5),
                hidden_size=self._metadata.get("hidden_size", 32),
                n_layers=self._metadata.get("n_layers", 2),
                bottleneck_size=self._metadata.get("bottleneck_size", 16),
                seq_len=self._seq_len,
            )

            # Load weights
            state_dict = torch.load(_MODEL_WEIGHTS_PATH, map_location="cpu", weights_only=True)
            self._model.load_state_dict(state_dict)
            self._model.eval()

            # Load scaler
            self._scaler = FeatureScaler()
            self._scaler.load(_SCALER_PATH)

            # Update buffer sizes
            for key in self._buffers:
                old_buf = self._buffers[key]
                new_buf = deque(old_buf, maxlen=self._seq_len)
                self._buffers[key] = new_buf

            self._loaded = True
            param_count = self._model.count_parameters()
            logger.info(
                f"🧠 LSTM Anomaly Predictor loaded! "
                f"({param_count:,} params, threshold={self._threshold:.6f}, "
                f"trained on {self._metadata.get('n_sequences_trained', '?')} sequences)"
            )
            return True

        except Exception as e:
            logger.warning(f"⚠️ Failed to load LSTM model: {e}")
            self._loaded = False
            return False

    @property
    def is_active(self) -> bool:
        """True if a trained model is loaded and ready for inference."""
        return self._loaded and self._model is not None

    def feed(
        self,
        endpoint: str,
        latency_ms: float,
        is_error: bool,
        response_size: int,
        recorded_at: Optional[datetime] = None,
    ) -> None:
        """
        Feed a new observation into the endpoint's sliding window buffer.

        This should be called after EVERY proxied request, regardless of
        whether the model is loaded. The buffer accumulates data so that
        when the model IS loaded, predictions start immediately.

        Args:
            endpoint:       Normalized path (e.g., "/analyze").
            latency_ms:     Request latency in milliseconds.
            is_error:       True if status code >= 400.
            response_size:  Response body size in bytes.
            recorded_at:    Timestamp (defaults to now).
        """
        if recorded_at is None:
            recorded_at = datetime.utcnow()

        from ml.data_pipeline import compute_features
        features = compute_features(latency_ms, is_error, response_size, recorded_at)

        # Normalize if scaler is available
        if self._scaler is not None and self._scaler.is_fitted:
            features = self._scaler.transform(features)

        self._buffers[endpoint].append(features)

    def predict(self, endpoint: str) -> Optional[Dict[str, Any]]:
        """
        Run LSTM inference on the endpoint's current sliding window.

        Returns:
            None if the model isn't loaded or buffer is too small.
            Dict with anomaly prediction details otherwise:
            {
                "is_anomaly":   bool,   — True if reconstruction error > threshold
                "anomaly_score": float, — Raw reconstruction error (MSE)
                "threshold":    float,  — The learned anomaly threshold
                "confidence":   float,  — How far above/below threshold (0-1 scale)
                "severity":     str,    — "none", "medium", "high"
                "model_status": str,    — "active", "warming_up", "inactive"
                "buffer_size":  int,    — Current observations in buffer
                "message":      str,    — Human-readable description
            }
        """
        if not self._loaded or self._model is None:
            return {
                "is_anomaly": False,
                "anomaly_score": 0.0,
                "threshold": 0.0,
                "confidence": 0.0,
                "severity": "none",
                "model_status": "inactive",
                "buffer_size": len(self._buffers.get(endpoint, [])),
                "message": "LSTM model not trained yet",
            }

        buffer = self._buffers.get(endpoint, deque())
        if len(buffer) < self._seq_len:
            return {
                "is_anomaly": False,
                "anomaly_score": 0.0,
                "threshold": self._threshold,
                "confidence": 0.0,
                "severity": "none",
                "model_status": "warming_up",
                "buffer_size": len(buffer),
                "message": f"Collecting data ({len(buffer)}/{self._seq_len} observations)",
            }

        # Build input tensor from buffer
        try:
            import torch

            sequence = np.array(list(buffer), dtype=np.float32)
            x = torch.FloatTensor(sequence).unsqueeze(0)  # (1, seq_len, n_features)

            # Run inference
            with torch.no_grad():
                error = self._model.get_reconstruction_error(x).item()

            is_anomaly = error > self._threshold
            self._total_predictions += 1

            # Compute confidence (how far above/below threshold)
            if self._threshold > 0:
                ratio = error / self._threshold
                if ratio <= 1.0:
                    confidence = 0.0  # Below threshold
                else:
                    # Sigmoid-like scaling: ratio of 2x threshold → ~0.73 confidence
                    confidence = min(1.0, 1.0 - 1.0 / ratio)
            else:
                confidence = 0.0

            # Severity classification
            if not is_anomaly:
                severity = "none"
            elif error > self._threshold * 3.0:
                severity = "high"
            elif error > self._threshold * 1.5:
                severity = "medium"
            else:
                severity = "low"

            if is_anomaly:
                self._total_anomalies += 1
                message = (
                    f"🧠 LSTM detected anomaly: reconstruction error {error:.4f} "
                    f"exceeds threshold {self._threshold:.4f} "
                    f"({error/self._threshold:.1f}x)"
                )
            else:
                message = (
                    f"Normal pattern (error={error:.4f}, "
                    f"threshold={self._threshold:.4f})"
                )

            result = {
                "is_anomaly": is_anomaly,
                "anomaly_score": round(error, 6),
                "threshold": round(self._threshold, 6),
                "confidence": round(confidence, 3),
                "severity": severity,
                "model_status": "active",
                "buffer_size": len(buffer),
                "message": message,
            }

            # Update cache
            self._prediction_cache[endpoint] = result
            return result

        except Exception as e:
            logger.error(f"❌ LSTM inference failed for {endpoint}: {e}")
            return None

    def get_cached_prediction(self, endpoint: str) -> Optional[Dict[str, Any]]:
        """Return the last prediction for an endpoint without re-running inference."""
        return self._prediction_cache.get(endpoint)

    def get_stats(self) -> Dict[str, Any]:
        """Return predictor status for the admin dashboard."""
        return {
            "model_loaded": self._loaded,
            "total_predictions": self._total_predictions,
            "total_anomalies": self._total_anomalies,
            "anomaly_rate": (
                round(self._total_anomalies / self._total_predictions, 4)
                if self._total_predictions > 0 else 0.0
            ),
            "threshold": self._threshold,
            "endpoints_tracked": len(self._buffers),
            "seq_len": self._seq_len,
            "trained_at": self._metadata.get("trained_at", "never"),
            "training_sequences": self._metadata.get("n_sequences_trained", 0),
        }

    def reload_model(self) -> bool:
        """
        Reload the model from disk (called after retraining).
        Preserves existing endpoint buffers.
        """
        logger.info("🔄 Reloading LSTM model from disk...")
        return self.load_model()
