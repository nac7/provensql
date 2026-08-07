"""
Verdict types for provensql.

The core soundness rule of the whole project: provensql must never claim two
queries are EQUIVALENT unless it has actually proven that, for every
database instance, they return the same result. Testing (counterexample
search) can disprove equivalence but can never prove it -- so the DIFFERENT
verdict is reserved for a stage that found a concrete witness, and
EQUIVALENT is reserved for a stage that produced a proof.

As of M2, Stage 4 (counterexample search, see counterexample.py) backs
DIFFERENT with an actual witness database instance. The enforcement is
still structural, just relocated: Verdict.different() raises ValueError if
called without a non-empty witness, so a future shortcut ("just return
DIFFERENT because the canonical forms differ") fails loudly instead of
silently degrading the guarantee. Note the witness is only a proof relative
to the *inferred* schema (see schema_infer.py) -- without a real catalog,
provensql doesn't know about NOT NULL/UNIQUE/FK constraints the production
tables might actually have, so every DIFFERENT verdict states that
assumption explicitly.
"""

from dataclasses import dataclass, field
from enum import Enum


class VerdictType(str, Enum):
    EQUIVALENT = "EQUIVALENT"
    DIFFERENT = "DIFFERENT"  # not constructible via this module yet -- see docstring
    SCHEMA_CHANGE = "SCHEMA_CHANGE"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class Verdict:
    type: VerdictType
    reason: str = ""
    assumptions: tuple[str, ...] = field(default_factory=tuple)
    reason_code: str = ""  # machine-readable, populated for UNKNOWN
    witness: str = ""  # populated for DIFFERENT: a replayable repro instance

    @staticmethod
    def equivalent(reason: str, assumptions: tuple[str, ...] = ()) -> "Verdict":
        return Verdict(VerdictType.EQUIVALENT, reason=reason, assumptions=assumptions)

    @staticmethod
    def schema_change(reason: str) -> "Verdict":
        return Verdict(VerdictType.SCHEMA_CHANGE, reason=reason)

    @staticmethod
    def different(reason: str, witness: str, assumptions: tuple[str, ...] = ()) -> "Verdict":
        if not witness:
            raise ValueError(
                "DIFFERENT verdicts must carry a non-empty witness -- "
                "a claim of divergence without a concrete instance is not proven"
            )
        return Verdict(VerdictType.DIFFERENT, reason=reason, assumptions=assumptions, witness=witness)

    @staticmethod
    def unknown(reason_code: str, reason: str = "") -> "Verdict":
        return Verdict(VerdictType.UNKNOWN, reason=reason, reason_code=reason_code)

    def __str__(self) -> str:
        s = self.type.value
        if self.reason:
            s += f" ({self.reason})"
        if self.assumptions:
            s += " [assuming: " + "; ".join(self.assumptions) + "]"
        return s
