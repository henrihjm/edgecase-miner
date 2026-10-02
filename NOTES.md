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

### Timings to fill

- Search warehouse top5: ~2.5–3s
- Cosmos verify / clip: TBD Phase 1
- Re-ingest / chunk: TBD Phase 3
