"""Shared recognition engine interface for prose and math backends."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from typing import Protocol


@dataclass(frozen=True)
class EngineCandidate:
    text: str
    score: float | None = None
    truncated: bool = False
    kind: str = "text"

    @property
    def raw_score(self) -> float | None:
        """Name used by the model-boundary contract."""
        return self.score


@dataclass(frozen=True)
class EngineSettings:
    """Explicit, serializable inference settings passed to an engine."""

    max_tokens: int = 160
    beam_size: int = 1
    temperature: float = 1.0
    device: str = "cpu"
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class EngineResult:
    engine_id: str
    engine_version: str
    candidates: list[EngineCandidate] = field(default_factory=list)
    available: bool = True
    reason: str | None = None
    latency_ms: float | None = None
    provenance: dict[str, Any] = field(default_factory=dict)
    settings: dict[str, Any] = field(default_factory=dict)

    @property
    def text(self) -> str:
        return self.candidates[0].text if self.candidates else ""

    @property
    def truncated(self) -> bool:
        return bool(self.candidates and self.candidates[0].truncated)


class FormulaEngine(Protocol):
    engine_id: str
    engine_version: str

    def is_available(self) -> bool:
        ...

    def recognize_crop(
        self, image_bytes: bytes, settings: EngineSettings | None = None
    ) -> EngineResult:
        ...
