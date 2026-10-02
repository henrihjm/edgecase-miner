"""Dataset export: manifest.json, labels.csv, clips/, README dataset card, zip."""
from __future__ import annotations

import csv
import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

from .config import EXPORT_DIR
from .report import coverage_table_md
from .vss_client import clip_path

LABEL_FIELDS = ("actors", "action", "distance_class", "lighting", "weather", "occlusion", "event_fully_visible")


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:50] or "dataset"


def export(run: dict) -> Path:
    """Writes exports/<slug>-<run id>/ and a zip next to it. Returns the zip path."""
    out = EXPORT_DIR / f"{slugify(run['request'])}-{run['id']}"
    if out.exists():
        shutil.rmtree(out)
    (out / "clips").mkdir(parents=True)

    confirmed = [c for c in run["candidates"] if c["status"] == "confirmed"]
    clips = []
    for i, c in enumerate(confirmed, 1):
        name = f"clip_{i:03d}.mp4"
        src = clip_path(c["source"])
        if src.exists():
            shutil.copyfile(src, out / "clips" / name)
        q = c.get("quality") or {}
        clips.append({
            "clip": f"clips/{name}",
            "source": c["source"],
            "original_video": c.get("original_video"),
            "camera_id": c["camera_id"],
            "start_sec": c.get("start_sec"),
            "end_sec": c.get("end_sec"),
            "similarity": c["similarity"],
            "verdict": "YES",
            "reasoning": c.get("reasoning", ""),
            "labels": c.get("labels", {}),
            "quality": {"score": q.get("score"), "blur": q.get("blur"), "reason": q.get("reason")},
            "detections": q.get("detections", {}),
        })

    rep = run.get("report", {})
    manifest = {
        "request": run["request"],
        "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "search_query": run.get("plan", {}).get("query"),
        "verification_question": f"Does this clip show {run.get('plan', {}).get('event')}?",
        "camera_groups_searched": run.get("cameras", []),
        "counts": run.get("counts", {}),
        "coverage": rep.get("coverage", {}),
        "gap_report": rep.get("gap_report", ""),
        "collection_plan": rep.get("collection_plan", []),
        "reingest_loop": run.get("loop", {}).get("iterations", []),
        "clips": clips,
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2))

    with open(out / "labels.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["clip", "camera_id", "similarity", "quality", *LABEL_FIELDS])
        for c in clips:
            labels = c["labels"]
            w.writerow([c["clip"], c["camera_id"], c["similarity"], c["quality"]["score"],
                        *("; ".join(map(str, labels.get(f))) if isinstance(labels.get(f), list) else labels.get(f, "")
                          for f in LABEL_FIELDS)])

    counts = run.get("counts", {})
    card = [
        f"# Dataset: {run['request']}", "",
        f"{len(clips)} verified clips, mined by Edge-Case Miner on {manifest['created']}.", "",
        "## Counts", "",
        f"- Candidates from search: {counts.get('searched', 0)}",
        f"- Checked by Cosmos Reason: {counts.get('verified', 0)}",
        f"- Confirmed: {counts.get('confirmed', 0)}",
        f"- Rejected: {counts.get('rejected', 0)}",
        f"- Unverified (model gave no verdict): {counts.get('unverified', 0)}", "",
        "## Coverage (confirmed clips by camera group and lighting)", "",
        coverage_table_md(rep["coverage"]) if rep.get("coverage") else "No coverage data.", "",
        "## Gaps", "", rep.get("gap_report", ""), "",
        "## Next collection plan", "", *[f"- {line}" for line in rep.get("collection_plan", [])], "",
        "## How it was produced", "",
        "1. Hybrid search (caption text + Cosmos Embed1 vectors) over the indexed archive, one search per camera group.",
        "2. Each candidate clip was shown to Cosmos3-Reason with a yes/no question; only YES clips are included.",
        "3. Labels were extracted by Cosmos3-Reason from the clip itself.",
        "4. Quality score from YOLO11 detections, blur (Laplacian variance on 3 frames) and event completeness.",
        "", "Files: `manifest.json` (everything per clip), `labels.csv`, `clips/`.", "",
    ]
    (out / "README.md").write_text("\n".join(card))
    return Path(shutil.make_archive(str(out), "zip", root_dir=out))
