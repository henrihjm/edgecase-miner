"""Load team config and resolve endpoints. Never print secret values."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _source_team_config() -> Path:
    config_dir = Path("/config")
    matches = sorted(config_dir.glob("*.config"))
    if len(matches) != 1:
        raise RuntimeError(
            f"expected exactly one /config/*.config, found {len(matches)}"
        )
    path = matches[0]
    # Parse KEY=VALUE without shelling out (avoids printing via env dumps).
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        if key and key not in os.environ:
            os.environ[key] = value
    return path


@dataclass(frozen=True)
class Settings:
    ingress_url: str
    username: str
    password: str
    gpu_bearer_token: str
    cosmos3_reason_url: str
    yolo_url: str
    cosmos_embed1_url: str
    wandb_api_key: str
    wandb_team: str
    wandb_project: str
    wandb_base_url: str
    wandb_model: str
    repo_root: Path
    cache_dir: Path
    exports_dir: Path

    @property
    def api_base(self) -> str:
        return f"{self.ingress_url.rstrip('/')}/api/v1"


def load_settings() -> Settings:
    _source_team_config()
    gpu_host = os.environ.get("GPU_HOST", "166.19.38.112")
    repo_root = Path(__file__).resolve().parent.parent
    cache_dir = repo_root / "cache"
    exports_dir = repo_root / "exports"
    cache_dir.mkdir(parents=True, exist_ok=True)
    exports_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / "verify").mkdir(parents=True, exist_ok=True)
    (cache_dir / "clips").mkdir(parents=True, exist_ok=True)

    required = ["INGRESS_URL", "USERNAME", "PASSWORD", "GPU_BEARER_TOKEN"]
    missing = [k for k in required if not os.environ.get(k)]
    if missing:
        raise RuntimeError(f"missing required env vars: {', '.join(missing)}")

    return Settings(
        ingress_url=os.environ["INGRESS_URL"].rstrip("/"),
        username=os.environ["USERNAME"],
        password=os.environ["PASSWORD"],
        gpu_bearer_token=os.environ["GPU_BEARER_TOKEN"],
        cosmos3_reason_url=os.environ.get(
            "COSMOS3_REASON_URL", f"http://{gpu_host}:8001"
        ).rstrip("/"),
        yolo_url=os.environ.get("YOLO_URL", f"http://{gpu_host}:8002").rstrip("/"),
        cosmos_embed1_url=os.environ.get(
            "COSMOS_EMBED1_URL", f"http://{gpu_host}:8003"
        ).rstrip("/"),
        wandb_api_key=os.environ.get("WANDB_API_KEY", ""),
        wandb_team=os.environ.get("WANDB_TEAM", ""),
        wandb_project=os.environ.get("WANDB_PROJECT", ""),
        wandb_base_url=os.environ.get(
            "WANDB_BASE_URL", "https://api.inference.wandb.ai/v1"
        ).rstrip("/"),
        wandb_model=os.environ.get(
            "WANDB_MODEL", "meta-llama/Llama-3.1-8B-Instruct"
        ),
        repo_root=repo_root,
        cache_dir=cache_dir,
        exports_dir=exports_dir,
    )
