"""Transformer decoder and optional CoMER-inspired coverage attention."""

from __future__ import annotations

import torch
from torch import Tensor, nn


class CoverageCrossAttention(nn.Module):
    """Cross-attention that accumulates a coverage vector over decoding steps."""

    def __init__(self, d_model: int, nhead: int) -> None:
        super().__init__()
        self.nhead = nhead
        self.scale = (d_model // nhead) ** -0.5
        self.q = nn.Linear(d_model, d_model)
        self.k = nn.Linear(d_model, d_model)
        self.v = nn.Linear(d_model, d_model)
        self.coverage_proj = nn.Linear(1, d_model)
        self.out = nn.Linear(d_model, d_model)

    def forward(
        self,
        query: Tensor,
        memory: Tensor,
        *,
        memory_key_padding_mask: Tensor | None = None,
        coverage: Tensor | None = None,
    ) -> tuple[Tensor, Tensor]:
        batch, tgt_len, dim = query.shape
        src_len = memory.size(1)
        heads = self.nhead
        head_dim = dim // heads
        q = self.q(query).view(batch, tgt_len, heads, head_dim).transpose(1, 2)
        k = self.k(memory).view(batch, src_len, heads, head_dim).transpose(1, 2)
        v = self.v(memory).view(batch, src_len, heads, head_dim).transpose(1, 2)
        scores = torch.matmul(q, k.transpose(-2, -1)) * self.scale
        if coverage is not None:
            cov = self.coverage_proj(coverage.unsqueeze(-1))
            cov_heads = cov.view(batch, src_len, heads, head_dim).permute(0, 2, 1, 3)
            scores = scores + torch.matmul(q, cov_heads.transpose(-2, -1))
        if memory_key_padding_mask is not None:
            scores = scores.masked_fill(
                memory_key_padding_mask.unsqueeze(1).unsqueeze(2),
                float("-inf"),
            )
        attn = torch.softmax(scores, dim=-1)
        attn = torch.nan_to_num(attn, nan=0.0)
        mixed = torch.matmul(attn, v).transpose(1, 2).contiguous().view(batch, tgt_len, dim)
        new_coverage = attn.mean(dim=1).sum(dim=1)
        if coverage is not None:
            new_coverage = coverage + new_coverage
        return self.out(mixed), new_coverage


class FormulaDecoder(nn.Module):
    """Causal Transformer decoder over image memory."""

    def __init__(
        self,
        vocab_size: int,
        d_model: int = 256,
        nhead: int = 4,
        num_layers: int = 4,
        dim_feedforward: int = 512,
        dropout: float = 0.1,
        coverage: bool = False,
    ) -> None:
        super().__init__()
        self.d_model = d_model
        self.coverage_enabled = coverage
        self.embed = nn.Embedding(vocab_size, d_model)
        layer = nn.TransformerDecoderLayer(
            d_model=d_model,
            nhead=nhead,
            dim_feedforward=dim_feedforward,
            dropout=dropout,
            batch_first=True,
        )
        self.decoder = nn.TransformerDecoder(layer, num_layers=num_layers)
        self.coverage_attn = CoverageCrossAttention(d_model, nhead) if coverage else None
        self.norm = nn.LayerNorm(d_model)
        self.proj = nn.Linear(d_model, vocab_size)

    def forward(
        self,
        tokens: Tensor,
        memory: Tensor,
        *,
        tgt_key_padding_mask: Tensor | None = None,
        memory_key_padding_mask: Tensor | None = None,
    ) -> Tensor:
        tgt = self.embed(tokens)
        tgt_len = tokens.size(1)
        causal = torch.triu(
            torch.ones(tgt_len, tgt_len, device=tokens.device, dtype=torch.bool),
            diagonal=1,
        )
        hidden = self.decoder(
            tgt,
            memory,
            tgt_mask=causal,
            tgt_key_padding_mask=tgt_key_padding_mask,
            memory_key_padding_mask=memory_key_padding_mask,
        )
        if self.coverage_attn is not None:
            # Run the auxiliary cross-attention left-to-right so coverage at
            # step t contains the attention mass accumulated at steps < t.
            # Calling it once over the whole sequence would make this an
            # attention-shaped residual, not a coverage mechanism.
            coverage = hidden.new_zeros(hidden.size(0), memory.size(1))
            covered_steps: list[Tensor] = []
            for step in range(hidden.size(1)):
                covered, coverage = self.coverage_attn(
                    hidden[:, step : step + 1],
                    memory,
                    memory_key_padding_mask=memory_key_padding_mask,
                    coverage=coverage,
                )
                covered_steps.append(covered)
            hidden = hidden + torch.cat(covered_steps, dim=1)
        return self.proj(self.norm(hidden))
