"""Configuration: config/factotum.yaml (structure) + .env (secrets) + settings table (overrides).

Precedence for a key read through `effective()`: database override > yaml > built-in default.
"""
from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATHS = [ROOT / "config" / "factotum.yaml", ROOT / "config" / "factotum.example.yaml"]

# Keys the Settings tab may override (stored in the `settings` table as dotted keys).
SETTINGS_KEYS = [
    "lifecycle.fade_days", "lifecycle.fade_warn_days", "lifecycle.last_call_days",
    "lifecycle.top_cap", "lifecycle.review_cap",
    "notify.review_time", "notify.snooze_presets", "notify.ntfy.enabled", "notify.ntfy.topic",
    "notify.morning_brief.enabled", "notify.morning_brief.time", "notify.morning_brief.to",
    "sources.plaud.enabled", "sources.canvas.enabled", "sources.mail.enabled",
    "sources.calendar.enabled", "sources.calendar.calendar_id",
    "sources.canvas.sweep_minutes", "sources.mail.sweep_minutes", "sources.calendar.sweep_minutes",
    "sources.slack.enabled", "sources.slack.sweep_minutes", "sources.discord.enabled",
    "sinks.notion_notes.enabled", "sinks.notion_tasks_mirror.enabled", "sinks.notion_tasks_mirror.sweep_minutes",
    "scoring.project_weights",
    "claude.models", "claude.planner.enabled", "claude.budgets_usd.chat_turn",
    "server.oauth.enabled", "server.mcp_writes",
]


def load(path: str | os.PathLike | None = None) -> dict:
    """Load .env, then the YAML config. Returns a plain dict with `_path` recording the file used."""
    load_dotenv(ROOT / ".env")
    candidates = [Path(path)] if path else []
    env_path = os.environ.get("FACTOTUM_CONFIG")
    if env_path:
        candidates.append(ROOT / env_path if not Path(env_path).is_absolute() else Path(env_path))
    candidates += DEFAULT_CONFIG_PATHS
    for p in candidates:
        if p and p.exists():
            with open(p, encoding="utf-8") as f:
                cfg = yaml.safe_load(f) or {}
            cfg["_path"] = str(p)
            return cfg
    raise FileNotFoundError("no config found; copy config/factotum.example.yaml to config/factotum.yaml")


def get(cfg: dict, dotted: str, default: Any = None) -> Any:
    cur: Any = cfg
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return cur


def set_(cfg: dict, dotted: str, value: Any) -> None:
    parts = dotted.split(".")
    cur = cfg
    for part in parts[:-1]:
        cur = cur.setdefault(part, {})
    cur[parts[-1]] = value


def effective(cfg: dict, overrides: dict[str, Any]) -> dict:
    """Return a copy of cfg with Settings-tab overrides applied (only whitelisted keys)."""
    out = copy.deepcopy(cfg)
    for key, value in overrides.items():
        if key in SETTINGS_KEYS:
            set_(out, key, value)
    return out


def project_weight(cfg: dict, project: str | None) -> str:
    for p in get(cfg, "taxonomy.projects", []) or []:
        if p.get("name") == project:
            return p.get("weight", "medium")
    return "medium"


def project_never_fades(cfg: dict, project: str | None) -> bool:
    for p in get(cfg, "taxonomy.projects", []) or []:
        if p.get("name") == project:
            return bool(p.get("never_fade"))
    return False


def project_names(cfg: dict) -> list[str]:
    return [p["name"] for p in get(cfg, "taxonomy.projects", []) or []]


def db_path() -> Path:
    p = os.environ.get("FACTOTUM_DB", "data/factotum.db")
    path = Path(p)
    return path if path.is_absolute() else ROOT / path
