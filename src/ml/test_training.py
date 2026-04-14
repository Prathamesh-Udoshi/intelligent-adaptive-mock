"""
End-to-end test: synthetic data → train → inference → verify anomaly detection.
Run from src/ directory.
"""

import torch
import numpy as np
import os

from ml.lstm_model import LSTMAutoencoder, SEQ_LEN, N_FEATURES
from ml.data_pipeline import FeatureScaler, create_sequences
from ml.trainer import LSTMTrainer

print("=== FULL TRAINING TEST (synthetic data) ===")

# Generate synthetic "normal" API traffic (200 observations)
np.random.seed(42)
n_obs = 200
latencies = np.abs(np.random.normal(250, 50, n_obs))   # ~250ms avg
errors = (np.random.random(n_obs) > 0.95).astype(float) # 5% error rate
sizes = np.abs(np.random.normal(3000, 500, n_obs))      # ~3KB responses
hour_sin = np.sin(2 * np.pi * np.linspace(0, 48, n_obs) / 24)
hour_cos = np.cos(2 * np.pi * np.linspace(0, 48, n_obs) / 24)

raw_data = np.column_stack([latencies, errors, sizes, hour_sin, hour_cos]).astype(np.float32)
print(f"Raw data shape: {raw_data.shape}")

# Normalize
scaler = FeatureScaler()
normalized = scaler.fit_transform(raw_data)

# Create sequences
sequences = create_sequences(normalized, seq_len=SEQ_LEN)
print(f"Sequences shape: {sequences.shape}")

# Train (20 epochs for quick test)
trainer = LSTMTrainer()
result = trainer.train(sequences, scaler, epochs=20)
print(f"\nTraining result:")
for k, v in result.items():
    print(f"  {k}: {v}")

# Verify saved files
model_dir = os.path.join(os.path.dirname(os.path.abspath(".")), "data", "ml_models", "lstm_anomaly")
print(f"\nSaved files in {model_dir}:")
for f in ["model.pt", "scaler.json", "metadata.json"]:
    path = os.path.join(model_dir, f)
    exists = os.path.exists(path)
    size = os.path.getsize(path) if exists else 0
    status = "EXISTS" if exists else "MISSING"
    print(f"  {f}: {status} ({size:,} bytes)")

# Test inference with trained model
from ml.inference.anomaly_predictor import AnomalyPredictor
predictor = AnomalyPredictor()
print(f"\nPredictor active: {predictor.is_active}")

# Feed 20 NORMAL observations
for i in range(20):
    predictor.feed("/test-normal", latencies[i], bool(errors[i]), int(sizes[i]))

normal_pred = predictor.predict("/test-normal")
print(f"\n--- Normal traffic ---")
print(f"  Anomaly: {normal_pred['is_anomaly']}")
print(f"  Score:   {normal_pred['anomaly_score']:.6f}")
print(f"  Status:  {normal_pred['model_status']}")

# Feed 20 ANOMALOUS observations (10x latency + all errors + tiny responses)
for i in range(20):
    predictor.feed("/test-anomaly", 2500 + np.random.normal(0, 100), True, 100)

anomaly_pred = predictor.predict("/test-anomaly")
print(f"\n--- Anomalous traffic ---")
print(f"  Anomaly: {anomaly_pred['is_anomaly']}")
print(f"  Score:   {anomaly_pred['anomaly_score']:.6f}")
print(f"  Status:  {anomaly_pred['model_status']}")

print(f"\n--- Threshold comparison ---")
print(f"  Threshold:     {normal_pred['threshold']:.6f}")
print(f"  Normal error:  {normal_pred['anomaly_score']:.6f} {'< threshold (CORRECT)' if not normal_pred['is_anomaly'] else '> threshold (unexpected)'}")
print(f"  Anomaly error: {anomaly_pred['anomaly_score']:.6f} {'> threshold (CORRECT)' if anomaly_pred['is_anomaly'] else '< threshold (needs more training)'}")

print("\n=== TEST COMPLETE ===")
