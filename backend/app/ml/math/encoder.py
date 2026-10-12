"""Compact CNN encoder that retains a two-dimensional feature grid."""

from __future__ import annotations

import math

import torch
from torch import Tensor, nn


class ConvEncoder(nn.Module):
    """Downsample spatially while keeping a 2D grid for nested formulas."""

    def __init__(self, in_channels: int = 1, d_model: int = 256) -> None:
        super().__init__()
        self.stem = nn.Sequential(
            nn.Conv2d(in_channels, 32, kernel_size=3, padding=1),
            nn.BatchNorm2d(32),
            nn.ReLU(inplace=True),
            nn.Conv2d(32, 64, kernel_size=3, stride=2, padding=1),
            nn.BatchNorm2d(64),
            nn.ReLU(inplace=True),
            nn.Conv2d(64, 128, kernel_size=3, stride=2, padding=1),
            nn.BatchNorm2d(128),
            nn.ReLU(inplace=True),
            nn.Conv2d(128, d_model, kernel_size=3, padding=1),
            nn.BatchNorm2d(d_model),
            nn.ReLU(inplace=True),
        )
        self.d_model = d_model

    def forward(self, images: Tensor, valid_mask: Tensor | None = None) -> tuple[Tensor, Tensor, int, int]:
        """Return flattened features ``(B, HW, D)``, padding mask, and grid size.

        ``valid_mask`` is ``True`` for real pixels in the original image
        (shape ``B, H, W``). Padded feature cells are masked out of cross-attention.
        """
        features = self.stem(images)
        batch, dim, height, width = features.shape
        memory = features.flatten(2).transpose(1, 2)
        if valid_mask is None:
            key_padding_mask = torch.zeros(
                batch, height * width, dtype=torch.bool, device=images.device
            )
        else:
            pooled = torch.nn.functional.adaptive_max_pool2d(
                valid_mask.float().unsqueeze(1), output_size=(height, width)
            ).squeeze(1)
            # Transformer attention consumes a (batch, source_tokens) mask;
            # keep the same row-major flattening used by ``features``.
            key_padding_mask = (pooled <= 0).flatten(1)
        return memory, key_padding_mask, height, width


def add_2d_positions(memory: Tensor, height: int, width: int) -> Tensor:
    """Add sinusoidal row/column encodings to a flattened feature grid."""
    device = memory.device
    dim = memory.size(-1)
    y = torch.arange(height, device=device).float()
    x = torch.arange(width, device=device).float()
    yy, xx = torch.meshgrid(y, x, indexing="ij")
    half = dim // 4
    div = torch.exp(torch.arange(half, device=device).float() * (-math.log(10000.0) / max(half, 1)))
    pe = memory.new_zeros(height * width, dim)
    pe[:, 0:half] = torch.sin(yy.reshape(-1, 1) * div)
    pe[:, half : 2 * half] = torch.cos(yy.reshape(-1, 1) * div)
    pe[:, 2 * half : 3 * half] = torch.sin(xx.reshape(-1, 1) * div)
    pe[:, 3 * half : 4 * half] = torch.cos(xx.reshape(-1, 1) * div)
    return memory + pe.unsqueeze(0)
