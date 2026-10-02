"""Dataset export — Phase 2: manifest.json, labels.csv, clips/, README dataset card, zip."""

from __future__ import annotations

import csv
import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import Settings
from .miner import MineResult
from .report import counts, coverage_table_md

LABEL_FIELDS = ("actors", "action", "distance_class", "lighting", "weather", "occlusion", "event_fully_visible")


def _cell(value: Any) -> str:
    """CSV cell as text. Model output must never be read as a spreadsheet formula."""
    text = "; ".join(map(str, value)) if isinstance(value, list) else "" if value is None else str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:50] or "dataset"


def export_dataset(
    result: MineResult,
    report: dict[str, Any],
    settings: Settings,
    loop_iterations: list[dict[str, Any]] | None = None,
) -> Path:
    """Writes exports/<slug>/ and a zip next to it. Returns the zip path."""
    out = settings.exports_dir / slugify(result.request)
    if out.exists():
        shutil.rmtree(out)
    (out / "clips").mkdir(parents=True)

    clips: list[dict[str, Any]] = []
    for i, row in enumerate(result.confirmed, 1):
        name = f"clip_{i:03d}.mp4"
        # Only copy files that really are in our clip cache.
        clip_dir = settings.cache_dir / "clips"
        stem = Path(row.get("clip_path") or "").stem
        if re.fullmatch(r"[0-9a-f]{24}", stem) and (clip_dir / f"{stem}.mp4").is_file():
            shutil.copyfile(clip_dir / f"{stem}.mp4", out / "clips" / name)
        clips.append({
            "clip": f"clips/{name}",
            "source": row["source"],
            "original_video": row.get("original_video"),
            "camera_id": row.get("camera_id"),
            "start_sec": row.get("start_time"),
            "end_sec": row.get("end_time"),
            "similarity": row.get("similarity"),
            "verdict": row.get("verdict"),
            "reasoning": row.get("think_full") or row.get("think") or "",
            "labels": row.get("labels") or {},
            "quality": {"score": row.get("quality"), "blur": row.get("blur"), "reason": row.get("reason")},
            "detections": row.get("detections") or {},
        })

    n = counts(result)
    created = datetime.now(timezone.utc).isoformat(timespec="seconds")
    manifest = {
        "request": result.request,
        "created": created,
        "search_query": result.query,
        "verification_question": f"Does this clip show {result.request}?",
        "camera_groups_searched": result.camera_ids or result.all_cameras,
        "counts": n,
        "coverage": report.get("coverage", {}),
        "gap_report": report.get("gap_report", ""),
        "collection_plan": report.get("collection_plan", []),
        "reingest_loop": loop_iterations or [],
        "clips": clips,
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2))

    with open(out / "labels.csv", "w", newline="") as fh:
        w = csv.writer(fh, quoting=csv.QUOTE_ALL)
        w.writerow(["clip", "camera_id", "similarity", "quality", *LABEL_FIELDS])
        for c in clips:
            labels = c["labels"]
            w.writerow([_cell(v) for v in (
                c["clip"], c["camera_id"], c["similarity"], c["quality"]["score"],
                *(labels.get(f) for f in LABEL_FIELDS),
            )])

    card = [
        f"# Dataset: {result.request}", "",
        f"{len(clips)} verified clips, mined by Edge-Case Miner on {created}.", "",
        "## Counts", "",
        f"- Candidates from search: {n['searched']}",
        f"- Checked by Cosmos Reason: {n['verified']}",
        f"- Confirmed: {n['confirmed']}",
        f"- Rejected: {n['rejected']}",
        f"- Unverified (model gave no verdict): {n['unverified']}", "",
        "## Coverage (confirmed clips by camera group and lighting)", "",
        coverage_table_md(report["coverage"]) if report.get("coverage") else "No coverage data.", "",
        "## Gaps", "", report.get("gap_report", ""), "",
        "## Next collection plan", "", *[f"- {line}" for line in report.get("collection_plan", [])], "",
        "## How it was produced", "",
        "1. Hybrid search (caption text + Cosmos Embed1 vectors) over the indexed archive.",
        "2. Each candidate clip was shown to Cosmos3-Reason with a yes/no question; only YES clips are included.",
        "3. Labels were extracted by Cosmos3-Reason from the clip itself.",
        "4. Quality score from YOLO11 detections, blur (Laplacian variance on 3 frames) and event completeness.",
        "", "Files: `manifest.json` (everything per clip), `labels.csv`, `clips/`.", "",
    ]
    (out / "README.md").write_text("\n".join(card))
    return Path(shutil.make_archive(str(out), "zip", root_dir=out))
