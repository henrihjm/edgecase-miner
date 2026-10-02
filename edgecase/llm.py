"""Reasoning LLM on W&B serverless inference (OpenAI-compatible), with Cosmos and rule fallbacks.

Every function returns something usable even when no LLM answers, so a flaky endpoint never
stops a run. The `source` field on results says which path produced them.
"""
from __future__ import annotations

import json
import re

import httpx

from .config import Settings

# Tried in order if EDGECASE_LLM_MODEL is unset; otherwise the first id from /models is used.
PREFERRED_MODELS = ("openai/gpt-oss-120b", "meta-llama/Llama-3.3-70B-Instruct", "deepseek-ai/DeepSeek-V3.1")
MAX_INGEST_PROMPT = 800


class LLM:
    def __init__(self, settings: Settings):
        self.s = settings
        self.http = httpx.Client(timeout=60)
        self._model = settings.llm_model
        self._cosmos_model = settings.cosmos_model
        self.last_source = "none"

    # --- transport ---
    def _wandb_headers(self) -> dict:
        h = {"Authorization": f"Bearer {self.s.wandb_api_key}"}
        if self.s.wandb_project:
            h["OpenAI-Project"] = self.s.wandb_project
        return h

    def model(self) -> str:
        if not self._model:
            r = self.http.get(self.s.wandb_base_url + "/models", headers=self._wandb_headers())
            r.raise_for_status()
            ids = [m["id"] for m in r.json().get("data", [])]
            self._model = next((m for m in PREFERRED_MODELS if m in ids), ids[0] if ids else "")
        return self._model

    def cosmos_model(self) -> str:
        if not self._cosmos_model:
            r = self.http.get(self.s.cosmos_url + "/v1/models", headers=self._gpu_headers())
            r.raise_for_status()
            self._cosmos_model = r.json()["data"][0]["id"]
        return self._cosmos_model

    def _gpu_headers(self) -> dict:
        return {"Authorization": f"Bearer {self.s.gpu_token}"} if self.s.gpu_token else {}

    def chat(self, system: str, user: str, max_tokens: int = 700) -> str:
        """Returns text, or "" if neither W&B nor Cosmos answered."""
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        if self.s.wandb_api_key:
            try:
                r = self.http.post(
                    self.s.wandb_base_url + "/chat/completions", headers=self._wandb_headers(),
                    json={"model": self.model(), "messages": messages, "temperature": 0.2, "max_tokens": max_tokens},
                )
                r.raise_for_status()
                text = (r.json()["choices"][0]["message"].get("content") or "").strip()
                if text:
                    self.last_source = "wandb"
                    return text
            except (httpx.HTTPError, KeyError, IndexError, ValueError):
                pass
        try:
            r = self.http.post(
                self.s.cosmos_url + "/v1/chat/completions", headers=self._gpu_headers(),
                json={"model": self.cosmos_model(), "messages": messages, "temperature": 0.2, "max_tokens": max_tokens},
            )
            r.raise_for_status()
            text = (r.json()["choices"][0]["message"].get("content") or "").strip()
            if text:
                self.last_source = "cosmos"
                return strip_think(text)
        except (httpx.HTTPError, KeyError, IndexError, ValueError):
            pass
        self.last_source = "rules"
        return ""

    # --- tasks ---
    def plan(self, request: str, camera_ids: list[str]) -> dict:
        """Turn a request into {query, event, camera_ids}. camera_ids empty means all groups."""
        text = self.chat(
            "You plan searches over an archive of indexed video clips. Reply with JSON only.",
            f"Request: {request}\nAvailable camera_id values: {camera_ids}\n"
            'Return {"query": "<short caption-style search phrase>", '
            '"event": "<the event as a noun phrase that completes: Does this clip show ...>", '
            '"camera_ids": [<subset of the available values, only if the request names a place or camera; else []>]}',
            max_tokens=300,
        )
        plan = first_json(text) or {}
        query = str(plan.get("query") or request).strip()
        event = str(plan.get("event") or request).strip().rstrip("?.")
        cams = [c for c in plan.get("camera_ids") or [] if c in camera_ids]
        return {"query": query, "event": event, "camera_ids": cams, "source": self.last_source}

    def ingest_prompt(self, request: str, near_miss_captions: list[str], coverage_summary: str) -> str:
        """A superset ingestion prompt: general scene description first, then the fields we need."""
        text = self.chat(
            "You write prompts for a video captioning model whose captions are indexed for search. "
            "Reply with the prompt text only.",
            f"A dataset request found too few clips: {request}\n"
            f"Captions of near-miss clips (what the current prompt captured):\n- "
            + "\n- ".join(c[:300] for c in near_miss_captions[:6])
            + f"\nCurrent coverage: {coverage_summary}\n"
            "Write a new captioning prompt UNDER 750 characters. It must be a superset: first ask for a "
            "general description of the scene (setting, all actors, objects, motion) so general search "
            "still works, then ask explicitly for the fields this request needs: each actor's action, "
            "distances between actors (contact, under 2 m, 2 to 5 m, over 5 m), lighting, weather and occlusion.",
            max_tokens=400,
        )
        text = text.strip().strip('"')
        if not text or len(text) > MAX_INGEST_PROMPT:
            self.last_source = "rules"
            text = (
                "Describe this clip for search. First give a general description: the setting, every actor and "
                "vehicle, objects, and how each one moves. Then state explicitly: each actor's action; any "
                "interaction between actors and the distance between them (contact, under 2 m, 2 to 5 m, over "
                "5 m); lighting (day, dusk, night, indoor); weather; and whether anything is occluded. "
                f"Pay particular attention to: {request[:200]}."
            )
        return text[:MAX_INGEST_PROMPT]

    def gap_report(self, request: str, coverage: dict, empty_cells: list[str]) -> dict:
        text = self.chat(
            "You advise a physical-AI data team on dataset coverage. Be concrete and brief. Reply with JSON only.",
            f"Dataset request: {request}\nConfirmed-clip coverage: {json.dumps(coverage)[:3000]}\n"
            f"Empty cells: {empty_cells}\n"
            'Return {"gap_report": "<3 to 5 sentences naming the gaps that matter for training on this request>", '
            '"collection_plan": ["<3 to 5 concrete next-collection lines>"]}',
            max_tokens=600,
        )
        out = first_json(text) or {}
        plan = [str(p) for p in out.get("collection_plan") or [] if p]
        if not out.get("gap_report") or not plan:
            self.last_source = "rules"
            return {
                "gap_report": ("No confirmed clips for: " + "; ".join(empty_cells) + ".") if empty_cells
                else "No empty cells among the camera groups and lighting conditions searched.",
                "collection_plan": [f"Collect or re-index footage covering: {c}" for c in empty_cells[:5]],
                "source": "rules",
            }
        return {"gap_report": str(out["gap_report"]), "collection_plan": plan[:5], "source": self.last_source}


def strip_think(text: str) -> str:
    return re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()


def first_json(text: str) -> dict | None:
    """First JSON object in a model reply, tolerating code fences and surrounding prose."""
    text = strip_think(text or "")
    start = text.find("{")
    while start != -1:
        depth = 0
        for i in range(start, len(text)):
            depth += text[i] == "{"
            depth -= text[i] == "}"
            if depth == 0:
                try:
                    obj = json.loads(text[start:i + 1])
                    if isinstance(obj, dict):
                        return obj
                except ValueError:
                    pass
                break
        start = text.find("{", start + 1)
    return None
