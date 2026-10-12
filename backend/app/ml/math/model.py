"""Compact image-to-LaTeX model (Track B)."""

from __future__ import annotations

from dataclasses import dataclass

import torch
from torch import Tensor, nn

from app.ml.math.decoder import FormulaDecoder
from app.ml.math.encoder import ConvEncoder, add_2d_positions
from app.ml.math.tokenizer import LatexTokenizer


@dataclass(frozen=True)
class CompactMathConfig:
    d_model: int = 256
    nhead: int = 4
    num_layers: int = 4
    image_height: int = 128
    max_width: int = 512
    max_tokens: int = 160
    coverage: bool = False
    dropout: float = 0.1
    beam_size: int = 1


class CompactMathModel(nn.Module):
    """CNN visual encoder + Transformer decoder that emits LaTeX tokens."""

    def __init__(
        self,
        tokenizer: LatexTokenizer | None = None,
        config: CompactMathConfig | None = None,
    ) -> None:
        super().__init__()
        self.tokenizer = tokenizer or LatexTokenizer.default()
        self.config = config or CompactMathConfig()
        self.encoder = ConvEncoder(in_channels=1, d_model=self.config.d_model)
        self.decoder = FormulaDecoder(
            vocab_size=self.tokenizer.vocab_size,
            d_model=self.config.d_model,
            nhead=self.config.nhead,
            num_layers=self.config.num_layers,
            dropout=self.config.dropout,
            coverage=self.config.coverage,
        )

    def encode_images(self, images: Tensor, valid_mask: Tensor | None = None) -> tuple[Tensor, Tensor]:
        memory, key_padding_mask, height, width = self.encoder(images, valid_mask)
        memory = add_2d_positions(memory, height, width)
        return memory, key_padding_mask

    def forward(
        self,
        images: Tensor,
        tokens: Tensor,
        *,
        valid_mask: Tensor | None = None,
        token_padding_mask: Tensor | None = None,
    ) -> Tensor:
        memory, memory_pad = self.encode_images(images, valid_mask)
        return self.decoder(
            tokens,
            memory,
            tgt_key_padding_mask=token_padding_mask,
            memory_key_padding_mask=memory_pad,
        )

    @torch.no_grad()
    def greedy_decode(
        self,
        images: Tensor,
        *,
        valid_mask: Tensor | None = None,
        max_tokens: int | None = None,
    ) -> tuple[list[str], list[bool]]:
        """Greedy decode one batch. Truncation is reported, not success."""
        self.eval()
        max_len = max_tokens or self.config.max_tokens
        memory, memory_pad = self.encode_images(images, valid_mask)
        batch = images.size(0)
        tokens = torch.full(
            (batch, 1),
            self.tokenizer.bos_id,
            dtype=torch.long,
            device=images.device,
        )
        finished = torch.zeros(batch, dtype=torch.bool, device=images.device)
        truncated = torch.ones(batch, dtype=torch.bool, device=images.device)
        for _ in range(max_len):
            logits = self.decoder(tokens, memory, memory_key_padding_mask=memory_pad)
            next_token = logits[:, -1].argmax(dim=-1)
            tokens = torch.cat([tokens, next_token.unsqueeze(1)], dim=1)
            finished = finished | (next_token == self.tokenizer.eos_id)
            if bool(finished.all()):
                truncated = ~finished
                break
        else:
            truncated = ~finished
        texts = [self.tokenizer.decode(seq.tolist()) for seq in tokens]
        return texts, truncated.tolist()

    @torch.no_grad()
    def beam_decode(
        self,
        images: Tensor,
        *,
        valid_mask: Tensor | None = None,
        max_tokens: int | None = None,
        beam_size: int | None = None,
    ) -> tuple[list[str], list[bool], list[float]]:
        """Bounded per-example beam search with explicit truncation flags."""
        self.eval()
        max_len = max_tokens or self.config.max_tokens
        width = max(1, min(int(beam_size or self.config.beam_size), 8))
        memory, memory_pad = self.encode_images(images, valid_mask)
        outputs: list[str] = []
        truncated: list[bool] = []
        scores: list[float] = []
        eos = self.tokenizer.eos_id
        for index in range(images.size(0)):
            item_memory = memory[index : index + 1]
            item_pad = memory_pad[index : index + 1]
            beams: list[tuple[list[int], float, bool]] = [([self.tokenizer.bos_id], 0.0, False)]
            for _ in range(max_len):
                expanded: list[tuple[list[int], float, bool]] = []
                for sequence, score, done in beams:
                    if done:
                        expanded.append((sequence, score, done))
                        continue
                    token_tensor = torch.tensor([sequence], dtype=torch.long, device=images.device)
                    logits = self.decoder(token_tensor, item_memory, memory_key_padding_mask=item_pad)
                    log_probs = torch.log_softmax(logits[:, -1], dim=-1)
                    values, indices = torch.topk(log_probs, k=min(width, log_probs.size(-1)), dim=-1)
                    for value, token in zip(values[0].tolist(), indices[0].tolist()):
                        expanded.append((sequence + [int(token)], score + float(value), int(token) == eos))
                expanded.sort(key=lambda beam: beam[1] / max(1, len(beam[0]) - 1), reverse=True)
                beams = expanded[:width]
                if all(done for _, _, done in beams):
                    break
            best = max(beams, key=lambda beam: beam[1] / max(1, len(beam[0]) - 1))
            outputs.append(self.tokenizer.decode(best[0]))
            truncated.append(not best[2])
            scores.append(best[1])
        return outputs, truncated, scores
