"""Cosmos3-Reason yes/no verification + label extraction, with disk cache."""

from __future__ import annotations

import base64
import hashlib
import json
import re
from pathlib import Path
from typing import Any

import httpx

from .config import Settings

VERIFY_PROMPT = """You are verifying a video clip for a training dataset.
Question: Does this clip show {event}?
Answer in this format:
<think>your reasoning</think>
<answer>YES or NO</answer>
<labels>{{"actors": [...], "action": "...", "distance_class": "contact|under_2m|2_to_5m|over_5m|none", "lighting": "day|dusk|night|indoor", "weather": "clear|rain|snow|fog|unknown", "occlusion": "none|partial|heavy", "event_fully_visible": true|false}}</labels>
"""


class CosmosVerifier:
    def __init__(self, settings: Settings, timeout: float = 60.0) -> None:
        self.settings = settings
        self.timeout = timeout
        self.cache_dir = settings.cache_dir / "verify"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._client = httpx.Client(
            timeout=timeout,
            headers={"Authorization": f"Bearer {settings.gpu_bearer_token}"},
        )
        self._model: str | None = None

    def close(self) -> None:
        self._client.close()

    def model_id(self) -> str:
        if not self._model:
            r = self._client.get(f"{self.settings.cosmos3_reason_url}/v1/models")
            r.raise_for_status()
            self._model = r.json()["data"][0]["id"]
        return self._model

    def _cache_key(self, source: str, question: str) -> Path:
        h = hashlib.sha256(f"{source}\n{question}".encode()).hexdigest()[:32]
        return self.cache_dir / f"{h}.json"

    def verify_clip(
        self,
        *,
        source: str,
        event: str,
        clip_path: Path,
        fallback_reasoning: str = "",
        use_cache: bool = True,
    ) -> dict[str, Any]:
        question = f"Does this clip show {event}?"
        cache_path = self._cache_key(source, question)
        if use_cache and cache_path.exists():
            return json.loads(cache_path.read_text())

        b64 = base64.b64encode(clip_path.read_bytes()).decode("ascii")
        prompt = VERIFY_PROMPT.format(event=event)
        body = {
            "model": self.model_id(),
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "video_url",
                            "video_url": {
                                "url": f"data:video/mp4;base64,{b64}"
                            },
                        },
                    ],
                }
            ],
            "temperature": 0,
            "max_tokens": 400,
        }

        content = ""
        for attempt in range(2):
            try:
                r = self._client.post(
                    f"{self.settings.cosmos3_reason_url}/v1/chat/completions",
                    json=body,
                )
                r.raise_for_status()
                content = (
                    r.json()["choices"][0]["message"].get("content") or ""
                ).strip()
                if content:
                    break
            except Exception:
                if attempt == 1:
                    raise

        if not content:
            result = {
                "source": source,
                "question": question,
                "verdict": "unverified",
                "think": fallback_reasoning,
                "labels": {},
                "raw": "",
                "fallback": True,
            }
        else:
            result = self._parse(content, source=source, question=question)

        cache_path.write_text(json.dumps(result, indent=2))
        return result

    def _parse(self, content: str, *, source: str, question: str) -> dict[str, Any]:
        think_m = re.search(r"<think>(.*?)</think>", content, re.DOTALL | re.I)
        answer_m = re.search(r"<answer>(.*?)</answer>", content, re.DOTALL | re.I)
        labels_m = re.search(r"<labels>(.*?)</labels>", content, re.DOTALL | re.I)

        verdict = "unverified"
        if answer_m:
            ans = answer_m.group(1).strip().upper()
            if "YES" in ans:
                verdict = "YES"
            elif "NO" in ans:
                verdict = "NO"

        labels: dict[str, Any] = {}
        if labels_m:
            raw_labels = labels_m.group(1).strip()
            try:
                labels = json.loads(raw_labels)
            except json.JSONDecodeError:
                labels = {"parse_error": raw_labels[:200]}

        return {
            "source": source,
            "question": question,
            "verdict": verdict,
            "think": (think_m.group(1).strip() if think_m else content[:400]),
            "labels": labels,
            "raw": content,
            "fallback": False,
        }
