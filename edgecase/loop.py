"""Agentic re-ingest loop — Phase 3: propose prompt, pick chunks, re-ingest, poll, search and verify again.

Re-ingest is shared team infrastructure and REPLACES segment descriptions, so nothing here starts
one without an explicit approval: `propose` is read-only, `execute` needs the proposal passed back.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from typing import Any, Callable

from .config import Settings
from .llm import LLMClient
from .miner import MineResult, mine
from .report import build_report, counts, status
from .vss_client import VSSClient

TARGET_CONFIRMED = 10
MAX_ITERATIONS = 2
MAX_CHUNKS = 2


def needs_loop(result: MineResult, iterations_done: int, target: int = TARGET_CONFIRMED) -> bool:
    return len(result.confirmed) < target and iterations_done < MAX_ITERATIONS


def propose(result: MineResult, report: dict[str, Any], settings: Settings) -> dict[str, Any]:
    """The superset prompt and the one or two chunks whose candidates came closest but did not pass."""
    near = sorted(result.rejected, key=lambda r: -float(r.get("similarity") or 0))
    chunks: list[str] = []
    with VSSClient(settings) as vss:
        for row in near:
            video = row.get("original_video")
            if not video:
                try:
                    video = vss.video_metadata(row["source"]).get("original_video")
                except Exception:
                    video = None
            if video and video not in chunks:
                chunks.append(video)
            if len(chunks) == MAX_CHUNKS:
                break
    summary = f"{len(result.confirmed)} confirmed; empty: {report.get('empty_cells', [])[:6]}"
    llm = LLMClient(settings)
    try:
        prompt = llm.ingest_prompt(
            result.request, [r["reasoning_content"] for r in near if r.get("reasoning_content")], summary
        )
    finally:
        llm.close()
    return {
        "prompt": prompt["prompt"],
        "prompt_source": prompt["source"],
        "chunks": chunks,
        "near_misses": [
            {"source": r["source"], "similarity": r.get("similarity"), "status": status(r),
             "caption": (r.get("reasoning_content") or "")[:300]}
            for r in near[:6]
        ],
    }


def execute(
    result: MineResult,
    proposal: dict[str, Any],
    settings: Settings,
    *,
    top_k: int = 40,
    verify_n: int = 20,
    on_update: Callable[[dict[str, Any]], None] | None = None,
) -> tuple[MineResult, dict[str, Any], dict[str, Any]]:
    """One approved iteration. Returns (new result, new report, iteration record) and logs it."""
    if not proposal.get("chunks"):
        raise RuntimeError("no chunk to re-ingest: no near-miss candidate has a parent video")
    started = time.time()
    it: dict[str, Any] = {
        "request": result.request,
        "prompt": proposal["prompt"],
        "chunks": proposal["chunks"][:MAX_CHUNKS],
        "jobs": [],
        "before": counts(result),
        "started": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "status": "re-ingesting",
    }

    def update() -> None:
        if on_update:
            on_update(it)

    try:
        with VSSClient(settings) as vss:
            for video in it["chunks"]:
                job = vss.reingest(original_video=video, chunk_count=1, custom_prompt=it["prompt"])
                rec = {"original_video": video, "job_id": job.get("job_id"), "progress": ""}
                it["jobs"].append(rec)
                update()
                deadline = time.time() + 900
                while True:
                    st = vss.reingest_status(rec["job_id"])
                    rec["progress"] = (
                        f"{st.get('completed_chunks', '?')}/{st.get('total_chunks', '?')} chunks, "
                        f"{st.get('indexed_segments', '?')}/{st.get('total_segments', '?')} clips"
                    )
                    update()
                    state = str(st.get("status") or "").lower()
                    if state == "completed":
                        break
                    if state in ("failed", "error"):
                        raise RuntimeError(f"re-ingest {rec['job_id']} ended with status {state}")
                    if time.time() > deadline:
                        raise TimeoutError(f"re-ingest {rec['job_id']} did not complete in 15 minutes")
                    time.sleep(4)
        it["reingest_seconds"] = round(time.time() - started, 1)

        it["status"] = "searching again"
        update()
        # Same camera selection as the first pass.
        new_result = mine(result.request, groups=result.camera_ids or None, top_k=top_k,
                          verify_n=verify_n, settings=settings)
        llm = LLMClient(settings)
        try:
            new_report = build_report(new_result, llm)
        finally:
            llm.close()
        it.update(after=counts(new_result), status="completed")
        return new_result, new_report, it
    except Exception as exc:
        it.update(status="failed", error=f"{type(exc).__name__}: {exc}")
        raise
    finally:
        it["seconds"] = round(time.time() - started, 1)
        update()
        with open(settings.repo_root / "reingest_log.jsonl", "a", encoding="utf-8") as fh:
            fh.write(json.dumps(it) + "\n")
