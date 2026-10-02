"""Orchestration: request -> search -> verify -> score -> results."""
from __future__ import annotations

import time
import uuid
from concurrent.futures import ThreadPoolExecutor

from . import quality, report
from .llm import LLM
from .verify import CONCURRENCY, MAX_CALLS, Verifier
from .vss_client import VSS, clip_path

START_KEYS = ("start_sec", "segment_start_sec", "start_time", "best_match_start_sec")
END_KEYS = ("end_sec", "segment_end_sec", "end_time", "best_match_end_sec")


def _first(row: dict, keys) -> object:
    return next((row[k] for k in keys if row.get(k) is not None), None)


def resolve_groups(groups: list[str] | None, camera_ids: list[str]) -> list[str]:
    """Match loose group names ("warehouse") to live camera_id values."""
    if not groups:
        return []
    wanted = [g.lower() for g in groups]
    return [c for c in camera_ids if any(g in c.lower() for g in wanted)]


def search_candidates(vss: VSS, query: str, cameras: list[str], top_k: int, min_similarity: float) -> list[dict]:
    """One search per camera group so every group is represented, merged and de-duplicated."""
    seen: dict[str, dict] = {}
    for cam in cameras or [None]:
        filters = {"camera_id": cam} if cam else {}
        for row in vss.search(query, top_k=top_k, min_similarity=min_similarity, metadata_filters=filters).get("results", []):
            source = row.get("source")
            if not source or source in seen:
                continue
            seen[source] = {
                "source": source,
                "clip_id": clip_path(source).stem,
                "original_video": row.get("original_video"),
                "camera_id": row.get("camera_id") or cam or "unknown",
                "similarity": round(float(row.get("similarity_score") or 0), 3),
                "caption": row.get("reasoning_content") or "",
                "start_sec": _first(row, START_KEYS),
                "end_sec": _first(row, END_KEYS),
                "status": "candidate",
            }
    return sorted(seen.values(), key=lambda c: -c["similarity"])


def pick_for_verification(candidates: list[dict], n: int) -> list[dict]:
    """Round-robin across camera groups, best similarity first, so coverage is not one camera."""
    by_cam: dict[str, list[dict]] = {}
    for c in candidates:
        by_cam.setdefault(c["camera_id"], []).append(c)
    picked: list[dict] = []
    while len(picked) < n and any(by_cam.values()):
        for cam in list(by_cam):
            if by_cam[cam] and len(picked) < n:
                picked.append(by_cam[cam].pop(0))
    return picked


def _process(c: dict, request: str, event: str, vss: VSS, verifier: Verifier) -> None:
    try:
        clip = vss.download(c["source"])
    except Exception as exc:  # shared infrastructure: record and move on
        c.update(status="unverified", reason=f"clip download failed ({type(exc).__name__})",
                 reasoning=c["caption"], labels={})
        return
    v = verifier.verify(c["source"], clip, event, stored_caption=c["caption"])
    c.update(reasoning=v["reasoning"], labels=v["labels"], verify_seconds=v["seconds"], cached=v["cached"])
    if v["verdict"] == "unverified":
        c.update(status="unverified", reason=f"Cosmos gave no verdict: {v['error']}; showing the stored caption")
        return
    if v["verdict"] == "no":
        c.update(status="rejected", reason="Cosmos: the clip does not show the event")
        return
    try:
        sidecar = vss.detections(c["source"])
    except Exception:
        sidecar = None
    q = quality.score(request, v["labels"], sidecar, clip)
    c["quality"] = q
    c.update(status="confirmed" if q["keep"] else "rejected", reason=q["reason"])


def count(run: dict) -> dict:
    statuses = [c["status"] for c in run["candidates"]]
    return {"searched": len(statuses), "verified": sum(s != "candidate" for s in statuses),
            "confirmed": statuses.count("confirmed"), "rejected": statuses.count("rejected"),
            "unverified": statuses.count("unverified")}


def mine(request: str, vss: VSS, llm: LLM, verifier: Verifier, groups: list[str] | None = None,
         top_k: int = 40, verify_n: int = MAX_CALLS, min_similarity: float = 0.3, run: dict | None = None) -> dict:
    """Runs one mining pass. `run` is mutated in place so a UI can poll progress."""
    run = run if run is not None else {}
    run.update(id=run.get("id") or uuid.uuid4().hex[:8], request=request, stage="planning",
               candidates=[], timings={}, error="")
    t0 = time.time()
    all_cameras = vss.camera_ids()
    plan = llm.plan(request, all_cameras)
    cameras = resolve_groups(groups, all_cameras) or plan["camera_ids"] or all_cameras
    run.update(plan=plan, cameras=cameras, stage="searching")

    t1 = time.time()
    run["candidates"] = search_candidates(vss, plan["query"], cameras, top_k, min_similarity)
    run["timings"]["search_s"] = round(time.time() - t1, 1)
    run.update(counts=count(run), stage="verifying")

    t2 = time.time()
    picked = pick_for_verification(run["candidates"], min(verify_n, MAX_CALLS))

    def work(c):
        _process(c, request, plan["event"], vss, verifier)
        run["counts"] = count(run)

    with ThreadPoolExecutor(max_workers=CONCURRENCY) as pool:
        list(pool.map(work, picked))
    fresh = [c["verify_seconds"] for c in picked if c.get("verify_seconds") and not c.get("cached")]
    run["timings"].update(verify_s=round(time.time() - t2, 1),
                          verify_per_clip_s=round(sum(fresh) / len(fresh), 1) if fresh else None)

    run.update(counts=count(run), stage="reporting")
    run["report"] = report.build(run, llm)
    run["timings"]["total_s"] = round(time.time() - t0, 1)
    run["stage"] = "done"
    return run
