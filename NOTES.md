# Edge-Case Miner — NOTES

Compressed schedule (hack day clock ~2:00 PM start for this agent): Phase 1 by 2:45, Phase 2 by 3:15, Phase 3 by 3:45, submission by 4:00.

## Phase 0 findings (2026-10-02)

### Index (team-30)

| camera_id | segment_rows |
|-----------|-------------:|
| pie_cam-3 | 1080 |
| neighborhood_cam-1 | 307 |
| i24_cam-1 | 180 |
| sf_streets_cam-2 | 180 |
| sf_streets_cam-4 | 180 |
| smartspace_cam-1 | 180 |
| sf_streets_cam-1 | 148 |
| sdg_warehouse_cam-2 | **60** (docs said ~178) |
| sf_streets_cam-3 | 37 |

Totals: **2352** clips, **414** videos, 100% reasoning/perception/object_classes OK. `re_ingest_rows: 0`.

Locations: toronto 1080, san_francisco 545, neighborhood 307, nashville 180, indoor 180, warehouse3 60.

### Health

- Login OK
- Cosmos3-Reason: models/ready/live 200; model id `nvidia/cosmos3-nano-reasoner`; text "OK" works
- YOLO `/healthz`: ok + model_loaded
- Embed1: models/ready/live 200; `nvidia/cosmos-embed1`
- W&B inference: works at `https://api.inference.wandb.ai/v1` with model `meta-llama/Llama-3.1-8B-Instruct`
  - **Gotcha:** bare urllib gets Cloudflare **1010**; need a browser-like `User-Agent`

### Warehouse caption probe (`sdg_warehouse_cam-2`, top_k=5, min_sim=0.3)

Search latency ~2.4–3.1s.

| Query | Hits (returned) | Top score | Caption signal |
|-------|----------------:|----------:|----------------|
| forklift near a person | 5 | 0.51 | Strong — person + forklift + walk/stationary + near/beside |
| person in a walkway | 1 | 0.15 | Weak — legs/feet close-up; no "walkway" language |
| pallet blocking an aisle | 2 | 0.21 | Weak — forklift in aisle, **no pallet** mentioned |

**Captions DO contain:** actors (person/worker), forklift identity/color, coarse actions (walks, stationary, approaches, running), informal distance (near, away, beside, toward), indoor/tint/bright lighting, occasional PPE note, aisle mention sometimes.

**Captions DO NOT contain:** structured distance classes (`under_2m` etc.), weather, occlusion labels, pallets/inventory blocking, metric distances, consistent walkway/zone taxonomy, event_fully_visible flags.

Implication for re-ingest later: use a **superset** warehouse prompt that still describes the general scene, then asks for distance class, actors, actions, lighting, occlusion, aisle/walkway clear vs blocked, pallet presence.

### First Phase 1 test request (proposed)

`forklift near a person` on `--groups warehouse` (camera `sdg_warehouse_cam-2`), `--top-k 40 --verify 20`.

High search signal already; short clips; good for end-to-end verify/quality before trying `pie_cam-3` pedestrian.

### API gotchas

- `llm_top_n` must be **>= 1** if sent (0 → 422)
- Do not re-ingest without Henri's yes

### Timings

- Search warehouse top5: ~2.5–3s; Phase 1 search top40 warehouse: ~7–11s
- Cosmos verify / clip: **~2.5s** wall amortized at concurrency 2 (warehouse short clips); full 20-clip pass ~50s verify wall / ~58s total
- Re-ingest / chunk: ~31 s (2 chunks in 62 s); whole loop iteration 78 s

### Phase 1 warehouse run (`forklift near a person`, verify=20)

- confirmed **19** / rejected **1** (1× cosmos unverified empty/partial)
- Labels look good: actors, action, `distance_class=under_2m`, lighting=indoor, event_fully_visible
- Quality scores 0.75–1.00; one kept with blur caveat (laplacian≈50)

### Phase 1 pie_cam-3 run (`pedestrian near a vehicle`, verify=8)

- confirmed **6** / rejected **2** (both cosmos **NO** — good demo of real verification)
- Search similarities lower (0.14–0.20) than warehouse; Cosmos still decisive
- verify_wall ~24s for 8 clips (~3s/clip amortized)

## Phases 2 to 5 (written off the VM, 2026-10-02 afternoon)

Report, export, re-ingest loop, web UI, README, demo script and submission draft were added on top
of the live-tested Phase 0 and 1 code. They pass an end-to-end test against a mock built from the
skill docs (`python -m pytest tests -q`) and were then run on the VM (results below).

Check on the first live run:

- `python -m edgecase mine "forklift near a person" --groups warehouse --export` (coverage table,
  gap report, zip in `exports/`)
- `python -m edgecase serve`, then one request in the browser: live counters, inline clip playback,
  rejected strip, coverage table, Export button
- `camera_id` on search rows: coverage rows need it. It is taken from the row, else from the camera
  filter used; with an unfiltered search and no `camera_id` on the row it shows as "unknown"
- The loop: `--loop` in the CLI or "Propose a better ingestion prompt" in the UI. Both ask before
  re-ingesting. Measure one iteration and fill the timing above

Changes to Phase 0 and 1 code: `verify.py` no longer caches an "unverified" fallback (so an
overloaded endpoint is asked again); `miner.py` rows carry `camera_id` and the full Cosmos
reasoning, and `mine()` takes an optional `on_progress`; `config.py` accepts an already exported
environment when `/config` is absent.

### Live run on the VM (2026-10-02, about 2:00 PM PDT)

- CLI `mine "forklift near a person" --groups warehouse --verify 20 --export`: 19 confirmed, coverage
  table, LLM gap report and plan, zip written to `exports/`. `labels.csv` has real labels.
- Web UI, `pedestrian near a vehicle` on `pie_cam-3`: 26 searched, 20 verified, 16 confirmed,
  4 rejected (real Cosmos NOs), about 50 s. Clips render and play inline (5 s each), labels and
  reasoning shown, rejected strip, coverage table, gap report all correct.
- Loop proposal, `pallet blocking an aisle` on warehouse (verify 10): 0 confirmed, the agent proposed
  a prompt and two chunks. The re-ingest itself was NOT run: it needs Henri's yes.
- `unzip` is not installed on the VM; use `python -m zipfile -l exports/<name>.zip` to list a zip.

### First live re-ingest (2026-10-02, 2:02 PM PDT, approved by Henri)

Request `pallet blocking an aisle`, warehouse group, verify 10. Two chunks re-ingested with the
superset prompt (`reingest_log.jsonl`).

- Re-ingest of 2 chunks (2 clips each): **62 s**. Whole iteration incl. search + verify: **78 s**.
  Fast enough to run live in the demo.
- Before: 2 candidates, 0 confirmed. After: 3 candidates, 0 confirmed, 1 unverified. The archive
  most likely has no pallet actually blocking an aisle; the gap report is the honest answer here.
- The first proposed prompt copied one clip's specifics ("a black forklift named ATLAS"); fixed so
  the general description is always prepended and the model writes only the specific part.

For the demo, the loop story works best on a request with near-misses, e.g. `person in a walkway`
(weak captions, real clips exist). Second iteration was declined.

Still open: README screenshots.

## Voice assistant (2026-10-02, 2:30 PM PDT)

`edgecase/static/assistant.js`, served into the dashboard by `voice.py` (which serves `/`). It drives
the same API as the page: greets, asks for the request, asks which camera groups, confirms, runs,
narrates one example clip, then the rest, the gaps, the prompt improvement, asks before re-ingesting
and before exporting. Nothing moves on screen on its own: it fills the request box, ticks the camera
boxes, scrolls to and outlines the section it is talking about. The user can type or click at any time.

- Speech in: WAV from the mic to `/api/transcribe` (Canary-1B on the stack); falls back to the typed
  box. Speech out: browser speech synthesis; captions always.
- The VM's browser session has NO microphone and NO sound card (ALSA "cannot find card"), so on the
  VM it runs silently with typed answers. For the real voice demo run the app on a laptop browser.
- Page contract for the redesign: listen for `window` event `edgecase:run` (`detail.id`) and poll that
  run; optional ids the assistant uses if present: `request`, `groups`, `confirmed`, `rejected`,
  `coverage`, `loop-note`, `prompt`, `proposal`.
