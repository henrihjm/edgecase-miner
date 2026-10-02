"""Offline end-to-end test against tests/mock_stack.py. Run: python -m pytest tests -q  (or python -m tests.test_e2e)"""
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

from edgecase import config, export, loop, verify, vss_client  # noqa: E402
from edgecase.llm import LLM  # noqa: E402
from edgecase.miner import mine  # noqa: E402
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


def test_end_to_end():
    tmp = Path(tempfile.mkdtemp())
    vss_client.CLIP_DIR = tmp / "clips"
    verify.VERIFY_DIR = tmp / "verify"
    export.EXPORT_DIR = tmp / "exports"
    loop.REINGEST_LOG = tmp / "reingest_log.jsonl"
    _serve()

    s = config.load()
    vss, llm, verifier = vss_client.VSS(s), LLM(s), verify.Verifier(s)
    run = mine("forklift passing close to a person", vss, llm, verifier)
    by = {c["source"].split("seg/")[1]: c for c in run["candidates"]}
    assert run["plan"]["query"] == "forklift close to person" and run["plan"]["source"] == "wandb"
    assert run["counts"] == {"searched": 8, "verified": 8, "confirmed": 4, "rejected": 2, "unverified": 2}, run["counts"]
    assert by["pie_cam-3/2.mp4"]["status"] == "rejected"          # Cosmos said NO
    assert by["pie_cam-3/3.mp4"]["status"] == "unverified"        # empty content -> stored caption
    assert by["pie_cam-3/3.mp4"]["reasoning"].startswith("A forklift moves")
    assert by["pie_cam-3/0.mp4"]["quality"]["relevant_detections"] == {"person": 2, "truck": 1}
    assert by["pie_cam-3/1.mp4"]["quality"]["detections"] == {}   # 404 sidecar lowers the score, still kept
    cov = run["report"]["coverage"]["by_camera_lighting"]
    assert cov["sdg_warehouse_cam-2"]["indoor"] == 2 and cov["pie_cam-3"]["day"] == 2
    assert any("i24_cam-1" in cell for cell in run["report"]["empty_cells"])
    assert run["report"]["gap_report"] == "Nothing from the highway view."

    # second pass hits the verification cache
    again = mine("forklift passing close to a person", vss, llm, verifier)
    assert all(c["cached"] for c in again["candidates"] if c["status"] in ("confirmed", "rejected"))

    # loop: propose is read-only, execute re-ingests, logs, and mines again
    assert loop.needs_loop(run)
    proposal = loop.propose(run, vss, llm)
    assert len(proposal["chunks"]) == 2 and len(proposal["prompt"]) <= 800
    assert not mock_stack.STATE["reingest_calls"]
    it = loop.execute(run, proposal, vss, llm, verifier)
    assert [c["chunk_count"] for c in mock_stack.STATE["reingest_calls"]] == [1, 1]
    assert it["status"] == "completed" and it["after"]["confirmed"] == 5 > it["before"]["confirmed"]
    logged = json.loads(loop.REINGEST_LOG.read_text().splitlines()[0])
    assert logged["prompt"] == proposal["prompt"] and logged["after"]["confirmed"] == 5

    # export (clip 4 of the re-ingested warehouse set is rejected: event not fully visible)
    z = zipfile.ZipFile(export.export(run))
    names = z.namelist()
    manifest = json.loads(z.read("manifest.json"))
    assert len(manifest["clips"]) == 5 and sum(n.startswith("clips/clip_") for n in names) == 5
    assert {"manifest.json", "labels.csv", "README.md"} <= set(names)
    assert manifest["clips"][0]["labels"]["distance_class"] == "under_2m" and manifest["reingest_loop"]
    assert any(c["reason"] == "event not fully visible" for c in run["candidates"])


if __name__ == "__main__":
    test_end_to_end()
    print("ok")
