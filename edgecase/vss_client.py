"""Client for the VSS retrieval backend. Request formats follow .cursor/skills/retrieval/*."""
from __future__ import annotations

import hashlib
import time
from pathlib import Path
from typing import Any, Callable

import httpx

from .config import CACHE_DIR, Settings

CLIP_DIR = CACHE_DIR / "clips"


def clip_path(source: str) -> Path:
    return CLIP_DIR / (hashlib.sha256(source.encode()).hexdigest() + ".mp4")


class VSS:
    def __init__(self, settings: Settings):
        self.s = settings
        self.http = httpx.Client(base_url=settings.api, timeout=120)
        self.token = ""

    def login(self) -> None:
        r = self.http.post("/auth/login", json={"username": self.s.username, "password": self.s.password})
        r.raise_for_status()
        self.token = r.json()["access_token"]

    def _req(self, method: str, path: str, **kw) -> httpx.Response:
        if not self.token:
            self.login()
        for attempt in (0, 1):
            r = self.http.request(method, path, headers={"Authorization": f"Bearer {self.token}"}, **kw)
            if r.status_code == 401 and attempt == 0:
                self.login()
                continue
            return r
        return r

    def _json(self, method: str, path: str, **kw) -> Any:
        r = self._req(method, path, **kw)
        r.raise_for_status()
        return r.json()

    # --- discovery ---
    def stats(self) -> dict:
        return self._json("GET", "/dashboard/stats", params={"scope": "all"})

    def schema(self) -> dict:
        return self._json("GET", "/metadata/schema")

    def values(self, field: str, limit: int = 100) -> list[str]:
        return self._json("GET", "/metadata/values", params={"field": field, "limit": limit}).get("values", [])

    def ingest_config(self) -> dict:
        return self._json("GET", "/metadata/ingest-config")

    def camera_ids(self) -> list[str]:
        return self.values("camera_id")

    # --- search and per-segment reads ---
    def search(self, query: str, top_k: int = 40, min_similarity: float = 0.3,
               metadata_filters: dict | None = None) -> dict:
        body = {
            "query": query,
            "top_k": max(1, min(top_k, 100)),
            "min_similarity": min_similarity,
            "llm_top_n": 1,
            "metadata_filters": metadata_filters or {},
            "include_public": True,
        }
        return self._json("POST", "/search", json=body)

    def metadata(self, source: str) -> dict:
        return self._json("GET", "/videos/metadata", params={"source": source})

    def detections(self, source: str) -> dict | None:
        """YOLO sidecar. 404 means no sidecar, which is a signal, not an error."""
        r = self._req("GET", "/videos/detections", params={"source": source})
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json()

    def download(self, source: str) -> Path:
        path = clip_path(source)
        if path.exists() and path.stat().st_size > 0:
            return path
        if not self.token:
            self.login()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".part")
        with self.http.stream("GET", "/videos/stream", params={"source": source, "token": self.token}) as r:
            r.raise_for_status()
            with open(tmp, "wb") as fh:
                for chunk in r.iter_bytes():
                    fh.write(chunk)
        tmp.replace(path)
        return path

    # --- re-ingest (shared infrastructure: callers must get Henri's yes first) ---
    def reingest(self, original_video: str, custom_prompt: str, chunk_count: int = 1) -> dict:
        if len(custom_prompt) > 800:
            raise ValueError("custom_prompt is over 800 characters")
        if chunk_count not in (1, 2):
            raise ValueError("chunk_count must be 1 or 2")
        body = {"original_video": original_video, "chunk_count": chunk_count, "custom_prompt": custom_prompt}
        return self._json("POST", "/dashboard/reingest", json=body)

    def reingest_status(self, job_id: str) -> dict:
        return self._json("GET", f"/dashboard/reingest/{job_id}")

    def wait_reingest(self, job_id: str, on_progress: Callable[[dict], None] | None = None,
                      timeout_s: int = 900) -> dict:
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            st = self.reingest_status(job_id)
            if on_progress:
                on_progress(st)
            status = str(st.get("status", "")).lower()
            if status == "completed":
                return st
            if status in ("failed", "error"):
                raise RuntimeError(f"re-ingest {job_id} ended with status {status}: {st.get('error', '')}")
            time.sleep(4)
        raise TimeoutError(f"re-ingest {job_id} did not complete within {timeout_s}s")
