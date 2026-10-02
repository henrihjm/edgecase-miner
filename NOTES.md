# Notes

## State on 2026-10-02, about 1:30 PM PDT

The package was written away from the hackathon VM, so it has NOT yet run against the live stack.
It passes an end-to-end test against a mock built from the skill docs (`tests/mock_stack.py`).

Run first on the VM, in this order:

1. `python -m edgecase health --captions`
2. `python -m edgecase mine "forklift passing close to a person" --groups warehouse --verify 5`

Things the docs do not pin down, so check them on the first live run:

- Field names in `/search` results beyond `source`, `similarity_score`, `reasoning_content`
  (`camera_id`, `original_video`, segment timing). `miner.py` reads them defensively.
- The YOLO sidecar shape from `/videos/detections`. `quality.detection_counts` handles
  `object_counts`, `object_classes` and per-box class fields.
- W&B inference base URL, model ids and the `OpenAI-Project` header (`config.py`, `llm.py`).
  If W&B does not answer, the tool falls back to Cosmos for text, then to fixed rules.
- Whether Cosmos3-Reason accepts a whole segment as base64 within the 60 second timeout.

## Measured timings

Not measured yet. Each run prints `Timings:` (search, verification total and per clip); a loop
iteration prints its duration. Copy the numbers here; they go into the demo.

- Search latency:
- Cosmos verification per clip:
- Re-ingest per chunk:
- One full loop iteration:
