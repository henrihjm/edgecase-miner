"""Clip quality: YOLO sidecar check, blur score, event completeness. Produces 0..1 and keep/reject."""
from __future__ import annotations

from pathlib import Path

try:  # OpenCV is optional so the rest of the tool still runs if the wheel is unavailable
    import cv2
except ImportError:  # pragma: no cover
    cv2 = None

KEEP_THRESHOLD = 0.5
BLUR_SHARP = 150.0  # Laplacian variance at or above this counts as fully sharp
BLUR_REJECT = 20.0

# Request / actor words mapped to the COCO classes YOLO11 can emit.
SYNONYMS = {
    "pedestrian": "person", "worker": "person", "people": "person", "man": "person", "woman": "person",
    "child": "person", "cyclist": "bicycle", "bike": "bicycle", "vehicle": "car", "forklift": "truck",
    "van": "truck", "lorry": "truck", "motorbike": "motorcycle",
}
CLASS_KEYS = ("class", "class_name", "label", "name")


def detection_counts(sidecar) -> dict[str, int]:
    """Class -> count from a YOLO sidecar of unknown exact shape."""
    counts: dict[str, int] = {}

    def walk(node):
        if isinstance(node, dict):
            oc = node.get("object_counts")
            if isinstance(oc, dict) and not counts:
                for k, v in oc.items():
                    if isinstance(v, (int, float)):
                        counts[str(k)] = int(v)
                return
            for key in CLASS_KEYS:
                if isinstance(node.get(key), str) and any(k in node for k in ("bbox", "box", "xyxy", "confidence", "conf")):
                    counts[node[key]] = counts.get(node[key], 0) + 1
                    break
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(sidecar)
    if not counts and isinstance(sidecar, dict) and isinstance(sidecar.get("object_classes"), list):
        counts.update({str(c): 1 for c in sidecar["object_classes"]})
    return counts


def relevant_classes(request: str, actors: list) -> set[str]:
    words = set(request.lower().replace(",", " ").split()) | {str(a).lower() for a in actors or []}
    words |= {w.rstrip("s") for w in words}
    return {SYNONYMS.get(w, w) for w in words}


def blur_score(clip: Path) -> float | None:
    """Mean Laplacian variance over 3 sampled frames. None if the clip cannot be read."""
    if cv2 is None:
        return None
    cap = cv2.VideoCapture(str(clip))
    try:
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        scores = []
        for frac in (0.2, 0.5, 0.8):
            if total > 0:
                cap.set(cv2.CAP_PROP_POS_FRAMES, int(total * frac))
            ok, frame = cap.read()
            if ok:
                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                scores.append(float(cv2.Laplacian(gray, cv2.CV_64F).var()))
        return round(sum(scores) / len(scores), 1) if scores else None
    finally:
        cap.release()


def score(request: str, labels: dict, sidecar, clip: Path | None) -> dict:
    counts = detection_counts(sidecar) if sidecar is not None else {}
    relevant = {c: n for c, n in counts.items() if c.lower() in relevant_classes(request, labels.get("actors"))}
    if sidecar is None:
        det, det_note = 0.2, "no YOLO sidecar"
    elif relevant:
        det, det_note = 1.0, "relevant detections: " + ", ".join(f"{c} x{n}" for c, n in relevant.items())
    elif counts:
        det, det_note = 0.6, "detections present, none of the requested classes"
    else:
        det, det_note = 0.3, "sidecar has no detections"

    blur = blur_score(clip) if clip else None
    sharp = 0.5 if blur is None else min(blur / BLUR_SHARP, 1.0)

    visible = labels.get("event_fully_visible")
    complete = 1.0 if visible is True else 0.0 if visible is False else 0.5

    total = round(0.4 * det + 0.3 * sharp + 0.3 * complete, 2)
    if visible is False:
        keep, reason = False, "event not fully visible"
    elif blur is not None and blur < BLUR_REJECT:
        keep, reason = False, f"too blurry (Laplacian variance {blur})"
    elif total < KEEP_THRESHOLD:
        keep, reason = False, f"quality {total} below {KEEP_THRESHOLD} ({det_note})"
    else:
        keep, reason = True, det_note
    return {"score": total, "keep": keep, "reason": reason, "blur": blur,
            "detections": counts, "relevant_detections": relevant}
