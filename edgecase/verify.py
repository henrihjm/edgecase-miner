"""Cosmos3-Reason yes/no verification and label extraction on a clip, cached on disk.

Bounds (hard rules from the brief): 60 second timeout, one retry, callers cap the number of calls
and run at most two at a time. Empty content means the endpoint is overloaded: fall back to the
stored caption and mark the verdict "unverified". Unverified answers are never cached.
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
import time
from pathlib import Path

import httpx

from .config import CACHE_DIR, Settings
from .llm import first_json

VERIFY_DIR = CACHE_DIR / "verify"
TIMEOUT_S = 60
MAX_CALLS = 20
CONCURRENCY = 2

PROMPT = """You are verifying a video clip for a training dataset.
Question: Does this clip show {event}?
Answer in this format:
<think>your reasoning</think>
<answer>YES or NO</answer>
<labels>{{"actors": [...], "action": "...", "distance_class": "contact|under_2m|2_to_5m|over_5m|none",
"lighting": "day|dusk|night|indoor", "weather": "clear|rain|snow|fog|unknown",
"occlusion": "none|partial|heavy", "event_fully_visible": true|false}}</labels>"""


def _cache_file(source: str, question: str) -> Path:
    key = hashlib.sha256(f"{source}\n{question}".encode()).hexdigest()
    return VERIFY_DIR / f"{key}.json"


def parse(content: str) -> dict:
    """Parse the think / answer / labels blocks. verdict is "yes", "no" or "unverified"."""
    think = re.search(r"<think>(.*?)</think>", content, flags=re.S)
    answer = re.search(r"<answer>\s*(YES|NO)\b", content, flags=re.I)
    labels_block = re.search(r"<labels>(.*?)(</labels>|$)", content, flags=re.S)
    if not answer:  # tolerate a bare YES/NO outside the tags
        rest = re.sub(r"<think>.*?</think>|<labels>.*", "", content, flags=re.S)
        answer = re.search(r"\b(YES|NO)\b", rest)
    labels = first_json(labels_block.group(1)) if labels_block else None
    return {
        "verdict": answer.group(1).lower() if answer else "unverified",
        "reasoning": (think.group(1) if think else re.sub(r"<[^>]+>.*", "", content, flags=re.S)).strip(),
        "labels": labels or {},
    }


class Verifier:
    def __init__(self, settings: Settings):
        self.s = settings
        self.http = httpx.Client(timeout=TIMEOUT_S)
        self._model = settings.cosmos_model

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.s.gpu_token}"} if self.s.gpu_token else {}

    def model(self) -> str:
        if not self._model:
            r = self.http.get(self.s.cosmos_url + "/v1/models", headers=self._headers())
            r.raise_for_status()
            self._model = r.json()["data"][0]["id"]
        return self._model

    def _ask(self, clip: Path, question: str) -> str:
        video = base64.b64encode(clip.read_bytes()).decode()
        body = {
            "model": self.model(),
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": question},
                {"type": "video_url", "video_url": {"url": f"data:video/mp4;base64,{video}"}},
            ]}],
            "temperature": 0,
            "max_tokens": 400,
        }
        r = self.http.post(self.s.cosmos_url + "/v1/chat/completions", headers=self._headers(), json=body)
        r.raise_for_status()
        return (r.json()["choices"][0]["message"].get("content") or "").strip()

    def verify(self, source: str, clip: Path, event: str, stored_caption: str = "") -> dict:
        question = PROMPT.format(event=event)
        cache = _cache_file(source, question)
        if cache.exists():
            return {**json.loads(cache.read_text()), "cached": True}

        started, content, error = time.time(), "", ""
        for _ in range(2):  # one retry
            try:
                content = self._ask(clip, question)
                if content:
                    break
                error = "empty content (endpoint overloaded)"
            except (httpx.HTTPError, KeyError, IndexError, ValueError) as exc:
                error = f"{type(exc).__name__}"
        result = parse(content) if content else {"verdict": "unverified", "reasoning": "", "labels": {}}
        result.update(seconds=round(time.time() - started, 1), cached=False, error="" if content else error)
        if result["verdict"] == "unverified":
            result["reasoning"] = result["reasoning"] or stored_caption
            result["error"] = result["error"] or "no YES/NO in the answer"
            return result
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(result))
        return result
