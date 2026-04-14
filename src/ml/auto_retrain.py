"""
Auto-Retrain — Background Model Training Loop
================================================
Periodically checks if enough new data has accumulated and triggers
retraining of the LSTM Autoencoder.

Self-improving loop:
  1. Platform proxies API traffic → observations stored in health_metrics
  2. Every RETRAIN_INTERVAL_SECONDS, this loop checks for new data
  3. If enough data exists, trains/retrains the LSTM
  4. Hot-swaps the new model into the running AnomalyPredictor
  5. Better anomaly detection → more accurate health scores

The first training is triggered when MIN_TRAINING_OBSERVATIONS is reached.
Subsequent retrains happen when NEW_DATA_THRESHOLD new observations arrive.
"""

import os
import logging
import asyncio
from datetime import datetime
from typing import Optional

logger = logging.getLogger("mock_platform")

# ── Configuration ──────────────────────────────────────────────────────────────

# Minimum observations across all endpoints before first training
MIN_TRAINING_OBSERVATIONS = 30

# Retrain when this many new observations have accumulated since last training
NEW_DATA_THRESHOLD = 20

# Check for retraining every N seconds (default: 60 seconds for demo)
RETRAIN_INTERVAL_SECONDS = 60

# Maximum training epochs per run
MAX_EPOCHS = 50

# Track global training state
IS_TRAINING: bool = False

# Track observations since last training
_last_training_count: int = 0
_last_training_time: Optional[datetime] = None


async def check_and_retrain() -> Optional[dict]:
    """
    Check if enough new data has accumulated, and retrain if so.

    This function is meant to be called periodically from a background loop.
    It is safe to call frequently — it will no-op if conditions aren't met.

    Returns:
        Training result dict if training was performed, None otherwise.
    """
    global _last_training_count, _last_training_time, IS_TRAINING

    try:
        # Check current data volume
        from core.database import AsyncSessionLocal
        from core.models import HealthMetric
        from sqlalchemy import select, func

        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(func.count()).select_from(HealthMetric)
                .where(HealthMetric.health_score >= 80.0)
            )
            total_normal_observations = result.scalar() or 0

        # ── Decision: should we train? ────────────────────────────────
        if total_normal_observations < MIN_TRAINING_OBSERVATIONS:
            logger.debug(
                f"🧠 LSTM: Not enough data yet "
                f"({total_normal_observations}/{MIN_TRAINING_OBSERVATIONS} normal observations)"
            )
            return None

        new_observations = total_normal_observations - _last_training_count

        if _last_training_count > 0 and new_observations < NEW_DATA_THRESHOLD:
            logger.debug(
                f"🧠 LSTM: Waiting for more data "
                f"({new_observations}/{NEW_DATA_THRESHOLD} new observations since last training)"
            )
            return None

        # ── Train! ─────────────────────────────────────────────────────
        logger.info(
            f"🧠 LSTM: Triggering {'initial' if _last_training_count == 0 else 're'}training "
            f"({total_normal_observations} total observations, "
            f"{new_observations} new since last run)"
        )

        IS_TRAINING = True
        try:
            result = await _run_training()

            if result and result.get("status") == "success":
                _last_training_count = total_normal_observations
                _last_training_time = datetime.utcnow()

                # Hot-swap model into the running predictor
                from core.state import lstm_predictor
                lstm_predictor.reload_model()

                logger.info(
                    f"✅ LSTM Autoencoder {'trained' if new_observations == total_normal_observations else 'retrained'} "
                    f"and hot-swapped! Threshold={result['threshold']:.6f}"
                )
            return result
        finally:
            IS_TRAINING = False

    except Exception as e:
        logger.error(f"❌ Auto-retrain check failed: {e}")
        return None


async def _run_training() -> Optional[dict]:
    """
    Execute the full training pipeline in a thread (to not block the event loop).

    Steps:
      1. Extract data from DB (async)
      2. Build training dataset (numpy)
      3. Train LSTM (PyTorch, synchronous but in thread)
      4. Save model to disk
    """
    from ml.data_pipeline import build_training_dataset
    from ml.trainer import LSTMTrainer
    from ml.lstm_model import SEQ_LEN

    # Step 1-2: Extract and prepare data
    sequences, scaler = await build_training_dataset(
        seq_len=SEQ_LEN,
        min_observations=30,
    )

    if sequences is None or scaler is None:
        logger.warning("⚠️ LSTM: Could not build training dataset.")
        return None

    # Step 3-4: Train model (CPU-bound, run in thread to avoid blocking)
    def _train_sync():
        trainer = LSTMTrainer()
        return trainer.train(sequences, scaler, epochs=MAX_EPOCHS)

    result = await asyncio.to_thread(_train_sync)
    return result


async def retrain_loop():
    """
    Background loop that periodically checks for retraining.
    Should be started as an asyncio task in mock_server.py.
    """
    logger.info(
        f"🔄 LSTM auto-retrain loop started "
        f"(check every {RETRAIN_INTERVAL_SECONDS}s, "
        f"min {MIN_TRAINING_OBSERVATIONS} observations for first train)"
    )

    # Wait a bit on startup before first check
    await asyncio.sleep(30)

    while True:
        try:
            await check_and_retrain()
        except Exception as e:
            logger.error(f"❌ LSTM retrain loop error: {e}")

        await asyncio.sleep(RETRAIN_INTERVAL_SECONDS)
