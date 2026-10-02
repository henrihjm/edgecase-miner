"""Orchestration stub — full mine path lands in Phase 1."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class MineResult:
    request: str
    query: str
    candidates: list[dict[str, Any]] = field(default_factory=list)
    confirmed: list[dict[str, Any]] = field(default_factory=list)
    rejected: list[dict[str, Any]] = field(default_factory=list)
    timings: dict[str, float] = field(default_factory=dict)


# Implemented in Phase 1
def mine(*args: Any, **kwargs: Any) -> MineResult:
    raise NotImplementedError("Phase 1: implement edgecase.miner.mine")
