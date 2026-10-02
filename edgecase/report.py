"""Coverage matrix, gap report and next-collection plan over the confirmed clips."""
from __future__ import annotations

from collections import Counter

from .llm import LLM

LIGHTING = ("day", "dusk", "night", "indoor")
INDOOR_HINTS = ("warehouse", "smartspace", "indoor")


def _label(c: dict, key: str) -> str:
    return str((c.get("labels") or {}).get(key) or "unknown").lower()


def coverage(run: dict) -> dict:
    confirmed = [c for c in run["candidates"] if c["status"] == "confirmed"]
    matrix = {cam: {light: 0 for light in LIGHTING} for cam in run["cameras"]}
    for c in confirmed:
        row = matrix.setdefault(c["camera_id"], {light: 0 for light in LIGHTING})
        light = _label(c, "lighting")
        row[light] = row.get(light, 0) + 1
    return {
        "confirmed": len(confirmed),
        "by_camera_lighting": matrix,
        "by_action": dict(Counter(_label(c, "action") for c in confirmed)),
        "by_distance": dict(Counter(_label(c, "distance_class") for c in confirmed)),
        "by_weather": dict(Counter(_label(c, "weather") for c in confirmed)),
    }


def empty_cells(cov: dict) -> list[str]:
    cells = []
    for cam, row in cov["by_camera_lighting"].items():
        if not any(row.values()):
            cells.append(f"camera group {cam} (no confirmed clips at all)")
            continue
        indoor = any(h in cam.lower() for h in INDOOR_HINTS)
        for light in LIGHTING:
            # indoor cameras only have "indoor"; outdoor cameras never do
            if row.get(light, 0) == 0 and (light == "indoor") == indoor:
                cells.append(f"{cam} at {light}")
    return cells


def build(run: dict, llm: LLM) -> dict:
    cov = coverage(run)
    cells = empty_cells(cov)
    gaps = llm.gap_report(run["request"], cov, cells)
    return {"coverage": cov, "empty_cells": cells, **gaps}


def coverage_table_md(cov: dict) -> str:
    lights = sorted({k for row in cov["by_camera_lighting"].values() for k in row}, key=lambda k: (k not in LIGHTING, k))
    lines = ["| camera group | " + " | ".join(lights) + " |", "|---|" + "---|" * len(lights)]
    for cam, row in cov["by_camera_lighting"].items():
        lines.append(f"| {cam} | " + " | ".join(str(row.get(k, 0)) for k in lights) + " |")
    return "\n".join(lines)
