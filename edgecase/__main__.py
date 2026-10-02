"""CLI: python -m edgecase health | mine \"...\" """

from __future__ import annotations

import argparse
import json
import sys
import time

from .config import load_settings
from .llm import LLMClient
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
        # tiny cosmos text
        model = c.get(f"{settings.cosmos3_reason_url}/v1/models").json()["data"][0][
            "id"
        ]
        r = c.post(
            f"{settings.cosmos3_reason_url}/v1/chat/completions",
            json={
                "model": model,
                "messages": [{"role": "user", "content": "Reply with the single word: OK"}],
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


def cmd_mine(args: argparse.Namespace) -> int:
    print("Phase 1 miner not wired yet. Request was:", args.request)
    return 2


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
