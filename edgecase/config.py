"""Loads the team environment and resolves endpoints. Never prints values."""
from __future__ import annotations

import glob
import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = ROOT / "cache"
EXPORT_DIR = ROOT / "exports"
REINGEST_LOG = ROOT / "reingest_log.jsonl"

# Shared GPU host and ports, from .cursor/skills/gpu/README.md. Env values win.
GPU_HOST = "166.19.38.112"
WANDB_BASE_URL = "https://api.inference.wandb.ai/v1"


class ConfigError(RuntimeError):
    pass


def _load_team_config() -> None:
    """Fill os.environ from the single /config/<team>.config if the VM did not export it."""
    if os.environ.get("INGRESS_URL"):
        return
    files = sorted(glob.glob("/config/*.config"))
    if len(files) != 1:
        return
    with open(files[0], encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.removeprefix("export ").partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip("'\""))


@dataclass(frozen=True)
class Settings:
    ingress_url: str
    username: str
    password: str
    gpu_token: str
    cosmos_url: str
    cosmos_model: str
    yolo_url: str
    wandb_api_key: str
    wandb_project: str  # "<team>/<project>", sent as the OpenAI-Project header
    wandb_base_url: str
    llm_model: str

    @property
    def api(self) -> str:
        return self.ingress_url.rstrip("/") + "/api/v1"


def load() -> Settings:
    _load_team_config()
    env = os.environ.get
    missing = [k for k in ("INGRESS_URL", "USERNAME", "PASSWORD") if not env(k)]
    if missing:
        raise ConfigError(
            "Missing environment variables: " + ", ".join(missing)
            + ". Run: set -a && source /config/*.config && set +a"
        )
    team, project = env("WANDB_TEAM", ""), env("WANDB_PROJECT", "")
    return Settings(
        ingress_url=env("INGRESS_URL"),
        username=env("USERNAME"),
        password=env("PASSWORD"),
        gpu_token=env("GPU_BEARER_TOKEN", ""),
        cosmos_url=env("COSMOS3_REASON_URL", f"http://{GPU_HOST}:8001").rstrip("/"),
        cosmos_model=env("COSMOS3_REASON_MODEL", ""),
        yolo_url=env("YOLO_URL", f"http://{GPU_HOST}:8002").rstrip("/"),
        wandb_api_key=env("WANDB_API_KEY", ""),
        wandb_project=f"{team}/{project}" if team and project else "",
        wandb_base_url=env("WANDB_BASE_URL", WANDB_BASE_URL).rstrip("/"),
        llm_model=env("EDGECASE_LLM_MODEL", ""),
    )
