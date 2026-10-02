"""Offline end-to-end test against tests/mock_stack.py. Run: python -m pytest tests -q"""
import dataclasses
import json
import os
import tempfile
import threading
import time
import zipfile
from pathlib import Path

import httpx
import uvicorn

PORT = 9177
BASE = f"http://127.0.0.1:{PORT}"
os.environ.update(INGRESS_URL=BASE, USERNAME="team-x", PASSWORD="x", GPU_BEARER_TOKEN="t",
                  COSMOS3_REASON_URL=BASE + "/cosmos", WANDB_BASE_URL=BASE + "/wandb/v1",
                  WANDB_API_KEY="k", WANDB_TEAM="t", WANDB_PROJECT="p")

from edgecase import loop  # noqa: E402
from edgecase.config import load_settings  # noqa: E402
from edgecase.export import export_dataset  # noqa: E402
from edgecase.llm import LLMClient  # noqa: E402
from edgecase.miner import mine  # noqa: E402
from edgecase.report import build_report, counts, status  # noqa: E402
from tests import mock_stack  # noqa: E402


def _serve():
    server = uvicorn.Server(uvicorn.Config(mock_stack.app, port=PORT, log_level="error"))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(50):
        try:
            httpx.get(BASE + "/cosmos/v1/models")
            return
        except httpx.HTTPError:
            time.sleep(0.1)


def _settings():
    tmp = Path(tempfile.mkdtemp())
    for d in ("cache/clips", "cache/verify", "exports"):
        (tmp / d).mkdir(parents=True)
    return dataclasses.replace(load_settings(), repo_root=tmp, cache_dir=tmp / "cache", exports_dir=tmp / "exports")


def test_end_to_end():
    _serve()
    settings = _settings()
    progress = []
    result = mine("forklift passing close to a person", settings=settings,
                  on_progress=lambda stage, n, rows: progress.append((stage, n, len(rows))))
    by = {r["source"].split("seg/")[1]: r for r in result.candidates}
    assert result.query == "forklift close to person"
    assert counts(result) == {"searched": 8, "verified": 8, "confirmed": 4, "rejected": 2, "unverified": 2}
    assert progress[0] == ("verifying", 8, 0) and progress[-1] == ("verifying", 8, 8)
    assert status(by["pie_cam-3/2.mp4"]) == "rejected"            # Cosmos said NO
    assert status(by["pie_cam-3/3.mp4"]) == "unverified"          # empty content -> stored caption
    assert by["pie_cam-3/3.mp4"]["think_full"].startswith("A forklift moves")
    assert by["pie_cam-3/1.mp4"]["keep"] and "no YOLO sidecar" in by["pie_cam-3/1.mp4"]["reason"]
    assert by["pie_cam-3/0.mp4"]["camera_id"] == "pie_cam-3"

    llm = LLMClient(settings)
    report = build_report(result, llm)
    cov = report["coverage"]["by_camera_lighting"]
    assert cov["sdg_warehouse_cam-2"]["indoor"] == 2 and cov["pie_cam-3"]["day"] == 2
    assert any("i24_cam-1" in cell for cell in report["empty_cells"])
    assert report["gap_report"] == "Nothing from the highway view." and report["source"] == "llm"

    # an overloaded answer is not cached; real answers are
    assert len(list((settings.cache_dir / "verify").glob("*.json"))) == 6

    # loop: propose is read-only, execute re-ingests, logs, and mines again
    assert loop.needs_loop(result, 0)
    proposal = loop.propose(result, report, settings)
    assert len(proposal["chunks"]) == 2 and len(proposal["prompt"]) <= 800 and proposal["prompt_source"] == "llm"
    assert not mock_stack.STATE["reingest_calls"]
    updates = []
    result2, report2, it = loop.execute(result, proposal, settings, on_update=lambda i: updates.append(i["status"]))
    assert [c["chunk_count"] for c in mock_stack.STATE["reingest_calls"]] == [1, 1]
    assert all(c["custom_prompt"] == proposal["prompt"] for c in mock_stack.STATE["reingest_calls"])
    assert it["status"] == "completed" and it["after"]["confirmed"] == 5 > it["before"]["confirmed"]
    assert "re-ingesting" in updates and "searching again" in updates
    logged = json.loads((settings.repo_root / "reingest_log.jsonl").read_text().splitlines()[0])
    assert logged["prompt"] == proposal["prompt"] and logged["after"]["confirmed"] == 5

    # export (clip 4 of the re-ingested warehouse set is rejected: event not fully visible)
    z = zipfile.ZipFile(export_dataset(result2, report2, settings, [it]))
    names = z.namelist()
    manifest = json.loads(z.read("manifest.json"))
    assert len(manifest["clips"]) == 5 and sum(n.startswith("clips/clip_") for n in names) == 5
    assert {"manifest.json", "labels.csv", "README.md"} <= set(names)
    assert manifest["clips"][0]["labels"]["distance_class"] == "under_2m" and manifest["reingest_loop"]
    assert any("event not fully visible" in r["reason"] for r in result2.rejected)
    llm.close()


if __name__ == "__main__":
    test_end_to_end()
    print("ok")
