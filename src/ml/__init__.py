"""
ML Module — Intelligent Adaptive Mock Platform
================================================
Locally trained open-source models that learn from the platform's own traffic.

Models:
  - LSTM Autoencoder:  Multi-signal anomaly detection (latency + errors + size + time)
  
Architecture:
  lstm_model.py        → PyTorch model definition
  data_pipeline.py     → Feature extraction and normalization
  trainer.py           → Training loop, threshold computation, model persistence
  inference/           → Real-time inference with per-endpoint sliding windows
  auto_retrain.py      → Background retraining loop
"""
