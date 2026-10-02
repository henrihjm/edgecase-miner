"""VSS backend client: login, search, metadata, detections, stream, reingest."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import httpx

from .config import Settings


class VSSClient:
    def __init__(self, settings: Settings, timeout: float = 120.0) -> None:
        self.settings = settings
        self.timeout = timeout
        self._token: str | None = None
        self._client = httpx.Client(timeout=timeout)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "VSSClient":
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    @property
    def token(self) -> str:
        if not self._token:
            self.login()
        assert self._token
        return self._token

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}

    def login(self) -> str:
        url = f"{self.settings.api_base}/auth/login"
        r = self._client.post(
            url,
            json={
                "username": self.settings.username,
                "password": self.settings.password,
            },
        )
        r.raise_for_status()
        self._token = r.json()["access_token"]
        return self._token

    def dashboard_stats(self, scope: str = "all") -> dict[str, Any]:
        r = self._client.get(
            f"{self.settings.api_base}/dashboard/stats",
            params={"scope": scope},
            headers=self._headers(),
        )
        r.raise_for_status()
        return r.json()

    def metadata_schema(self) -> dict[str, Any]:
        r = self._client.get(
            f"{self.settings.api_base}/metadata/schema",
            headers=self._headers(),
        )
        r.raise_for_status()
        return r.json()

    def metadata_values(self, field: str, limit: int = 50) -> dict[str, Any]:
        r = self._client.get(
            f"{self.settings.api_base}/metadata/values",
            params={"field": field, "limit": limit},
            headers=self._headers(),
        )
        r.raise_for_status()
        return r.json()

    def ingest_config(self) -> dict[str, Any]:
        r = self._client.get(f"{self.settings.api_base}/metadata/ingest-config")
        r.raise_for_status()
        return r.json()

    def search(
        self,
        query: str,
        *,
        top_k: int = 40,
        min_similarity: float = 0.3,
        metadata_filters: dict[str, str] | None = None,
        tags: list[str] | None = None,
        time_filter: str = "all",
        llm_top_n: int | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "query": query,
            "top_k": top_k,
            "min_similarity": min_similarity,
            "metadata_filters": metadata_filters or {},
            "tags": tags or [],
            "time_filter": time_filter,
            "include_public": True,
        }
        # llm_top_n must be >= 1 if set; omit to use backend default
        if llm_top_n is not None:
            body["llm_top_n"] = max(1, llm_top_n)
        r = self._client.post(
            f"{self.settings.api_base}/search",
            headers=self._headers(),
            json=body,
        )
        r.raise_for_status()
        return r.json()

    def video_metadata(self, source: str) -> dict[str, Any]:
        r = self._client.get(
            f"{self.settings.api_base}/videos/metadata",
            params={"source": source},
            headers=self._headers(),
        )
        r.raise_for_status()
        return r.json()

    def video_detections(self, source: str) -> dict[str, Any] | None:
        r = self._client.get(
            f"{self.settings.api_base}/videos/detections",
            params={"source": source},
            headers=self._headers(),
        )
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json()

    def download_stream(self, source: str, dest: Path) -> Path:
        dest.parent.mkdir(parents=True, exist_ok=True)
        url = f"{self.settings.api_base}/videos/stream"
        with self._client.stream(
            "GET",
            url,
            params={"source": source, "token": self.token},
        ) as r:
            r.raise_for_status()
            with dest.open("wb") as f:
                for chunk in r.iter_bytes():
                    f.write(chunk)
        return dest

    def reingest(
        self,
        *,
        original_video: str | None = None,
        stream_id: str | None = None,
        chunk_count: int = 1,
        custom_prompt: str,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "chunk_count": chunk_count,
            "custom_prompt": custom_prompt[:800],
        }
        if original_video:
            body["original_video"] = original_video
        if stream_id:
            body["stream_id"] = stream_id
        r = self._client.post(
            f"{self.settings.api_base}/dashboard/reingest",
            headers=self._headers(),
            json=body,
        )
        r.raise_for_status()
        return r.json()

    def reingest_status(self, job_id: str) -> dict[str, Any]:
        r = self._client.get(
            f"{self.settings.api_base}/dashboard/reingest/{job_id}",
            headers=self._headers(),
        )
        r.raise_for_status()
        return r.json()

    def poll_reingest(
        self, job_id: str, *, interval: float = 4.0, timeout: float = 600.0
    ) -> dict[str, Any]:
        deadline = time.time() + timeout
        while time.time() < deadline:
            status = self.reingest_status(job_id)
            if status.get("status") in {"completed", "failed", "error"}:
                return status
            time.sleep(interval)
        raise TimeoutError(f"reingest {job_id} timed out after {timeout}s")
