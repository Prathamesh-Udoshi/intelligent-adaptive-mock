"""
LSTM Autoencoder — Model Definition
=====================================
A sequence-to-sequence autoencoder that learns "normal" API traffic patterns.

Architecture:
  Encoder: LSTM(n_features → hidden) → Bottleneck(hidden → latent)
  Decoder: Expand(latent → hidden) → LSTM(hidden → hidden) → Linear(hidden → n_features)

The encoder compresses a window of N recent observations into a compact
latent vector. The decoder reconstructs the original window from this
vector. High reconstruction error = the current traffic pattern doesn't
match what the model learned as "normal" → anomaly detected.

Feature vector per timestep (5 features):
  0: latency_ms          (normalized by global mean/std)
  1: is_error            (binary: 0.0 or 1.0)
  2: response_size_bytes (normalized by global mean/std)
  3: hour_sin            (sin(2π × hour / 24) — cyclical time feature)
  4: hour_cos            (cos(2π × hour / 24) — cyclical time feature)

Why these features:
  - latency_ms:     Core signal. Spikes indicate endpoint degradation.
  - is_error:       Error bursts are a strong anomaly indicator.
  - response_size:  Sudden drops = truncated data; spikes = data leak.
  - hour_sin/cos:   Cyclical encoding lets the model learn time-of-day patterns
                    (e.g., "this endpoint is always slow at 3PM" is NOT an anomaly).
"""

import torch
import torch.nn as nn


# ──────────────────────────────────────────────────────
# CONSTANTS
# ──────────────────────────────────────────────────────

N_FEATURES = 5              # Number of features per timestep
SEQ_LEN = 20                # Sliding window size (last N observations)
HIDDEN_SIZE = 32            # LSTM hidden state size (small = fast CPU inference)
N_LAYERS = 2                # LSTM depth (2 layers can learn richer patterns)
BOTTLENECK_SIZE = 16        # Latent space dimensionality (compression ratio)


# ──────────────────────────────────────────────────────
# MODEL
# ──────────────────────────────────────────────────────

class LSTMAutoencoder(nn.Module):
    """
    LSTM Autoencoder for multi-signal API traffic anomaly detection.

    Trained on "normal" traffic only. At inference time, the reconstruction
    error (MSE between input and output) serves as the anomaly score.
    Normal patterns → low error. Anomalous patterns → high error.

    Input shape:  (batch_size, seq_len, n_features)
    Output shape: (batch_size, seq_len, n_features)

    Example:
        model = LSTMAutoencoder()
        x = torch.randn(1, 20, 5)          # 1 sample, 20 timesteps, 5 features
        reconstructed = model(x)            # Same shape
        error = F.mse_loss(reconstructed, x)  # Anomaly score
    """

    def __init__(
        self,
        n_features: int = N_FEATURES,
        hidden_size: int = HIDDEN_SIZE,
        n_layers: int = N_LAYERS,
        bottleneck_size: int = BOTTLENECK_SIZE,
        seq_len: int = SEQ_LEN,
    ):
        super().__init__()
        self.n_features = n_features
        self.hidden_size = hidden_size
        self.n_layers = n_layers
        self.seq_len = seq_len

        # ── Encoder ─────────────────────────────────────────────────────
        # Reads the full input sequence and compresses it into a fixed-size
        # hidden state. We only keep the LAST hidden state (the "summary"
        # of the entire sequence).
        self.encoder = nn.LSTM(
            input_size=n_features,
            hidden_size=hidden_size,
            num_layers=n_layers,
            batch_first=True,
            dropout=0.1 if n_layers > 1 else 0.0,
        )

        # ── Bottleneck ──────────────────────────────────────────────────
        # Compresses the hidden state further. This forces the model to
        # learn the MOST important features of the traffic pattern.
        self.bottleneck = nn.Sequential(
            nn.Linear(hidden_size, bottleneck_size),
            nn.ReLU(),
        )

        # ── Expand ──────────────────────────────────────────────────────
        # Expands the bottleneck back to hidden_size for the decoder.
        self.expand = nn.Sequential(
            nn.Linear(bottleneck_size, hidden_size),
            nn.ReLU(),
        )

        # ── Decoder ─────────────────────────────────────────────────────
        # Takes the expanded latent vector (repeated seq_len times) and
        # reconstructs the original sequence.
        self.decoder = nn.LSTM(
            input_size=hidden_size,
            hidden_size=hidden_size,
            num_layers=n_layers,
            batch_first=True,
            dropout=0.1 if n_layers > 1 else 0.0,
        )

        # ── Output projection ──────────────────────────────────────────
        # Projects decoder output back to the original feature space.
        self.output_layer = nn.Linear(hidden_size, n_features)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """
        Forward pass: encode → bottleneck → decode → reconstruct.

        Args:
            x: Input tensor of shape (batch_size, seq_len, n_features).

        Returns:
            Reconstructed tensor of shape (batch_size, seq_len, n_features).
        """
        # Encode: process full sequence, keep last hidden state
        _, (hidden, cell) = self.encoder(x)
        # hidden shape: (n_layers, batch_size, hidden_size)
        # We use the LAST layer's hidden state as the sequence summary
        last_hidden = hidden[-1]  # (batch_size, hidden_size)

        # Bottleneck: compress
        latent = self.bottleneck(last_hidden)  # (batch_size, bottleneck_size)

        # Expand: decompress
        expanded = self.expand(latent)  # (batch_size, hidden_size)

        # Repeat across timesteps: the decoder must reconstruct the full
        # sequence from this single compressed vector
        decoder_input = expanded.unsqueeze(1).repeat(1, self.seq_len, 1)
        # decoder_input shape: (batch_size, seq_len, hidden_size)

        # Decode: reconstruct the sequence
        decoder_output, _ = self.decoder(decoder_input)
        # decoder_output shape: (batch_size, seq_len, hidden_size)

        # Project back to original feature space
        reconstructed = self.output_layer(decoder_output)
        # reconstructed shape: (batch_size, seq_len, n_features)

        return reconstructed

    def get_reconstruction_error(self, x: torch.Tensor) -> torch.Tensor:
        """
        Compute per-sample reconstruction error (MSE).

        Args:
            x: Input tensor (batch_size, seq_len, n_features).

        Returns:
            Per-sample MSE tensor of shape (batch_size,).
        """
        with torch.no_grad():
            reconstructed = self.forward(x)
            # MSE per sample (average over seq_len and n_features)
            error = torch.mean((x - reconstructed) ** 2, dim=(1, 2))
            return error

    def count_parameters(self) -> int:
        """Return total number of trainable parameters."""
        return sum(p.numel() for p in self.parameters() if p.requires_grad)
