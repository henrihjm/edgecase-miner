"""Offline UI preview: serves the real index.html against fake runs with synthetic, browser-playable clips.

    python tests/preview_ui.py warehouse   # or: street
    open http://127.0.0.1:8765

Needs ffmpeg on PATH. Writes clips under cache/preview/. Not part of the product.
"""
from __future__ import annotations

import hashlib
import random
import subprocess
import sys
import threading
import time
from pathlib import Path

import cv2
import numpy as np
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
from edgecase import stills  # noqa: E402

CLIPS = REPO / "cache" / "preview" / "clips"; CLIPS.mkdir(parents=True, exist_ok=True)
STILLS = REPO / "cache" / "preview" / "stills"
STATIC = REPO / "edgecase" / "static" / "index.html"
app = FastAPI()

CAMS = [
    {"id": "sdg_warehouse_cam-2", "name": "Warehouse", "place": "Indoor"},
    {"id": "pie_cam-3", "name": "Toronto dashcam", "place": "Toronto"},
    {"id": "neighborhood_cam-1", "name": "Neighbourhood", "place": "Street camera"},
    {"id": "i24_cam-1", "name": "I-24 highway", "place": "Nashville"},
    {"id": "sf_streets_cam-1", "name": "SF streets 1", "place": "San Francisco"},
    {"id": "smartspace_cam-1", "name": "Smart space", "place": "Indoor"},
]


def scene(kind: str, night: bool, seed: int) -> Path:
    cid = hashlib.sha256(f"{kind}{night}{seed}".encode()).hexdigest()[:24]
    CLIPS.mkdir(parents=True, exist_ok=True)   # something may sweep cache/ while the server runs
    out = CLIPS / f"{cid}.mp4"
    if out.exists():
        return out
    rng = random.Random(seed)
    w, h, fps, secs = 640, 360, 12, 3
    raw = CLIPS / f"{cid}.raw.mp4"
    vw = cv2.VideoWriter(str(raw), cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))
    sky = (40, 30, 20) if night else ((210, 190, 160) if kind == "street" else (235, 232, 225))
    ground = (35, 35, 38) if night else ((70, 70, 72) if kind == "street" else (150, 150, 155))
    x0 = rng.randint(-200, 100)
    for f in range(fps * secs):
        img = np.zeros((h, w, 3), np.uint8)
        img[:] = sky
        img[h // 2:] = ground
        # perspective lines
        for i in range(-6, 7):
            cv2.line(img, (w // 2 + i * 15, h // 2), (w // 2 + i * 220, h), (ground[0] + 20,) * 3, 1)
        if kind == "street":
            cv2.rectangle(img, (0, h // 2 - 90), (w, h // 2), (90, 80, 70) if not night else (25, 25, 30), -1)
            for bx in range(0, w, 110):
                cv2.rectangle(img, (bx + 10, h // 2 - 90 - rng.randint(0, 40)), (bx + 90, h // 2), (120, 110, 100) if not night else (35, 35, 42), -1)
        else:
            for sx in range(40, w, 160):
                cv2.rectangle(img, (sx, h // 2 - 120), (sx + 110, h // 2), (200, 140, 70), -1)
                for sy in range(h // 2 - 110, h // 2, 36):
                    cv2.rectangle(img, (sx + 6, sy), (sx + 104, sy + 26), (140, 95, 50), -1)
        # vehicle
        vx = x0 + int(f * 7.5)
        vcol = (30, 200, 240) if kind == "warehouse" else rng.choice([(200, 200, 200), (40, 40, 160), (30, 30, 30)])
        cv2.rectangle(img, (vx, h // 2 + 40), (vx + 150, h // 2 + 130), vcol, -1)
        cv2.rectangle(img, (vx + 20, h // 2 + 10), (vx + 110, h // 2 + 40), (60, 60, 60), -1)
        cv2.circle(img, (vx + 30, h // 2 + 132), 16, (20, 20, 20), -1); cv2.circle(img, (vx + 120, h // 2 + 132), 16, (20, 20, 20), -1)
        if night:
            cv2.circle(img, (vx + 150, h // 2 + 90), 10, (200, 240, 255), -1)
        # person
        px = w - 120 - int(f * 2.5)
        cv2.rectangle(img, (px, h // 2 + 30), (px + 22, h // 2 + 100), (200, 120, 40) if kind == "warehouse" else (90, 60, 50), -1)
        cv2.circle(img, (px + 11, h // 2 + 18), 12, (190, 170, 160), -1)
        if night:
            img = (img * 0.55).astype(np.uint8)
        noise = np.random.default_rng(f).integers(0, 12, (h, w, 3), dtype=np.uint8)
        img = cv2.add(img, noise)
        vw.write(img)
    vw.release()
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(raw), "-c:v", "libx264", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out)], check=True)
    raw.unlink()
    return out


def make_cards(kind: str, cam: str, n_conf: int, n_rej: int, n_unv: int = 0) -> list[dict]:
    rows = []
    actions = ["forklift drives past worker", "worker walks beside forklift", "pedestrian crosses in front of car", "car turns across crosswalk"]
    i = 0
    for status in ["confirmed"] * n_conf + ["rejected"] * n_rej + ["unverified"] * n_unv:
        night = kind == "street" and i % 3 == 2
        clip = scene(kind, night, i * 7 + len(cam))
        indoor = kind == "warehouse"
        rows.append({
            "clip_id": clip.stem, "source": f"s3://seg/{cam}/{i}.mp4", "filename": f"{cam}_segment_{i:03d}.mp4", "camera_id": cam,
            "camera": next(c["name"] for c in CAMS if c["id"] == cam), "start_time": f"{i * 10}s", "end_time": f"{i * 10 + 8}s",
            "similarity": round(0.52 - i * 0.015, 3), "status": status, "verdict": "YES" if status == "confirmed" else ("NO" if status == "rejected" and i % 2 else "YES") if status != "unverified" else "unverified",
            "reason": "ok" if status == "confirmed" else ("cosmos NO; ok" if i % 2 else "blurry (laplacian=38.2)") if status == "rejected" else "unverified fallback",
            "reasoning": "The forklift (yellow) travels down the aisle from left to right while a worker in a high-visibility vest walks along the same aisle. At the mid-point they are within about one metre of each other, so the clip shows a forklift passing close to a person." if status == "confirmed" else "The forklift is visible but the person stays at the far end of the aisle, more than five metres away. This does not show a forklift passing close to a person.",
            "caption": "A yellow forklift moves along an indoor warehouse aisle lined with racks of boxes. A worker walks nearby. Lighting is bright and even.",
            "labels": {"actors": ["forklift", "worker"] if indoor else ["car", "pedestrian"], "action": actions[(i + (0 if indoor else 2)) % 4], "distance_class": ["under_2m", "2_to_5m", "over_5m"][i % 3],
                       "lighting": "indoor" if indoor else ("night" if night else "day"), "weather": None if indoor else "clear", "occlusion": "none" if i % 4 else "partial", "event_fully_visible": status != "rejected" or i % 2 == 1} if status != "unverified" else {},
            "quality": {"score": 0.92 - i * 0.03 if status == "confirmed" else 0.41, "blur": 180 - i * 9 if status != "rejected" or i % 2 else 38.2, "detections": {"present": True, "classes": ["person", "truck"], "relevant": ["person", "truck"]}},
            "timings": {"download_s": 0.4, "verify_s": 2.6 + (i % 5) * 0.2},
        })
        i += 1
    return rows


RUNS: dict[str, dict] = {}
SCENARIO = {"mode": "warehouse"}  # or "street"


def view(run: dict) -> dict:
    t = time.time() - run["t0"]
    cards = run["cards"]
    if run.get("iter") and run["iter"].get("status") != "completed":
        it = run["iter"]; stage = "re-ingesting"
        el = time.time() - it["t0"]
        it["jobs"] = [{"original_video": "s3://chunks/sdg_warehouse_cam-2/warehouse_set_03.mp4", "job_id": "j1", "progress": f"{min(1, int(el // 4))}/1 chunks, {min(12, int(el * 2))}/12 clips"}]
        if el > 8:
            it.update(status="completed", seconds=round(el, 1), reingest_seconds=6.2)
            run["cards"] = cards = cards + [dict(c, clip_id=scene("warehouse", False, 900 + k).stem, status="confirmed", reason="ok", verdict="YES") for k, c in enumerate(cards[:2])]
            it["after"] = {"confirmed": sum(c["status"] == "confirmed" for c in cards)}
            stage = "done"
        shown = cards if stage == "done" else run["cards"]
    else:
        stage = "searching" if t < 2 else "verifying" if t < 2 + len(cards) * 0.8 else "reporting" if t < 2 + len(cards) * 0.8 + 2 else "done"
        shown = [] if stage == "searching" else cards[: int((t - 2) / 0.8)] if stage == "verifying" else cards
    conf = sum(c["status"] == "confirmed" for c in shown)
    rej = sum(c["status"] == "rejected" for c in shown)
    unv = sum(c["status"] == "unverified" for c in shown)
    its = []
    if run.get("iter"):
        it = run["iter"]
        its = [{k: v for k, v in it.items() if k != "t0"}]
    report = None
    if stage == "done":
        wh = SCENARIO["mode"] == "warehouse"
        report = {
            "coverage": {"confirmed": conf,
                         "by_camera_lighting": {"sdg_warehouse_cam-2": {"day": 0, "dusk": 0, "night": 0, "indoor": conf}} if wh else {"pie_cam-3": {"day": conf - 2, "dusk": 0, "night": 2, "indoor": 0}, "neighborhood_cam-1": {"day": 0, "dusk": 0, "night": 0, "indoor": 0}},
                         "by_action": {"forklift drives past worker": 5, "worker walks beside forklift": 3, "forklift reverses near worker": 1} if wh else {"pedestrian crosses in front of car": 4, "car turns across crosswalk": 2},
                         "by_distance": {"under_2m": 4, "2_to_5m": 3, "over_5m": 2}},
            "empty_cells": [],
            "gap_report": "Every confirmed clip comes from the single synthetic warehouse camera under bright, even indoor lighting, so the set has no low-light, dusk or glare conditions. Distances cluster under five metres; there are no far approaches that later become close passes. Occlusion is rare, so the model will not see a forklift partly hidden behind racking." if wh else "All confirmed clips come from one dashcam in Toronto by day, with two at night. There is nothing from the fixed neighbourhood camera, no dusk, and no rain or wet-road footage, which matters for a pedestrian-crossing detector.",
            "collection_plan": ["Capture 20+ warehouse passes under dimmed or partial lighting", "Add clips where a forklift emerges from behind racking (occluded approach)", "Record far-to-near approaches that start beyond 5 m", "Include a second camera angle, ideally overhead, for the same aisle"] if wh else ["Film pedestrian crossings at dusk and in rain", "Add the fixed neighbourhood camera to the search once captions mention crossings", "Capture turning vehicles with pedestrians already in the crosswalk", "Collect night clips with headlight glare"],
            "source": "llm",
        }
    cams = ["sdg_warehouse_cam-2"] if SCENARIO["mode"] == "warehouse" else ["pie_cam-3", "neighborhood_cam-1"]
    return {"id": run["id"], "request": run["request"], "stage": stage, "error": "", "plan": {"query": run["request"].lower()} if stage != "searching" else None,
            "cameras": cams, "timings": {"search_s": 7.4, "verify_wall_s": 48.9, "verify_per_clip_s": 2.5},
            "counts": {"searched": 0 if stage == "searching" else len(cards) + 20, "verified": len(shown), "confirmed": conf, "rejected": rej, "unverified": unv},
            "candidates": shown, "report": report, "loop": {"iterations": its}, "needs_loop": stage == "done" and conf < 10 and not its, "target": 10}


@app.get("/")
def index():
    return FileResponse(STATIC)


@app.get("/api/cameras")
def cameras():
    return {"cameras": CAMS}


@app.post("/api/mine")
def mine(body: dict):
    rid = hashlib.md5(str(time.time()).encode()).hexdigest()[:8]
    if SCENARIO["mode"] == "warehouse":
        cards = make_cards("warehouse", "sdg_warehouse_cam-2", 9, 3, 1)
    else:
        cards = make_cards("street", "pie_cam-3", 6, 2)
    RUNS[rid] = {"id": rid, "request": body["request"], "t0": time.time(), "cards": cards}
    return {"id": rid}


@app.get("/api/runs/{rid}")
def run(rid: str):
    return view(RUNS[rid])


@app.get("/api/clip/{cid}")
def clip(cid: str):
    p = CLIPS / f"{cid}.mp4"
    if not p.exists():
        raise HTTPException(404)
    return FileResponse(p, media_type="video/mp4")


@app.get("/api/still/{cid}/{i}")
def still(cid: str, i: int):
    p = stills.still(CLIPS / f"{cid}.mp4", STILLS, i)
    if p is None:
        raise HTTPException(404)
    return FileResponse(p, media_type="image/jpeg")


@app.post("/api/runs/{rid}/loop/propose")
def propose(rid: str):
    run = RUNS[rid]
    rej = [c for c in run["cards"] if c["status"] != "confirmed"]
    return {"prompt": "Describe the scene in detail: the setting, every person and vehicle, what each is doing, and how the scene changes over the clip. Then state: the closest distance between any forklift and any person (under 2 m, 2 to 5 m, over 5 m); whether the person is walking, standing or running; the lighting (bright, dim, mixed); whether any aisle or walkway is blocked by pallets or inventory; whether either actor is partly hidden; and whether the whole interaction is visible from start to end.",
            "prompt_source": "llm", "chunks": ["s3://chunks/sdg_warehouse_cam-2/warehouse_set_03.mp4", "s3://chunks/sdg_warehouse_cam-2/warehouse_set_11.mp4"],
            "near_misses": [{"source": c["source"], "similarity": c["similarity"], "status": c["status"], "caption": c["caption"]} for c in rej]}


@app.post("/api/runs/{rid}/loop/approve")
def approve(rid: str, body: dict):
    run = RUNS[rid]
    run["iter"] = {"request": run["request"], "prompt": body["prompt"], "chunks": body["chunks"], "jobs": [], "before": {"confirmed": sum(c["status"] == "confirmed" for c in run["cards"])}, "status": "re-ingesting", "t0": time.time()}
    return {"started": True}


@app.post("/api/runs/{rid}/export")
def export(rid: str):
    return JSONResponse({"detail": "export disabled in preview"}, status_code=409)


if __name__ == "__main__":
    import uvicorn
    SCENARIO["mode"] = sys.argv[1] if len(sys.argv) > 1 else "warehouse"
    uvicorn.run(app, host="127.0.0.1", port=8765, log_level="warning")
