"""LaTeX tokenizer with explicit unsupported-token handling."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Sequence

PAD = "<pad>"
BOS = "<bos>"
EOS = "<eos>"
UNK = "<unk>"

SPECIAL_TOKENS = (PAD, BOS, EOS, UNK)

LATEX_COMMANDS = (
    r"\frac",
    r"\sqrt",
    r"\sum",
    r"\int",
    r"\prod",
    r"\lim",
    r"\sin",
    r"\cos",
    r"\tan",
    r"\log",
    r"\ln",
    r"\exp",
    r"\alpha",
    r"\beta",
    r"\gamma",
    r"\delta",
    r"\epsilon",
    r"\theta",
    r"\lambda",
    r"\mu",
    r"\pi",
    r"\sigma",
    r"\phi",
    r"\omega",
    r"\infty",
    r"\partial",
    r"\cdot",
    r"\times",
    r"\div",
    r"\leq",
    r"\geq",
    r"\neq",
    r"\approx",
    r"\in",
    r"\subset",
    r"\rightarrow",
    r"\left",
    r"\right",
    r"\begin",
    r"\end",
    r"\mathrm",
    r"\mathbf",
    r"\overline",
    r"\hat",
    r"\bar",
    r"\vec",
    r"\to",
    r"\ldots",
    r"\cdots",
    r"\vdots",
    r"\ddots",
    r"\quad",
    r"\qquad",
    r"\text",
    r"\operatorname",
    r"\mathcal",
    r"\mathbb",
    r"\mathsf",
    r"\mathit",
    r"\mathnormal",
    r"\color",
    r"\displaystyle",
    r"\textstyle",
    r"\limits",
    r"\pm",
    r"\mp",
    r"\cdotp",
    r"\equiv",
    r"\sim",
    r"\propto",
    r"\notin",
    r"\subseteq",
    r"\supseteq",
    r"\mapsto",
    r"\uparrow",
    r"\downarrow",
    r"\nabla",
    r"\Delta",
    r"\Gamma",
    r"\Lambda",
    r"\Omega",
    r"\Pi",
    r"\Sigma",
    r"\Theta",
    r"\Phi",
    r"\Psi",
    r"\begin{matrix}",
    r"\end{matrix}",
    r"\begin{pmatrix}",
    r"\end{pmatrix}",
    r"\begin{bmatrix}",
    r"\end{bmatrix}",
    r"\begin{aligned}",
    r"\end{aligned}",
)

_COMMAND_RE = re.compile(r"\\[a-zA-Z]+|\\.|.")


class UnsupportedLatexError(ValueError):
    """Raised when a label contains tokens outside the accepted vocabulary."""


@dataclass(frozen=True)
class LatexTokenizer:
    """Character/command tokenizer that round-trips accepted LaTeX."""

    token_to_id: dict[str, int]
    id_to_token: dict[int, str]

    @classmethod
    def default(cls) -> "LatexTokenizer":
        tokens = list(SPECIAL_TOKENS)
        seen = set(tokens)
        for command in LATEX_COMMANDS:
            if command not in seen:
                tokens.append(command)
                seen.add(command)
        for char in (
            "abcdefghijklmnopqrstuvwxyz"
            "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
            "0123456789"
            r" +-*/=<>()[]{}.,:;^_|&!%#'\"\\"
        ):
            if char not in seen:
                tokens.append(char)
                seen.add(char)
        # ``\\`` is the row-separator token used in matrix/aligned formulas;
        # it is distinct from a single literal backslash.
        if r"\\" not in seen:
            tokens.append(r"\\")
            seen.add(r"\\")
        token_to_id = {tok: i for i, tok in enumerate(tokens)}
        id_to_token = {i: tok for tok, i in token_to_id.items()}
        return cls(token_to_id=token_to_id, id_to_token=id_to_token)

    @property
    def pad_id(self) -> int:
        return self.token_to_id[PAD]

    @property
    def bos_id(self) -> int:
        return self.token_to_id[BOS]

    @property
    def eos_id(self) -> int:
        return self.token_to_id[EOS]

    @property
    def unk_id(self) -> int:
        return self.token_to_id[UNK]

    @property
    def vocab_size(self) -> int:
        return len(self.token_to_id)

    def split(self, latex: str) -> list[str]:
        # Spaces are part of the accepted vocabulary and must survive a
        # round-trip (they may separate commands or be meaningful in text
        # commands).  Newlines/tabs remain unsupported because they are not
        # present in the default vocabulary.
        return _COMMAND_RE.findall(latex)

    def encode(self, latex: str, *, add_bos_eos: bool = True) -> list[int]:
        pieces = self.split(latex)
        unknown = sorted({tok for tok in pieces if tok not in self.token_to_id})
        if unknown:
            raise UnsupportedLatexError(
                "Unsupported LaTeX tokens: " + ", ".join(repr(tok) for tok in unknown[:12])
            )
        ids = [self.token_to_id[tok] for tok in pieces]
        if add_bos_eos:
            return [self.bos_id, *ids, self.eos_id]
        return ids

    def decode(self, ids: Sequence[int], *, skip_special: bool = True) -> str:
        tokens: list[str] = []
        for idx in ids:
            token = self.id_to_token.get(int(idx), UNK)
            if skip_special and token in SPECIAL_TOKENS:
                if token == EOS:
                    break
                continue
            tokens.append(token)
        return "".join(tokens)

    def round_trip(self, latex: str) -> str:
        return self.decode(self.encode(latex))
