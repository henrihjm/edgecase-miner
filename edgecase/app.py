"""FastAPI UI — Phase 4: one HTML page plus JSON endpoints. Run with: python -m edgecase serve"""

from __future__ import annotations

import re
import threading
import uuid
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from . import loop, stills, voice
from .config import Settings, load_settings
from .export import export_dataset
from .llm import LLMClient
from .miner import mine
from .report import build_report, counts, status
from .vss_client import VSSClient

app = FastAPI(title="Edge-Case Miner")
app.include_router(voice.router)
STATIC = Path(__file__).parent / "static"
RUNS: dict[str, dict[str, Any]] = {}


@lru_cache(maxsize=1)
def settings() -> Settings:
    return load_settings()


def get_run(run_id: str) -> dict[str, Any]:
    run = RUNS.get(run_id)
    if run is None:
        raise HTTPException(404, "unknown run")
    return run


def _background(run: dict[str, Any], fn: Callable[[], None]) -> None:
    def target() -> None:
        try:
            fn()
        except Exception as exc:
            run["error"] = f"{type(exc).__name__}: {exc}"
        finally:
            run["stage"] = "done"

    threading.Thread(target=target, daemon=True).start()


def _report(result) -> dict[str, Any]:
    llm = LLMClient(settings())
    try:
        return build_report(result, llm)
    finally:
        llm.close()


# Friendly names for the page. Anything not listed falls back to the raw camera_id.
CAMERA_NAMES: dict[str, dict[str, str]] = {
    "sdg_warehouse_cam-2": {"name": "Warehouse", "place": "Indoor"},
    "pie_cam-3": {"name": "Toronto dashcam", "place": "Toronto"},
    "neighborhood_cam-1": {"name": "Neighbourhood", "place": "Street camera"},
    "i24_cam-1": {"name": "I-24 highway", "place": "Nashville"},
    "sf_streets_cam-1": {"name": "SF streets 1", "place": "San Francisco"},
    "sf_streets_cam-2": {"name": "SF streets 2", "place": "San Francisco"},
    "sf_streets_cam-3": {"name": "SF streets 3", "place": "San Francisco"},
    "sf_streets_cam-4": {"name": "SF streets 4", "place": "San Francisco"},
    "smartspace_cam-1": {"name": "Smart space", "place": "Indoor"},
}


def _card(row: dict[str, Any]) -> dict[str, Any]:
    """What the page needs for one clip. No S3 URIs beyond the source, no tokens."""
    return {
        "clip_id": Path(row.get("clip_path") or "").stem,
        "source": row.get("source"),
        "filename": row.get("filename"),
        "camera_id": row.get("camera_id"),
        "camera": CAMERA_NAMES.get(row.get("camera_id") or "", {}).get("name") or row.get("camera_id"),
        "start_time": row.get("start_time"),
        "end_time": row.get("end_time"),
        "similarity": round(float(row.get("similarity") or 0), 3),
        "status": status(row),
        "verdict": row.get("verdict"),
        "reason": row.get("reason"),
        "reasoning": row.get("think_full") or row.get("think") or "",
        "caption": row.get("reasoning_content") or "",
        "labels": row.get("labels") or {},
        "quality": {
            "score": row.get("quality"),
            "blur": row.get("blur"),
            "detections": row.get("detections") or {},
        },
        "timings": row.get("timings") or {},
    }


def _view(run: dict[str, Any]) -> dict[str, Any]:
    result = run.get("result")
    rows = result.candidates if result else run.get("live_rows", [])
    cards = [_card(r) for r in rows]
    statuses = [c["status"] for c in cards]
    searched = result.searched if result else run.get("live_searched", 0)
    done = run["stage"] == "done" and result is not None
    return {
        "id": run["id"],
        "request": run["request"],
        "stage": run["stage"],
        "error": run.get("error", ""),
        "plan": {"query": result.query} if result else None,
        "cameras": (result.camera_ids or result.all_cameras) if result else [],
        "timings": result.timings if result else {},
        "counts": {
            "searched": max(searched, len(cards)),
            "verified": len(cards),
            "confirmed": statuses.count("confirmed"),
            "rejected": statuses.count("rejected"),
            "unverified": statuses.count("unverified"),
        },
        "candidates": cards,
        "report": run.get("report"),
        "loop": {"iterations": run["iterations"]},
        "needs_loop": done and loop.needs_loop(result, len(run["iterations"])),
        "target": loop.TARGET_CONFIRMED,
    }


class MineRequest(BaseModel):
    request: str = Field(min_length=3, max_length=300)
    groups: list[str] = []
    top_k: int = Field(40, ge=1, le=100)
    verify_n: int = Field(20, ge=1, le=20)


class ApproveRequest(BaseModel):
    prompt: str = Field(min_length=20, max_length=800)
    chunks: list[str] = Field(min_length=1, max_length=loop.MAX_CHUNKS)


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/cameras")
def cameras() -> dict[str, list[dict[str, Any]]]:
    try:
        with VSSClient(settings()) as vss:
            ids = list(vss.metadata_values("camera_id").get("values") or [])
    except Exception as exc:
        raise HTTPException(502, f"backend not reachable: {type(exc).__name__}")
    return {"cameras": [{"id": c, **CAMERA_NAMES.get(c, {"name": c, "place": ""})} for c in ids]}


@app.post("/api/mine")
def start_mine(body: MineRequest) -> dict[str, str]:
    run: dict[str, Any] = {
        "id": uuid.uuid4().hex[:8], "request": body.request, "stage": "searching", "error": "",
        "params": body.model_dump(), "result": None, "report": None, "iterations": [],
    }
    RUNS[run["id"]] = run

    def progress(stage: str, searched: int, rows: list[dict[str, Any]]) -> None:
        run.update(stage=stage, live_searched=searched, live_rows=rows)

    def work() -> None:
        result = mine(body.request, groups=body.groups or None, top_k=body.top_k,
                      verify_n=body.verify_n, settings=settings(), on_progress=progress)
        run["stage"] = "reporting"
        run["report"] = _report(result)
        run["result"] = result

    _background(run, work)
    return {"id": run["id"]}


@app.get("/api/runs/{run_id}")
def run_state(run_id: str) -> dict[str, Any]:
    return _view(get_run(run_id))


@app.get("/api/clip/{clip_id}")
def clip(clip_id: str) -> FileResponse:
    """Serves clips already downloaded during verification, so the backend token never reaches the page."""
    if not re.fullmatch(r"[0-9a-f]{24}", clip_id):
        raise HTTPException(400, "bad clip id")
    path = settings().cache_dir / "clips" / f"{clip_id}.mp4"
    if not path.is_file():
        raise HTTPException(404, "clip not downloaded")
    return FileResponse(path, media_type="video/mp4")


@app.get("/api/still/{clip_id}/{index}")
def still(clip_id: str, index: int) -> FileResponse:
    """One of five evenly spaced frames of a cached clip (2 is the poster). Rendered once, then cached."""
    if not re.fullmatch(r"[0-9a-f]{24}", clip_id) or not 0 <= index < stills.FRAMES:
        raise HTTPException(400, "bad still id")
    clip = settings().cache_dir / "clips" / f"{clip_id}.mp4"
    if not clip.is_file():
        raise HTTPException(404, "clip not downloaded")
    path = stills.still(clip, settings().cache_dir / "stills", index)
    if path is None:
        raise HTTPException(404, "could not decode clip")
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "max-age=86400"})


def _finished(run: dict[str, Any]):
    if run["stage"] != "done" or run.get("result") is None:
        raise HTTPException(409, "run is still in progress or failed")
    return run["result"]


@app.post("/api/runs/{run_id}/export")
def export_run(run_id: str) -> FileResponse:
    run = get_run(run_id)
    path = export_dataset(_finished(run), run["report"] or {}, settings(), run["iterations"])
    return FileResponse(path, media_type="application/zip", filename=path.name)


@app.post("/api/runs/{run_id}/loop/propose")
def loop_propose(run_id: str) -> dict[str, Any]:
    run = get_run(run_id)
    run["proposal"] = loop.propose(_finished(run), run["report"] or {}, settings())
    return run["proposal"]


@app.post("/api/runs/{run_id}/loop/approve")
def loop_approve(run_id: str, body: ApproveRequest) -> dict[str, bool]:
    """Starts the re-ingest. Only chunks from the stored proposal are accepted."""
    run = get_run(run_id)
    result = _finished(run)
    proposal = run.get("proposal")
    if not proposal:
        raise HTTPException(409, "propose first")
    if not set(body.chunks) <= set(proposal["chunks"]):
        raise HTTPException(400, "chunks must come from the proposal")
    if len(run["iterations"]) >= loop.MAX_ITERATIONS:
        raise HTTPException(409, f"loop limit reached ({loop.MAX_ITERATIONS} iterations)")
    approved = {**proposal, "prompt": body.prompt, "chunks": body.chunks}
    run.pop("proposal")
    run["stage"] = "re-ingesting"  # set before the thread starts so a poll never sees a stale "done"
    slot = len(run["iterations"])
    run["iterations"].append({"prompt": approved["prompt"], "chunks": approved["chunks"], "jobs": [],
                              "before": counts(result), "status": "starting"})

    def on_update(it: dict[str, Any]) -> None:
        run["iterations"][slot] = it

    def work() -> None:
        p = run["params"]
        new_result, new_report, _ = loop.execute(result, approved, settings(), top_k=p["top_k"],
                                                 verify_n=p["verify_n"], on_update=on_update)
        run["report"] = new_report
        run["result"] = new_result

    _background(run, work)
    return {"started": True}
