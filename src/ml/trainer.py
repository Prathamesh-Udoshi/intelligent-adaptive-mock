"""
LSTM Trainer — Training Loop, Threshold Learning & Persistence
================================================================
Handles the full training lifecycle:
  1. Split data into train/validation
  2. Train the LSTM Autoencoder to minimize reconstruction error
  3. Compute the anomaly threshold from the training error distribution
  4. Save model weights, scaler, and threshold for inference

The anomaly threshold is the KEY insight: we compute the reconstruction
error on normal data and set the threshold at the 97th percentile.
Anything above that threshold during inference = anomaly.
"""

import os
import json
import logging
import numpy as np

import torch
import torch.nn as nn
from torch.utils.data import DataLoader, TensorDataset

from ml.lstm_model import LSTMAutoencoder, N_FEATURES, SEQ_LEN, HIDDEN_SIZE, N_LAYERS, BOTTLENECK_SIZE

logger = logging.getLogger("mock_platform")

# ── Paths ──────────────────────────────────────────────────────────────────────
_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # src/
_MODEL_DIR = os.path.join(_BASE_DIR, "..", "data", "ml_models", "lstm_anomaly")

MODEL_WEIGHTS_PATH = os.path.join(_MODEL_DIR, "model.pt")
SCALER_PATH = os.path.join(_MODEL_DIR, "scaler.json")
METADATA_PATH = os.path.join(_MODEL_DIR, "metadata.json")


class LSTMTrainer:
    """
    Trains the LSTM Autoencoder and computes the anomaly detection threshold.

    Training strategy:
      - Loss function: MSE (mean squared error between input and reconstruction)
      - Optimizer: Adam with learning rate scheduling
      - Early stopping: if validation loss doesn't improve for `patience` epochs
      - Threshold: 97th percentile of reconstruction errors on the training set

    Usage:
        trainer = LSTMTrainer()
        result = trainer.train(sequences, scaler, epochs=50)
        # Model is saved automatically to data/ml_models/lstm_anomaly/
    """

    def __init__(
        self,
        model_dir: str = _MODEL_DIR,
        learning_rate: float = 1e-3,
        batch_size: int = 32,
        patience: int = 10,
        threshold_percentile: float = 97.0,
    ):
        self.model_dir = model_dir
        self.learning_rate = learning_rate
        self.batch_size = batch_size
        self.patience = patience
        self.threshold_percentile = threshold_percentile

        # Will be set during training
        self.model: LSTMAutoencoder = None
        self.threshold: float = 0.0
        self.train_losses: list = []
        self.val_losses: list = []

    def train(
        self,
        sequences: np.ndarray,
        scaler,
        epochs: int = 50,
        val_split: float = 0.2,
    ) -> dict:
        """
        Full training pipeline.

        Args:
            sequences: Array of shape (n_sequences, seq_len, n_features).
                       Already normalized by FeatureScaler.
            scaler:    The FeatureScaler used for normalization (will be saved).
            epochs:    Maximum training epochs.
            val_split: Fraction of data to use for validation.

        Returns:
            Training result dict with metrics and paths.
        """
        logger.info(f"🧠 Starting LSTM Autoencoder training...")
        logger.info(f"   Dataset: {sequences.shape[0]} sequences")
        logger.info(f"   Window: {sequences.shape[1]} timesteps × {sequences.shape[2]} features")

        # ── Split into train/validation ──────────────────────────────
        n_samples = len(sequences)
        n_val = max(1, int(n_samples * val_split))
        n_train = n_samples - n_val

        # Shuffle before splitting
        indices = np.random.permutation(n_samples)
        train_data = sequences[indices[:n_train]]
        val_data = sequences[indices[n_train:]]

        logger.info(f"   Train: {n_train} samples, Validation: {n_val} samples")

        # Convert to PyTorch tensors
        train_tensor = torch.FloatTensor(train_data)
        val_tensor = torch.FloatTensor(val_data)

        train_loader = DataLoader(
            TensorDataset(train_tensor, train_tensor),  # Input = Target (autoencoder)
            batch_size=self.batch_size,
            shuffle=True,
        )
        val_loader = DataLoader(
            TensorDataset(val_tensor, val_tensor),
            batch_size=self.batch_size,
            shuffle=False,
        )

        # ── Initialize model ────────────────────────────────────────
        self.model = LSTMAutoencoder(
            n_features=sequences.shape[2],
            hidden_size=HIDDEN_SIZE,
            n_layers=N_LAYERS,
            bottleneck_size=BOTTLENECK_SIZE,
            seq_len=sequences.shape[1],
        )

        param_count = self.model.count_parameters()
        logger.info(f"   Model parameters: {param_count:,}")

        # ── Training setup ──────────────────────────────────────────
        criterion = nn.MSELoss()
        optimizer = torch.optim.Adam(self.model.parameters(), lr=self.learning_rate)
        scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(
            optimizer, mode="min", factor=0.5, patience=5
        )

        best_val_loss = float("inf")
        patience_counter = 0
        self.train_losses = []
        self.val_losses = []

        # ── Training loop ───────────────────────────────────────────
        for epoch in range(epochs):
            # Train
            self.model.train()
            train_loss = 0.0
            for batch_x, batch_y in train_loader:
                optimizer.zero_grad()
                output = self.model(batch_x)
                loss = criterion(output, batch_y)
                loss.backward()
                torch.nn.utils.clip_grad_norm_(self.model.parameters(), max_norm=1.0)
                optimizer.step()
                train_loss += loss.item() * len(batch_x)
            train_loss /= n_train

            # Validate
            self.model.eval()
            val_loss = 0.0
            with torch.no_grad():
                for batch_x, batch_y in val_loader:
                    output = self.model(batch_x)
                    loss = criterion(output, batch_y)
                    val_loss += loss.item() * len(batch_x)
            val_loss /= n_val

            self.train_losses.append(train_loss)
            self.val_losses.append(val_loss)

            scheduler.step(val_loss)

            # Early stopping
            if val_loss < best_val_loss:
                best_val_loss = val_loss
                patience_counter = 0
                # Save best model state
                best_state = {k: v.clone() for k, v in self.model.state_dict().items()}
            else:
                patience_counter += 1

            if (epoch + 1) % 10 == 0 or epoch == 0:
                lr = optimizer.param_groups[0]["lr"]
                logger.info(
                    f"   Epoch {epoch+1:3d}/{epochs}: "
                    f"train_loss={train_loss:.6f}, "
                    f"val_loss={val_loss:.6f}, "
                    f"lr={lr:.6f}"
                )

            if patience_counter >= self.patience:
                logger.info(f"   ⏹️  Early stopping at epoch {epoch+1} (patience={self.patience})")
                break

        # Restore best model
        self.model.load_state_dict(best_state)
        self.model.eval()

        # ── Compute anomaly threshold ───────────────────────────────
        # Reconstruction error on training data → threshold at Nth percentile
        all_tensor = torch.FloatTensor(sequences)
        with torch.no_grad():
            errors = self.model.get_reconstruction_error(all_tensor).numpy()

        self.threshold = float(np.percentile(errors, self.threshold_percentile))

        logger.info(
            f"   📏 Anomaly threshold: {self.threshold:.6f} "
            f"({self.threshold_percentile}th percentile of training errors)"
        )
        logger.info(
            f"   Error stats: min={errors.min():.6f}, "
            f"mean={errors.mean():.6f}, max={errors.max():.6f}"
        )

        # ── Save everything ─────────────────────────────────────────
        self._save(scaler, sequences.shape, len(self.train_losses))

        result = {
            "status": "success",
            "epochs_trained": len(self.train_losses),
            "best_val_loss": best_val_loss,
            "threshold": self.threshold,
            "n_sequences": n_samples,
            "n_parameters": param_count,
            "model_path": MODEL_WEIGHTS_PATH,
        }

        logger.info(f"✅ LSTM Autoencoder training complete!")
        return result

    def _save(self, scaler, data_shape, epochs_trained) -> None:
        """Save model weights, scaler, and metadata to disk."""
        os.makedirs(self.model_dir, exist_ok=True)

        # 1. Model weights
        torch.save(self.model.state_dict(), MODEL_WEIGHTS_PATH)
        logger.info(f"💾 Model weights saved to {MODEL_WEIGHTS_PATH}")

        # 2. Feature scaler
        scaler.save(SCALER_PATH)

        # 3. Metadata (threshold, config, training stats)
        metadata = {
            "threshold": self.threshold,
            "threshold_percentile": self.threshold_percentile,
            "n_features": int(data_shape[2]),
            "seq_len": int(data_shape[1]),
            "hidden_size": HIDDEN_SIZE,
            "n_layers": N_LAYERS,
            "bottleneck_size": BOTTLENECK_SIZE,
            "n_sequences_trained": int(data_shape[0]),
            "epochs_trained": epochs_trained,
            "final_train_loss": self.train_losses[-1] if self.train_losses else 0,
            "final_val_loss": self.val_losses[-1] if self.val_losses else 0,
            "trained_at": __import__("datetime").datetime.utcnow().isoformat(),
        }
        with open(METADATA_PATH, "w") as f:
            json.dump(metadata, f, indent=2)
        logger.info(f"💾 Training metadata saved to {METADATA_PATH}")
