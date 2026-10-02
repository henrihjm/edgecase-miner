"""FastAPI app: one HTML page plus JSON endpoints. Run with: python -m edgecase serve"""
from __future__ import annotations

import re
import threading
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from . import config, export as exporter, loop
from .llm import LLM
from .miner import mine
from .verify import MAX_CALLS, Verifier
from .vss_client import CLIP_DIR, VSS

app = FastAPI(title="Edge-Case Miner")
STATIC = Path(__file__).parent / "static"
RUNS: dict[str, dict] = {}
_clients: tuple[VSS, LLM, Verifier] | None = None
_lock = threading.Lock()


def clients() -> tuple[VSS, LLM, Verifier]:
    global _clients
    with _lock:
        if _clients is None:
            s = config.load()
            _clients = (VSS(s), LLM(s), Verifier(s))
        return _clients


def get_run(run_id: str) -> dict:
    run = RUNS.get(run_id)
    if run is None:
        raise HTTPException(404, "unknown run")
    return run


def _background(run: dict, fn) -> None:
    def target():
        try:
            fn()
        except Exception as exc:
            run.update(stage="done", error=f"{type(exc).__name__}: {exc}")
    threading.Thread(target=target, daemon=True).start()


class MineRequest(BaseModel):
    request: str = Field(min_length=3, max_length=300)
    groups: list[str] = []
    top_k: int = Field(40, ge=1, le=100)
    verify_n: int = Field(MAX_CALLS, ge=1, le=MAX_CALLS)


class ApproveRequest(BaseModel):
    prompt: str = Field(min_length=20, max_length=800)
    chunks: list[str] = Field(min_length=1, max_length=loop.MAX_CHUNKS)


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/cameras")
def cameras():
    try:
        return {"cameras": clients()[0].camera_ids()}
    except Exception as exc:
        raise HTTPException(502, f"backend not reachable: {type(exc).__name__}: {exc}")


@app.post("/api/mine")
def start_mine(body: MineRequest):
    vss, llm, verifier = clients()
    run = {"id": uuid.uuid4().hex[:8], "request": body.request, "stage": "starting", "candidates": [],
           "counts": {}, "error": "", "params": body.model_dump()}
    RUNS[run["id"]] = run
    _background(run, lambda: mine(body.request, vss, llm, verifier, groups=body.groups,
                                  top_k=body.top_k, verify_n=body.verify_n, run=run))
    return {"id": run["id"]}


@app.get("/api/runs/{run_id}")
def run_state(run_id: str):
    run = get_run(run_id)
    return {**run, "needs_loop": run.get("stage") == "done" and bool(run.get("counts")) and loop.needs_loop(run),
            "target": loop.TARGET_CONFIRMED}


@app.get("/api/clip/{clip_id}")
def clip(clip_id: str):
    """Serves clips already downloaded during verification, so the backend token never reaches the page."""
    if not re.fullmatch(r"[0-9a-f]{64}", clip_id):
        raise HTTPException(400, "bad clip id")
    path = CLIP_DIR / f"{clip_id}.mp4"
    if not path.is_file():
        raise HTTPException(404, "clip not downloaded")
    return FileResponse(path, media_type="video/mp4")


@app.post("/api/runs/{run_id}/export")
def export_run(run_id: str):
    run = get_run(run_id)
    if run.get("stage") != "done":
        raise HTTPException(409, "run is still in progress")
    path = exporter.export(run)
    return FileResponse(path, media_type="application/zip", filename=path.name)


@app.post("/api/runs/{run_id}/loop/propose")
def loop_propose(run_id: str):
    run = get_run(run_id)
    if run.get("stage") != "done":
        raise HTTPException(409, "run is still in progress")
    vss, llm, _ = clients()
    run["proposal"] = loop.propose(run, vss, llm)
    return run["proposal"]


@app.post("/api/runs/{run_id}/loop/approve")
def loop_approve(run_id: str, body: ApproveRequest):
    """Starts the re-ingest. Only chunks from the stored proposal are accepted."""
    run = get_run(run_id)
    proposal = run.get("proposal")
    if run.get("stage") != "done" or not proposal:
        raise HTTPException(409, "propose first, and wait for the run to finish")
    if not set(body.chunks) <= set(proposal["chunks"]):
        raise HTTPException(400, "chunks must come from the proposal")
    if not loop.needs_loop(run, target=10 ** 9):
        raise HTTPException(409, f"loop limit reached ({loop.MAX_ITERATIONS} iterations)")
    vss, llm, verifier = clients()
    approved = {**proposal, "prompt": body.prompt, "chunks": body.chunks}
    run.pop("proposal")
    run["stage"] = "re-ingesting"  # set before the thread starts so a poll never sees a stale "done"
    p = run.get("params", {})
    _background(run, lambda: loop.execute(run, approved, vss, llm, verifier,
                                          top_k=p.get("top_k", 40), verify_n=p.get("verify_n", MAX_CALLS)))
    return {"started": True}
