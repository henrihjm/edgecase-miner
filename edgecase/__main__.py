"""CLI: python -m edgecase mine "forklift passing close to a person" --groups warehouse --top-k 40 --verify 20"""
from __future__ import annotations

import argparse
import sys

import httpx

from . import config, export as exporter, loop
from .llm import LLM
from .miner import mine
from .verify import MAX_CALLS, Verifier
from .vss_client import VSS

WAREHOUSE_PROBES = ("forklift near a person", "person in a walkway", "pallet blocking an aisle")


def _short(text: str, n: int) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= n else text[: n - 1] + "…"


def print_table(run: dict) -> None:
    print(f"\nRequest: {run['request']}\nSearch query: {run['plan']['query']}  (plan by {run['plan']['source']})")
    print(f"Camera groups: {', '.join(run['cameras'])}\n")
    print(f"{'status':<11}{'sim':>6}{'qual':>6}  {'camera':<22}{'reason':<44}Cosmos reasoning")
    for c in run["candidates"]:
        if c["status"] == "candidate":
            continue
        q = (c.get("quality") or {}).get("score")
        print(f"{c['status']:<11}{c['similarity']:>6}{q if q is not None else '-':>6}  "
              f"{_short(c['camera_id'], 21):<22}{_short(c.get('reason'), 43):<44}{_short(c.get('reasoning'), 90)}")
    print(f"\nCounts: {run['counts']}\nTimings: {run['timings']}")
    rep = run["report"]
    print(f"\nGap report ({rep.get('source')}): {rep['gap_report']}")
    for line in rep["collection_plan"]:
        print(f"  - {line}")


def cmd_health(args) -> int:
    s = config.load()
    vss, llm = VSS(s), LLM(s)
    vss.login()
    stats = vss.stats()
    ov = stats.get("overview", {})
    print(f"Backend login OK. Videos: {ov.get('unique_videos')}  segments: {ov.get('segment_rows')}  "
          f"indexed clips: {ov.get('indexed_clips')}")
    print("camera_id counts:", stats.get("metadata", {}).get("camera_id") or vss.camera_ids())
    headers = {"Authorization": f"Bearer {s.gpu_token}"} if s.gpu_token else {}
    for name, url in (("Cosmos3-Reason", s.cosmos_url + "/v1/models"), ("YOLO11", s.yolo_url + "/healthz")):
        try:
            r = httpx.get(url, headers=headers, timeout=15)
            print(f"{name}: HTTP {r.status_code}")
        except httpx.HTTPError as exc:
            print(f"{name}: unreachable ({type(exc).__name__})")
    answer = llm.chat("You are a health check.", "Reply with the single word: OK", max_tokens=16)
    print(f"Reasoning LLM: {answer!r} via {llm.last_source}" + (f" (model {llm.model()})" if llm.last_source == "wandb" else ""))
    if args.captions:
        cams = [c for c in vss.camera_ids() if "warehouse" in c.lower()]
        for probe in WAREHOUSE_PROBES:
            print(f"\n== {probe}")
            rows = vss.search(probe, top_k=5, metadata_filters={"camera_id": cams[0]} if cams else None).get("results", [])
            for row in rows[:5]:
                print(f"  [{row.get('similarity_score')}] {_short(row.get('reasoning_content'), 400)}")
    return 0


def cmd_mine(args) -> int:
    s = config.load()
    vss, llm, verifier = VSS(s), LLM(s), Verifier(s)
    kw = {"top_k": args.top_k, "verify_n": args.verify}
    run = mine(args.request, vss, llm, verifier, groups=args.groups, **kw)
    print_table(run)
    while args.loop and loop.needs_loop(run, args.target):
        proposal = loop.propose(run, vss, llm)
        print(f"\nOnly {run['counts']['confirmed']} confirmed (target {args.target}). Proposed re-ingest:")
        print(f"  prompt ({len(proposal['prompt'])} chars): {proposal['prompt']}")
        print("  chunks (chunk_count 1 each):", *proposal["chunks"], sep="\n    ")
        if not proposal["chunks"] or input("Re-ingest replaces these chunks' captions. Start? [y/N] ").strip().lower() != "y":
            print("Skipped re-ingest.")
            break
        it = loop.execute(run, proposal, vss, llm, verifier, **kw)
        print(f"Iteration took {it['seconds']}s (re-ingest {it.get('reingest_seconds')}s). "
              f"Confirmed before {it['before']['confirmed']}, after {it['after']['confirmed']}.")
        print_table(run)
    if args.export:
        print(f"\nExported: {exporter.export(run)}")
    return 0


def cmd_serve(args) -> int:
    import uvicorn
    uvicorn.run("edgecase.app:app", host=args.host, port=args.port)
    return 0


def main() -> int:
    p = argparse.ArgumentParser(prog="edgecase")
    sub = p.add_subparsers(dest="cmd", required=True)
    h = sub.add_parser("health", help="Phase 0 checks: backend, models, indexed counts")
    h.add_argument("--captions", action="store_true", help="also print warehouse captions for three probe searches")
    h.set_defaults(fn=cmd_health)
    m = sub.add_parser("mine", help="mine a dataset from one request")
    m.add_argument("request")
    m.add_argument("--groups", nargs="*", help="camera groups, loose match on camera_id (e.g. warehouse pie)")
    m.add_argument("--top-k", type=int, default=40)
    m.add_argument("--verify", type=int, default=MAX_CALLS, choices=range(1, MAX_CALLS + 1), metavar=f"1..{MAX_CALLS}")
    m.add_argument("--target", type=int, default=loop.TARGET_CONFIRMED)
    m.add_argument("--loop", action="store_true", help="offer the re-ingest loop when too few clips pass (asks first)")
    m.add_argument("--export", action="store_true")
    m.set_defaults(fn=cmd_mine)
    sv = sub.add_parser("serve", help="run the web UI")
    sv.add_argument("--host", default="127.0.0.1")
    sv.add_argument("--port", type=int, default=8000)
    sv.set_defaults(fn=cmd_serve)
    args = p.parse_args()
    try:
        return args.fn(args)
    except config.ConfigError as exc:
        print(exc, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
