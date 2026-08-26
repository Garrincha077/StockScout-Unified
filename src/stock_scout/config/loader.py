from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import yaml
from dotenv import dotenv_values

from stock_scout.config.schema import Env, Settings


def _find_project_root(start: Path | None = None) -> Path:
    """Walk upward looking for pyproject.toml; fall back to cwd."""
    here = (start or Path.cwd()).resolve()
    for candidate in [here, *here.parents]:
        if (candidate / "pyproject.toml").exists():
            return candidate
    return here


def load_config(config_path: str | Path | None = None) -> Settings:
    """Load Settings from YAML. Defaults to <project_root>/config/config.yaml."""
    root = _find_project_root()
    if config_path is None:
        config_path = root / "config" / "config.yaml"
    path = Path(config_path)
    if not path.is_absolute():
        path = root / path

    if not path.exists():
        # Allow running with defaults if no config.yaml present.
        settings = Settings()
    else:
        with path.open("r", encoding="utf-8") as fh:
            raw = yaml.safe_load(fh) or {}
        settings = Settings(**raw)

    settings.project_root = root
    return settings


@lru_cache(maxsize=1)
def load_env() -> Env:
    """Load .env once per process.

    Pydantic-Settings prioritises OS env vars over `.env`. On Windows, an OS
    env var with an EMPTY value (e.g. a stale `ANTHROPIC_API_KEY=`) silently
    blanks out the real value in `.env`. We work around that by reading the
    `.env` file directly and using its value whenever the corresponding OS
    env var is empty or unset.
    """
    root = _find_project_root()
    env_path = root / ".env"
    file_vals: dict[str, str | None] = {}
    if env_path.exists():
        file_vals = dotenv_values(env_path)

    # For each .env key with a non-empty value, only override the OS var if
    # the OS var is missing or empty.
    for key, val in file_vals.items():
        if val is None:
            continue
        existing = os.environ.get(key, "")
        if not existing:
            os.environ[key] = val

    return Env()
