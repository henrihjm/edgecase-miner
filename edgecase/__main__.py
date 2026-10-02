"""CLI: python -m edgecase health | mine \"...\" """

from __future__ import annotations

import argparse
import sys
import time

from .config import load_settings
from .llm import LLMClient
from .miner import mine
from .vss_client import VSSClient


def cmd_health(_: argparse.Namespace) -> int:
    settings = load_settings()
    print("=== Edge-Case Miner Phase 0 health ===")
    with VSSClient(settings) as vss:
        vss.login()
        print("login: OK")
        stats = vss.dashboard_stats()
        overview = stats.get("overview", {})
        print(
            f"indexed: {overview.get('indexed_clips')} clips / "
            f"{overview.get('unique_videos')} videos"
        )
        meta = stats.get("metadata", {})
        cams = meta.get("camera_id") or []
        print("camera_id counts:")
        if isinstance(cams, list):
            for row in sorted(cams, key=lambda x: -int(x.get("count") or 0)):
                print(f"  {row.get('label')}: {row.get('count')}")
        schema = vss.metadata_schema()
        fields = [f.get("name") for f in schema.get("schema", [])]
        print(f"filterable fields: {', '.join(fields)}")
        values = vss.metadata_values("camera_id")
        print(f"cameras live: {', '.join(values.get('values') or [])}")

    import httpx

    auth = {"Authorization": f"Bearer {settings.gpu_bearer_token}"}
    with httpx.Client(timeout=30.0, headers=auth) as c:
        r = c.get(f"{settings.cosmos3_reason_url}/v1/models")
        print(f"cosmos models: {r.status_code} id={r.json()['data'][0]['id']}")
        r = c.get(f"{settings.yolo_url}/healthz")
        y = r.json()
        print(f"yolo healthz: ok={y.get('ok')} loaded={y.get('model_loaded')}")
        r = c.get(f"{settings.cosmos_embed1_url}/v1/models")
        print(f"embed1 models: {r.status_code} id={r.json()['data'][0]['id']}")
        model = c.get(f"{settings.cosmos3_reason_url}/v1/models").json()["data"][0][
            "id"
        ]
        r = c.post(
            f"{settings.cosmos3_reason_url}/v1/chat/completions",
            json={
                "model": model,
                "messages": [
                    {"role": "user", "content": "Reply with the single word: OK"}
                ],
                "max_tokens": 16,
                "temperature": 0,
            },
        )
        content = r.json()["choices"][0]["message"].get("content", "")
        print(f"cosmos text: {content!r}")

    llm = LLMClient(settings)
    try:
        t0 = time.time()
        out = llm.chat(
            [{"role": "user", "content": "Reply with the single word: OK"}],
            max_tokens=8,
        )
        print(f"wandb inference: {out!r} ({time.time()-t0:.2f}s)")
    except Exception as e:
        print(f"wandb inference: FAIL ({type(e).__name__}: {e})")
        return 1
    finally:
        llm.close()

    print("health: PASS")
    return 0


def _short_source(source: str, width: int = 42) -> str:
    name = source.rsplit("/", 1)[-1]
    if len(name) <= width:
        return name
    return name[: width - 3] + "..."


def cmd_mine(args: argparse.Namespace) -> int:
    settings = load_settings()
    print(f"mining: {args.request!r}")
    print(
        f"groups={args.groups or ['all']} top_k={args.top_k} verify={args.verify}"
    )
    t0 = time.time()
    result = mine(
        args.request,
        groups=args.groups,
        top_k=args.top_k,
        verify_n=args.verify,
        settings=settings,
    )
    wall = time.time() - t0

    print(f"search query: {result.query!r}")
    print(f"cameras: {result.camera_ids or ['(all)']}")
    print(
        f"timings: search={result.timings.get('search_s')}s "
        f"verify_wall={result.timings.get('verify_wall_s')}s "
        f"~{result.timings.get('verify_per_clip_s')}s/clip "
        f"total={wall:.1f}s"
    )
    print(
        f"confirmed={len(result.confirmed)} rejected={len(result.rejected)} "
        f"checked={len(result.candidates)}"
    )
    print()
    header = (
        f"{'KEEP':4} {'SIM':>5} {'VER':>10} {'Q':>5}  "
        f"{'SOURCE':42}  REASON / THINK"
    )
    print(header)
    print("-" * len(header))
    for row in result.candidates:
        keep = "YES" if row.get("keep") else "no"
        sim = f"{float(row.get('similarity') or 0):.3f}"
        ver = str(row.get("verdict") or "?")
        q = f"{float(row.get('quality') or 0):.2f}"
        src = _short_source(row.get("source") or "")
        reason = (row.get("reason") or "")[:40]
        think = (row.get("think") or "")[:70]
        print(f"{keep:4} {sim:>5} {ver:>10} {q:>5}  {src:42}  {reason}")
        print(f"{'':4} {'':5} {'':10} {'':5}  {'':42}  {think}")

    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="edgecase")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_health = sub.add_parser("health", help="Phase 0 health checks")
    p_health.set_defaults(func=cmd_health)

    p_mine = sub.add_parser("mine", help="Mine clips for a request")
    p_mine.add_argument("request", help="Plain-English event description")
    p_mine.add_argument("--groups", nargs="*", default=None)
    p_mine.add_argument("--top-k", type=int, default=40)
    p_mine.add_argument("--verify", type=int, default=20)
    p_mine.set_defaults(func=cmd_mine)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
