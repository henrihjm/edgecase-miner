"""Orchestration: request -> search -> verify -> score -> results."""

from __future__ import annotations

import hashlib
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .config import Settings, load_settings
from .llm import LLMClient
from .quality import score_quality
from .verify import CosmosVerifier
from .vss_client import VSSClient

# Demo group aliases → camera_id values in the live index
GROUP_CAMERAS: dict[str, list[str]] = {
    "warehouse": ["sdg_warehouse_cam-2"],
    "pie": ["pie_cam-3"],
    "driving": ["pie_cam-3"],
    "toronto": ["pie_cam-3"],
    "neighborhood": ["neighborhood_cam-1"],
    "i24": ["i24_cam-1"],
    "highway": ["i24_cam-1"],
    "sf": [
        "sf_streets_cam-1",
        "sf_streets_cam-2",
        "sf_streets_cam-3",
        "sf_streets_cam-4",
    ],
    "streets": [
        "sf_streets_cam-1",
        "sf_streets_cam-2",
        "sf_streets_cam-3",
        "sf_streets_cam-4",
        "neighborhood_cam-1",
        "pie_cam-3",
    ],
    "smartspace": ["smartspace_cam-1"],
    "indoor": ["smartspace_cam-1"],
}


@dataclass
class MineResult:
    request: str
    query: str
    camera_ids: list[str]
    all_cameras: list[str] = field(default_factory=list)
    groups: list[str] | None = None
    searched: int = 0
    candidates: list[dict[str, Any]] = field(default_factory=list)
    confirmed: list[dict[str, Any]] = field(default_factory=list)
    rejected: list[dict[str, Any]] = field(default_factory=list)
    timings: dict[str, float] = field(default_factory=dict)


def resolve_cameras(groups: list[str] | None, all_cameras: list[str]) -> list[str]:
    if not groups:
        return []
    resolved: list[str] = []
    for g in groups:
        key = g.strip().lower()
        if key in ("all", "*"):
            return []
        if key in GROUP_CAMERAS:
            resolved.extend(GROUP_CAMERAS[key])
        elif key in all_cameras:
            resolved.append(key)
        else:
            # allow partial match e.g. sdg_warehouse
            hits = [c for c in all_cameras if key in c.lower()]
            resolved.extend(hits)
    # dedupe preserve order
    seen: set[str] = set()
    out: list[str] = []
    for c in resolved:
        if c not in seen:
            seen.add(c)
            out.append(c)
    return out


def _clip_path(settings: Settings, source: str) -> Path:
    h = hashlib.sha256(source.encode()).hexdigest()[:24]
    return settings.cache_dir / "clips" / f"{h}.mp4"


def _search_merged(
    vss: VSSClient,
    query: str,
    *,
    top_k: int,
    min_similarity: float,
    camera_ids: list[str],
) -> list[dict[str, Any]]:
    """Search once per camera (API takes a single camera_id) or once for all."""
    if not camera_ids:
        data = vss.search(
            query, top_k=top_k, min_similarity=min_similarity
        )
        return list(data.get("results") or [])

    if len(camera_ids) == 1:
        data = vss.search(
            query,
            top_k=top_k,
            min_similarity=min_similarity,
            metadata_filters={"camera_id": camera_ids[0]},
        )
        return [
            {**row, "_camera": camera_ids[0]} for row in data.get("results") or []
        ]

    # Multiple cameras: fan out and merge by similarity
    per = max(5, top_k // len(camera_ids) + 5)
    by_source: dict[str, dict[str, Any]] = {}
    for cam in camera_ids:
        data = vss.search(
            query,
            top_k=min(per, 100),
            min_similarity=min_similarity,
            metadata_filters={"camera_id": cam},
        )
        for row in data.get("results") or []:
            src = row.get("source") or ""
            prev = by_source.get(src)
            if prev is None or float(row.get("similarity_score") or 0) > float(
                prev.get("similarity_score") or 0
            ):
                by_source[src] = {**row, "_camera": cam}
    merged = sorted(
        by_source.values(),
        key=lambda r: float(r.get("similarity_score") or 0),
        reverse=True,
    )
    return merged[:top_k]


def _process_one(
    *,
    settings: Settings,
    vss: VSSClient,
    verifier: CosmosVerifier,
    request: str,
    row: dict[str, Any],
) -> dict[str, Any]:
    source = row.get("source") or ""
    similarity = float(row.get("similarity_score") or 0)
    reasoning = (row.get("reasoning_content") or "").strip()
    clip = _clip_path(settings, source)

    t_dl = time.time()
    if not clip.exists() or clip.stat().st_size < 1000:
        vss.download_stream(source, clip)
    dl_s = time.time() - t_dl

    t_v = time.time()
    try:
        verification = verifier.verify_clip(
            source=source,
            event=request,
            clip_path=clip,
            fallback_reasoning=reasoning,
        )
    except Exception as e:
        verification = {
            "source": source,
            "verdict": "unverified",
            "think": f"verify error: {type(e).__name__}: {e}",
            "labels": {},
            "fallback": True,
        }
    verify_s = time.time() - t_v

    detections = None
    try:
        detections = vss.video_detections(source)
    except Exception:
        detections = None

    quality = score_quality(
        detections=detections,
        labels=verification.get("labels") or {},
        clip_path=clip,
    )

    verdict = verification.get("verdict", "unverified")
    keep = (
        verdict == "YES"
        and quality.get("keep", False)
        and not verification.get("fallback")
    )
    # Unverified YES-ish fallbacks never confirm; unverified alone rejects
    if verdict == "YES" and verification.get("fallback"):
        keep = False
        quality = {**quality, "keep": False, "reason": "unverified fallback"}
    elif verdict != "YES":
        keep = False
        if verdict == "NO":
            quality = {
                **quality,
                "keep": False,
                "reason": f"cosmos NO; {quality.get('reason')}",
            }
        else:
            quality = {
                **quality,
                "keep": False,
                "reason": f"cosmos {verdict}; {quality.get('reason')}",
            }

    think = (verification.get("think") or "").replace("\n", " ").strip()
    think_full = think
    if len(think) > 120:
        think = think[:117] + "..."

    return {
        "source": source,
        "filename": (row.get("filename") or source.rsplit("/", 1)[-1]),
        "original_video": row.get("original_video"),
        "camera_id": row.get("camera_id") or row.get("_camera") or "unknown",
        "think_full": think_full,
        "start_time": row.get("start_time") or row.get("t_start"),
        "end_time": row.get("end_time") or row.get("t_end"),
        "similarity": similarity,
        "reasoning_content": reasoning,
        "verdict": verdict,
        "labels": verification.get("labels") or {},
        "quality": quality.get("quality"),
        "keep": keep,
        "reason": quality.get("reason"),
        "think": think,
        "blur": quality.get("blur"),
        "detections": quality.get("detections"),
        "clip_path": str(clip),
        "fallback": bool(verification.get("fallback")),
        "timings": {"download_s": round(dl_s, 2), "verify_s": round(verify_s, 2)},
    }


def mine(
    request: str,
    *,
    groups: list[str] | None = None,
    top_k: int = 40,
    verify_n: int = 20,
    min_similarity: float = 0.3,
    settings: Settings | None = None,
    skip_llm_plan: bool = False,
    on_progress: Callable[[str, int, list[dict[str, Any]]], None] | None = None,
) -> MineResult:
    """on_progress(stage, searched_count, processed_so_far) lets a UI show live counts."""
    settings = settings or load_settings()
    verify_n = max(0, min(20, verify_n))  # hard bound from brief

    with VSSClient(settings) as vss:
        vss.login()
        all_cams = list(vss.metadata_values("camera_id").get("values") or [])
        camera_ids = resolve_cameras(groups, all_cams)

        query = request
        if not skip_llm_plan:
            llm = LLMClient(settings)
            try:
                plan = llm.plan_search(request, all_cams)
                query = (plan.get("query") or request).strip() or request
                planned = plan.get("camera_ids") or []
                if not camera_ids and isinstance(planned, list) and planned:
                    camera_ids = [c for c in planned if c in all_cams]
            except Exception:
                query = request
            finally:
                llm.close()

        t_search = time.time()
        results = _search_merged(
            vss,
            query,
            top_k=top_k,
            min_similarity=min_similarity,
            camera_ids=camera_ids,
        )
        search_s = time.time() - t_search

        candidates = results[:verify_n]
        if on_progress:
            on_progress("verifying", len(results), [])
        processed: list[dict[str, Any]] = []
        verify_s_total = 0.0

        if candidates:
            verifier = CosmosVerifier(settings, timeout=60.0)
            try:
                t_v = time.time()
                with ThreadPoolExecutor(max_workers=2) as pool:
                    futs = [
                        pool.submit(
                            _process_one,
                            settings=settings,
                            vss=vss,
                            verifier=verifier,
                            request=request,
                            row=row,
                        )
                        for row in candidates
                    ]
                    for fut in as_completed(futs):
                        processed.append(fut.result())
                        if on_progress:
                            on_progress("verifying", len(results), list(processed))
                verify_s_total = time.time() - t_v
            finally:
                verifier.close()

        # stable sort: confirmed first, then similarity
        processed.sort(
            key=lambda r: (not r.get("keep"), -float(r.get("similarity") or 0))
        )
        confirmed = [r for r in processed if r.get("keep")]
        rejected = [r for r in processed if not r.get("keep")]

        return MineResult(
            request=request,
            query=query,
            camera_ids=camera_ids,
            all_cameras=all_cams,
            groups=groups,
            searched=len(results),
            candidates=processed,
            confirmed=confirmed,
            rejected=rejected,
            timings={
                "search_s": round(search_s, 2),
                "verify_wall_s": round(verify_s_total, 2),
                "verify_per_clip_s": round(
                    verify_s_total / max(len(candidates), 1), 2
                ),
            },
        )
