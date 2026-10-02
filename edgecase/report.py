"""Coverage matrix + gap report — Phase 2."""

from __future__ import annotations

from collections import Counter
from typing import Any

from .llm import LLMClient
from .miner import MineResult

LIGHTING = ("day", "dusk", "night", "indoor")
INDOOR_HINTS = ("warehouse", "smartspace", "indoor")


def status(row: dict[str, Any]) -> str:
    """confirmed | rejected | unverified, for one processed candidate."""
    if row.get("keep"):
        return "confirmed"
    return "rejected" if row.get("verdict") in ("YES", "NO") else "unverified"


def counts(result: MineResult) -> dict[str, int]:
    statuses = [status(r) for r in result.candidates]
    return {
        "searched": max(result.searched, len(statuses)),
        "verified": len(statuses),
        "confirmed": statuses.count("confirmed"),
        "rejected": statuses.count("rejected"),
        "unverified": statuses.count("unverified"),
    }


def _label(row: dict[str, Any], key: str) -> str:
    value = (row.get("labels") or {}).get(key)
    return str(value).lower() if value not in (None, "") else "unknown"


def build_coverage(confirmed: list[dict[str, Any]], cameras: list[str]) -> dict[str, Any]:
    """Confirmed clips by camera group x lighting, plus action / distance / weather counts."""
    matrix: dict[str, dict[str, int]] = {cam: {light: 0 for light in LIGHTING} for cam in cameras}
    for row in confirmed:
        cells = matrix.setdefault(row.get("camera_id") or "unknown", {light: 0 for light in LIGHTING})
        light = _label(row, "lighting")
        cells[light] = cells.get(light, 0) + 1
    return {
        "confirmed": len(confirmed),
        "by_camera_lighting": matrix,
        "by_action": dict(Counter(_label(r, "action") for r in confirmed)),
        "by_distance": dict(Counter(_label(r, "distance_class") for r in confirmed)),
        "by_weather": dict(Counter(_label(r, "weather") for r in confirmed)),
    }


def empty_cells(coverage: dict[str, Any]) -> list[str]:
    cells: list[str] = []
    for cam, row in coverage["by_camera_lighting"].items():
        if not any(row.values()):
            cells.append(f"camera group {cam} (no confirmed clips at all)")
            continue
        indoor = any(h in cam.lower() for h in INDOOR_HINTS)
        for light in LIGHTING:
            # indoor cameras only have "indoor"; outdoor cameras never do
            if row.get(light, 0) == 0 and (light == "indoor") == indoor:
                cells.append(f"{cam} at {light}")
    return cells


def build_report(result: MineResult, llm: LLMClient) -> dict[str, Any]:
    cameras = result.camera_ids or result.all_cameras
    coverage = build_coverage(result.confirmed, cameras)
    cells = empty_cells(coverage)
    return {"coverage": coverage, "empty_cells": cells, **llm.gap_report(result.request, coverage, cells)}


def coverage_table_md(coverage: dict[str, Any]) -> str:
    rows = coverage["by_camera_lighting"]
    lights = sorted({k for row in rows.values() for k in row}, key=lambda k: (LIGHTING.index(k) if k in LIGHTING else len(LIGHTING), k))
    lines = ["| camera group | " + " | ".join(lights) + " |", "|---|" + "---|" * len(lights)]
    for cam, row in rows.items():
        lines.append(f"| {cam} | " + " | ".join(str(row.get(k, 0)) for k in lights) + " |")
    return "\n".join(lines)
