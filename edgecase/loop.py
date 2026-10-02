"""The agentic re-ingest loop: propose prompt, pick chunks, re-ingest, poll, search and verify again.

Re-ingest is shared team infrastructure and REPLACES segment descriptions, so nothing here starts
one without an explicit approval: `propose` is read-only, `execute` needs the proposal passed back.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone

from .config import REINGEST_LOG
from .llm import LLM
from .miner import count, mine
from .verify import Verifier
from .vss_client import VSS

TARGET_CONFIRMED = 10
MAX_ITERATIONS = 2
MAX_CHUNKS = 2


def needs_loop(run: dict, target: int = TARGET_CONFIRMED) -> bool:
    done = len(run.get("loop", {}).get("iterations", []))
    return run["counts"]["confirmed"] < target and done < MAX_ITERATIONS


def propose(run: dict, vss: VSS, llm: LLM) -> dict:
    """The superset prompt and the one or two chunks whose candidates came closest but did not pass."""
    near = sorted((c for c in run["candidates"] if c["status"] != "confirmed"), key=lambda c: -c["similarity"])
    chunks: list[str] = []
    for c in near:
        video = c.get("original_video")
        if not video:
            try:
                video = c["original_video"] = vss.metadata(c["source"]).get("original_video")
            except Exception:
                video = None
        if video and video not in chunks:
            chunks.append(video)
        if len(chunks) == MAX_CHUNKS:
            break
    cov = run.get("report", {}).get("coverage", {})
    summary = f"{cov.get('confirmed', 0)} confirmed; empty: {run.get('report', {}).get('empty_cells', [])[:6]}"
    prompt = llm.ingest_prompt(run["request"], [c["caption"] for c in near if c["caption"]], summary)
    return {"prompt": prompt, "prompt_source": llm.last_source, "chunks": chunks,
            "near_misses": [{"source": c["source"], "similarity": c["similarity"], "status": c["status"],
                             "caption": c["caption"][:300]} for c in near[:6]]}


def _log(entry: dict) -> None:
    with open(REINGEST_LOG, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry) + "\n")


def execute(run: dict, proposal: dict, vss: VSS, llm: LLM, verifier: Verifier, **mine_kw) -> dict:
    """Runs one approved iteration. Mutates `run` and returns the iteration record."""
    loop = run.setdefault("loop", {"iterations": []})
    if len(loop["iterations"]) >= MAX_ITERATIONS:
        raise RuntimeError(f"loop limit reached ({MAX_ITERATIONS} iterations)")
    if not proposal.get("chunks"):
        raise RuntimeError("no chunk to re-ingest: no near-miss candidate has a parent video")

    before = dict(run["counts"])
    started = time.time()
    it = {"prompt": proposal["prompt"], "chunks": proposal["chunks"], "jobs": [], "before": before,
          "started": datetime.now(timezone.utc).isoformat(timespec="seconds"), "status": "re-ingesting"}
    loop["iterations"].append(it)
    run["stage"] = "re-ingesting"
    try:
        for video in proposal["chunks"][:MAX_CHUNKS]:
            job = vss.reingest(video, proposal["prompt"], chunk_count=1)
            job_id = job.get("job_id")
            it["jobs"].append({"original_video": video, "job_id": job_id, "progress": ""})

            def on_progress(st, rec=it["jobs"][-1]):
                rec["progress"] = (f"{st.get('completed_chunks', '?')}/{st.get('total_chunks', '?')} chunks, "
                                   f"{st.get('indexed_segments', '?')}/{st.get('total_segments', '?')} clips")

            vss.wait_reingest(job_id, on_progress)
        it["reingest_seconds"] = round(time.time() - started, 1)

        it["status"] = "searching again"
        groups = mine_kw.pop("groups", None) or run.get("cameras")
        mine(run["request"], vss, llm, verifier, groups=groups, run=run, **mine_kw)
        it.update(after=count(run), status="completed")
    except Exception as exc:
        it.update(status="failed", error=f"{type(exc).__name__}: {exc}")
        run["stage"] = "done"
        raise
    finally:
        it["seconds"] = round(time.time() - started, 1)
        run["loop"] = loop  # mine() rewrites run fields; keep the loop log attached
        _log({"run": run["id"], "request": run["request"], **it})
    return it
