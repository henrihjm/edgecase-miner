"""A fake VSS backend, Cosmos3-Reason and W&B endpoint on one port, for offline end-to-end tests.

Shapes follow .cursor/skills/. The real stack may differ in details the docs do not specify.
"""
from __future__ import annotations

import base64
import tempfile
from pathlib import Path

import cv2
import numpy as np
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response

app = FastAPI()
CAMERAS = ["sdg_warehouse_cam-2", "pie_cam-3", "i24_cam-1"]
STATE = {"reingested": False, "polls": 0, "reingest_calls": []}
MARK = b"MOCKSRC:"


def _mp4() -> bytes:
    path = Path(tempfile.mkdtemp()) / "c.mp4"
    w = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 5, (160, 120))
    rng = np.random.default_rng(0)
    for _ in range(10):
        w.write(rng.integers(0, 255, (120, 160, 3), dtype=np.uint8))
    w.release()
    return path.read_bytes()


CLIP = _mp4()


def _segments(cam: str) -> list[dict]:
    n = 6 if (cam == "sdg_warehouse_cam-2" and STATE["reingested"]) else 4 if cam != "i24_cam-1" else 0
    return [{"source": f"s3://seg/{cam}/{i}.mp4", "original_video": f"s3://chunks/{cam}/parent.mp4",
             "camera_id": cam, "similarity_score": 0.8 - i * 0.05, "start_sec": i * 10, "end_sec": i * 10 + 10,
             "reasoning_content": f"A forklift moves near a worker in aisle {i}."} for i in range(n)]


@app.post("/api/v1/auth/login")
def login():
    return {"access_token": "mock-token", "token_type": "bearer"}


@app.get("/api/v1/metadata/values")
def values(field: str):
    return {"field": field, "values": CAMERAS}


@app.get("/api/v1/dashboard/stats")
def stats():
    return {"overview": {"unique_videos": 3, "segment_rows": 8}, "metadata": {"camera_id": {c: 4 for c in CAMERAS}}}


@app.post("/api/v1/search")
async def search(req: Request):
    body = await req.json()
    cam = body.get("metadata_filters", {}).get("camera_id")
    rows = [s for c in ([cam] if cam else CAMERAS) for s in _segments(c)]
    return {"results": rows[: body.get("top_k", 15)], "chunk_results": []}


@app.get("/api/v1/videos/stream")
def stream(source: str, token: str):
    if token != "mock-token":
        raise HTTPException(401)
    return Response(CLIP + MARK + source.encode(), media_type="video/mp4")


@app.get("/api/v1/videos/detections")
def detections(source: str):
    if source.endswith("/1.mp4"):
        raise HTTPException(404)
    return {"object_counts": {"person": 2, "truck": 1}}


@app.get("/api/v1/videos/metadata")
def metadata(source: str):
    return {"source": source, "original_video": source.rsplit("/", 1)[0] + "/parent.mp4"}


@app.post("/api/v1/dashboard/reingest")
async def reingest(req: Request):
    STATE["reingest_calls"].append(await req.json())
    STATE["polls"] = 0
    return {"job_id": "job-1", "selected_chunks": 1, "copied_segments": 4}


@app.get("/api/v1/dashboard/reingest/{job_id}")
def reingest_status(job_id: str):
    STATE["polls"] += 1
    done = STATE["polls"] >= 2
    STATE["reingested"] = STATE["reingested"] or done
    return {"status": "completed" if done else "running", "completed_chunks": int(done), "total_chunks": 1,
            "indexed_segments": 4 if done else 1, "total_segments": 4}


@app.get("/cosmos/v1/models")
def cosmos_models():
    return {"data": [{"id": "nvidia/cosmos3-reason"}]}


@app.post("/cosmos/v1/chat/completions")
async def cosmos_chat(req: Request):
    content = (await req.json())["messages"][-1]["content"]
    if isinstance(content, str):
        return {"choices": [{"message": {"content": "OK"}}]}
    video = base64.b64decode(content[1]["video_url"]["url"].split(",", 1)[1])
    source = video.rsplit(MARK, 1)[1].decode()
    i = int(source.rsplit("/", 1)[1].split(".")[0])
    if i == 3:  # overloaded endpoint
        return {"choices": [{"message": {"content": ""}}]}
    yes = i != 2
    visible = "false" if i == 4 else "true"
    labels = ('{"actors": ["person", "forklift"], "action": "passing", "distance_class": "under_2m", '
              f'"lighting": "{"indoor" if "warehouse" in source else "day"}", "weather": "unknown", '
              f'"occlusion": "none", "event_fully_visible": {visible}}}')
    return {"choices": [{"message": {"content":
            f"<think>Clip {i}: I looked at the forklift and the worker.</think>\n"
            f"<answer>{'YES' if yes else 'NO'}</answer>\n<labels>{labels}</labels>"}}]}


@app.get("/wandb/v1/models")
def wandb_models():
    return {"data": [{"id": "mock/llm"}]}


@app.post("/wandb/v1/chat/completions")
async def wandb_chat(req: Request):
    system = (await req.json())["messages"][0]["content"]
    if "plan searches" in system:
        text = '```json\n{"query": "forklift close to person", "event": "a forklift passing close to a person", "camera_ids": []}\n```'
    elif "captioning model" in system:
        text = "Describe the scene in general, then each actor's action, distances between actors, lighting and occlusion."
    else:
        text = '{"gap_report": "Nothing from the highway view.", "collection_plan": ["Collect highway footage.", "Collect night footage.", "Collect rain footage."]}'
    return {"choices": [{"message": {"content": text}}]}
