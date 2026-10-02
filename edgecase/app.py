"""FastAPI UI — Phase 4."""

from __future__ import annotations

from fastapi import FastAPI

app = FastAPI(title="Edge-Case Miner")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "phase": "0"}
