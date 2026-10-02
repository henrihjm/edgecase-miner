"""Clip quality scoring: YOLO sidecar, blur, event completeness."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import cv2
import numpy as np


RELEVANT_CLASSES = {
    "person",
    "forklift",  # may not be in YOLO COCO; truck/car often proxy
    "truck",
    "car",
    "bus",
    "motorcycle",
    "bicycle",
}


def blur_score(clip_path: Path, samples: int = 3) -> float:
    """Laplacian variance averaged over evenly spaced frames. Higher = sharper."""
    cap = cv2.VideoCapture(str(clip_path))
    if not cap.isOpened():
        return 0.0
    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    if frame_count <= 0:
        cap.release()
        return 0.0
    indices = np.linspace(0, max(frame_count - 1, 0), num=samples, dtype=int)
    vars_: list[float] = []
    for idx in indices:
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(idx))
        ok, frame = cap.read()
        if not ok or frame is None:
            continue
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        vars_.append(float(cv2.Laplacian(gray, cv2.CV_64F).var()))
    cap.release()
    if not vars_:
        return 0.0
    return float(sum(vars_) / len(vars_))


def score_quality(
    *,
    detections: dict[str, Any] | None,
    labels: dict[str, Any],
    clip_path: Path | None,
    blur_threshold: float = 50.0,
) -> dict[str, Any]:
    """Return quality 0..1, keep bool, and reason."""
    reasons: list[str] = []
    parts: list[float] = []

    # Detections sidecar
    if detections is None:
        parts.append(0.35)
        reasons.append("no YOLO sidecar (404)")
        det_summary: dict[str, Any] = {"present": False}
    else:
        classes = set()
        if isinstance(detections.get("object_classes"), list):
            classes = {str(c).lower() for c in detections["object_classes"]}
        elif isinstance(detections.get("object_counts"), dict):
            classes = {str(c).lower() for c in detections["object_counts"]}
        relevant = classes & RELEVANT_CLASSES
        det_score = 1.0 if relevant else (0.6 if classes else 0.4)
        parts.append(det_score)
        det_summary = {"present": True, "classes": sorted(classes), "relevant": sorted(relevant)}
        if not relevant:
            reasons.append("no relevant YOLO classes")

    # Blur
    blur = 0.0
    if clip_path and clip_path.exists():
        blur = blur_score(clip_path)
        # Map: <threshold → low, soft saturate around 200
        blur_norm = max(0.0, min(1.0, blur / 200.0))
        parts.append(blur_norm)
        if blur < blur_threshold:
            reasons.append(f"blurry (laplacian={blur:.1f})")
    else:
        parts.append(0.5)
        reasons.append("clip missing for blur check")

    # Event fully visible from Cosmos labels
    fully = labels.get("event_fully_visible")
    if fully is True:
        parts.append(1.0)
    elif fully is False:
        parts.append(0.2)
        reasons.append("event not fully visible")
    else:
        parts.append(0.5)

    quality = float(sum(parts) / len(parts)) if parts else 0.0
    keep = quality >= 0.45 and fully is not False and blur >= blur_threshold * 0.5
    if keep and not reasons:
        reasons.append("ok")
    elif keep:
        reasons.append("kept with caveats")

    return {
        "quality": round(quality, 3),
        "keep": keep,
        "reason": "; ".join(reasons) if reasons else "ok",
        "blur": round(blur, 2),
        "detections": det_summary,
    }
