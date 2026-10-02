"""W&B Serverless Inference client (OpenAI-compatible)."""

from __future__ import annotations

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
