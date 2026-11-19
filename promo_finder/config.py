"""Load config.yaml and apply env overrides."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    load_dotenv = None

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = REPO_ROOT / "config.yaml"


@dataclass
class ProviderSettings:
    name: str
    base_url: str | None
    model: str | None
    api_key: str | None
    rpm: float
    judge_model: str | None


def load_config(path: str | Path | None = None) -> dict[str, Any]:
    if load_dotenv is not None:
        load_dotenv(REPO_ROOT / ".env")
    cfg_path = Path(path) if path else DEFAULT_CONFIG
    with open(cfg_path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def provider_settings(cfg: dict[str, Any], provider: str | None = None, api_key: str | None = None) -> ProviderSettings:
    """Resolve the active provider. Precedence: explicit args > env vars > config.yaml."""
    name = provider or os.getenv("LLM_PROVIDER") or cfg["provider"]
    presets = cfg.get("providers", {})
    if name not in presets:
        raise ValueError(f"Unknown provider '{name}'. Known: {', '.join(presets)}")
    p = presets[name]
    key = api_key or os.getenv("LLM_API_KEY") or (os.getenv(p["api_key_env"]) if p.get("api_key_env") else None)
    return ProviderSettings(
        name=name,
        base_url=os.getenv("LLM_BASE_URL") or p.get("base_url"),
        model=os.getenv("LLM_MODEL") or p.get("model"),
        api_key=key or None,
        rpm=float(os.getenv("LLM_RPM") or p.get("rpm") or 10),
        judge_model=os.getenv("JUDGE_MODEL") or cfg.get("judge_model"),
    )
