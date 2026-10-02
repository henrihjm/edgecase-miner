"""Still frames for the UI: one poster or a five-frame filmstrip per cached clip, cached as JPEG."""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

FRAMES = 5
MAX_WIDTH = 640


def _frame_at(cap: cv2.VideoCapture, fraction: float) -> np.ndarray | None:
    count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    if count <= 0:
        return None
    cap.set(cv2.CAP_PROP_POS_FRAMES, int(max(0, min(count - 1, round(fraction * (count - 1))))))
    ok, frame = cap.read()
    if not ok or frame is None:
        return None
    h, w = frame.shape[:2]
    if w > MAX_WIDTH:
        frame = cv2.resize(frame, (MAX_WIDTH, int(h * MAX_WIDTH / w)), interpolation=cv2.INTER_AREA)
    return frame


def still(clip: Path, out_dir: Path, index: int) -> Path | None:
    """Frame `index` of FRAMES, sampled at the centre of its fifth of the clip. Returns the JPEG path."""
    index = max(0, min(FRAMES - 1, index))
    out = out_dir / f"{clip.stem}-{index}.jpg"
    if out.is_file():
        return out
    cap = cv2.VideoCapture(str(clip))
    try:
        frame = _frame_at(cap, (index + 0.5) / FRAMES) if cap.isOpened() else None
    finally:
        cap.release()
    if frame is None:
        return None
    out_dir.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out), frame, [cv2.IMWRITE_JPEG_QUALITY, 82])
    return out
