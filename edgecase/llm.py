"""W&B Serverless Inference client (OpenAI-compatible)."""

from __future__ import annotations

import json
import re
from typing import Any

import httpx

from .config import Settings

# Cloudflare on api.inference.wandb.ai returns 1010 without a browser-like UA.
_DEFAULT_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


class LLMClient:
    def __init__(self, settings: Settings, timeout: float = 60.0) -> None:
        self.settings = settings
        self._client = httpx.Client(
            timeout=timeout,
            headers={
                "Authorization": f"Bearer {settings.wandb_api_key}",
                "Content-Type": "application/json",
                "User-Agent": _DEFAULT_UA,
                "OpenAI-Project": f"{settings.wandb_team}/{settings.wandb_project}",
            },
        )

    def close(self) -> None:
        self._client.close()

    def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str | None = None,
        temperature: float = 0.0,
        max_tokens: int = 512,
    ) -> str:
        if not self.settings.wandb_api_key:
            raise RuntimeError("WANDB_API_KEY not set")
        body = {
            "model": model or self.settings.wandb_model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        r = self._client.post(
            f"{self.settings.wandb_base_url}/chat/completions",
            json=body,
        )
        r.raise_for_status()
        data = r.json()
        return (data["choices"][0]["message"].get("content") or "").strip()

    def plan_search(self, request: str, camera_ids: list[str]) -> dict[str, Any]:
        """Turn a plain-English request into a search query + optional filters."""
        import json
        import re

        prompt = (
            "You plan a video archive search for a physical-AI training dataset.\n"
            f"User request: {request}\n"
            f"Available camera_ids: {', '.join(camera_ids)}\n"
            "Reply with ONLY JSON: "
            '{"query":"...","camera_ids":[] or subset,"notes":"..."}\n'
            "Use empty camera_ids to search all. Keep query short and visual."
        )
        raw = self.chat([{"role": "user", "content": prompt}], max_tokens=300)
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if not match:
            return {"query": request, "camera_ids": [], "notes": "fallback"}
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            return {"query": request, "camera_ids": [], "notes": "parse_fallback"}

    def _json_reply(self, prompt: str, max_tokens: int) -> dict[str, Any]:
        """Chat and parse the first JSON object. {} on any failure, so callers can fall back."""
        try:
            raw = self.chat([{"role": "user", "content": prompt}], max_tokens=max_tokens)
            match = re.search(r"\{.*\}", raw, re.DOTALL)
            out = json.loads(match.group(0)) if match else {}
            return out if isinstance(out, dict) else {}
        except Exception:
            return {}

    def gap_report(
        self, request: str, coverage: dict[str, Any], empty_cells: list[str]
    ) -> dict[str, Any]:
        """Short gap report + next-collection plan. Falls back to fixed rules if the LLM fails."""
        out = self._json_reply(
            "You advise a physical-AI data team on dataset coverage. Be concrete and brief.\n"
            f"Dataset request: {request}\n"
            f"Confirmed-clip coverage: {json.dumps(coverage)[:3000]}\n"
            f"Empty cells: {empty_cells[:30]}\n"
            'Reply with ONLY JSON: {"gap_report": "<3 to 5 sentences naming the gaps that matter '
            'for training on this request>", "collection_plan": ["<3 to 5 concrete next-collection lines>"]}',
            max_tokens=600,
        )
        plan = [str(p) for p in out.get("collection_plan") or [] if p]
        if out.get("gap_report") and plan:
            return {"gap_report": str(out["gap_report"]), "collection_plan": plan[:5], "source": "llm"}
        return {
            "gap_report": (
                "No confirmed clips for: " + "; ".join(empty_cells[:12]) + "."
                if empty_cells
                else "No empty cells among the camera groups and lighting conditions searched."
            ),
            "collection_plan": [f"Collect or re-index footage covering: {c}" for c in empty_cells[:5]],
            "source": "rules",
        }

    def ingest_prompt(
        self, request: str, near_miss_captions: list[str], coverage_summary: str
    ) -> dict[str, str]:
        """A superset ingestion prompt (general description first, then the fields we need), max 800 chars."""
        text = ""
        try:
            text = self.chat(
                [{"role": "user", "content": (
                    "You write prompts for a video captioning model whose captions are indexed for search.\n"
                    f"A dataset request found too few clips: {request}\n"
                    "Captions of near-miss clips (what the current prompt captured):\n- "
                    + "\n- ".join(c[:300] for c in near_miss_captions[:6])
                    + f"\nCurrent coverage: {coverage_summary}\n"
                    "Write a new captioning prompt UNDER 750 characters. It must be a superset: first ask "
                    "for a general description of the scene (setting, all actors, objects, motion) so general "
                    "search still works, then ask explicitly for the fields this request needs: each actor's "
                    "action, distances between actors (contact, under 2 m, 2 to 5 m, over 5 m), lighting, "
                    "weather and occlusion. Reply with the prompt text only."
                )}],
                max_tokens=400,
                temperature=0.2,
            ).strip().strip('"')
        except Exception:
            text = ""
        if text and len(text) <= 800:
            return {"prompt": text, "source": "llm"}
        return {
            "prompt": (
                "Describe this clip for search. First give a general description: the setting, every actor "
                "and vehicle, objects, and how each one moves. Then state explicitly: each actor's action; "
                "any interaction between actors and the distance between them (contact, under 2 m, 2 to 5 m, "
                "over 5 m); lighting (day, dusk, night, indoor); weather; and whether anything is occluded. "
                f"Pay particular attention to: {request[:200]}."
            )[:800],
            "source": "rules",
        }
