"""Configuration: config/factotum.yaml (structure) + .env (secrets) + settings table (overrides).

Precedence for a key read through `effective()`: database override > yaml > built-in default.

2026-11-10: with no config/factotum.yaml the fallback is config/factotum.starter.yaml (neutral, everything
off), not the example, which was the owner's own setup and so quietly ran a stranger's install as theirs.
"""
from __future__ import annotations

import copy
import os
import sys
from pathlib import Path
from typing import Any

import yaml
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
USER_CONFIG = ROOT / "config" / "factotum.yaml"
STARTER_CONFIG = ROOT / "config" / "factotum.starter.yaml"
EXAMPLE_CONFIG = ROOT / "config" / "factotum.example.yaml"
DEFAULT_CONFIG_PATHS = [USER_CONFIG, STARTER_CONFIG]

STARTER_NOTE = ("factotum: no config/factotum.yaml, so starter settings in use (config/factotum.starter.yaml, which "
                "switches every input, output and scheduled paid Claude run off, ripples and filing on; a Settings override in the database still wins). "
                "Copy config/factotum.example.yaml to config/factotum.yaml to make it your own.")
_starter_told = False

# Keys the Settings tab may override (stored in the `settings` table as dotted keys).
SETTINGS_KEYS = [
    "lifecycle.fade_days", "lifecycle.fade_warn_days", "lifecycle.last_call_days",
    "lifecycle.top_cap", "lifecycle.review_cap",
    "notify.review_time", "notify.snooze_presets", "notify.ntfy.enabled", "notify.ntfy.topic", "notify.provider", "notify.app.badge",
    "notify.morning_brief.enabled", "notify.morning_brief.time", "notify.morning_brief.to",
    "sources.plaud.enabled", "sources.canvas.enabled", "sources.mail.enabled",
    "sources.calendar.enabled", "sources.calendar.calendar_id",
    "sources.canvas.sweep_minutes", "sources.mail.sweep_minutes", "sources.calendar.sweep_minutes",
    "sources.slack.enabled", "sources.slack.sweep_minutes", "sources.discord.enabled",
    "sources.mail.suggest.enabled", "sources.mail.suggest.minutes",
    "sinks.notion_notes.enabled", "sinks.notion_tasks_mirror.enabled", "sinks.notion_tasks_mirror.sweep_minutes",
    "scoring.project_weights",
    "claude.models", "claude.planner.enabled", "claude.budgets_usd.chat_turn", "claude.summary.auto", "claude.summary.auto_minutes",
    "claude.ripple.enabled", "claude.ripple.model", "claude.ripple.delay_seconds", "claude.ripple.hourly_cap", "claude.budgets_usd.ripple",
    "server.oauth.enabled", "server.mcp_writes",
    "links.suggestions_per_review",                                       # stage 7
    "claude.filing.enabled",                                              # stage 7
]

# One default for every Settings key. Absent means off for the scheduled paid runs and anything that sends
# mail (PAID_SWITCHES, the mail suggestions); ripples and filing (ON_WHEN_NEW) are the two exceptions, on
# unless switched off: each runs only after something the owner did, costs cents, and writes nothing
# History cannot undo (the owner's decision, 2026-11-11). Read these with value(). The AST test in tests/test_stage3.py
# holds every literal default for one of these keys elsewhere in the code to this table.
DEFAULTS: dict[str, Any] = {
    "lifecycle.fade_days": 21, "lifecycle.fade_warn_days": 3, "lifecycle.last_call_days": 7,
    "lifecycle.top_cap": 7, "lifecycle.review_cap": 15,
    "notify.review_time": "19:30", "notify.snooze_presets": [], "notify.ntfy.enabled": True, "notify.ntfy.topic": "",
    "notify.provider": "ntfy",
    "notify.app.badge": True,       # stage 8: costs nothing, sends nothing (never a push of its own), so on when absent
    "notify.morning_brief.enabled": False, "notify.morning_brief.time": "06:30", "notify.morning_brief.to": "",
    "sources.plaud.enabled": False, "sources.canvas.enabled": False, "sources.mail.enabled": False,
    "sources.calendar.enabled": False, "sources.calendar.calendar_id": "primary",
    "sources.canvas.sweep_minutes": 60, "sources.mail.sweep_minutes": 20, "sources.calendar.sweep_minutes": 30,
    "sources.slack.enabled": False, "sources.slack.sweep_minutes": 20, "sources.discord.enabled": False,
    "sources.mail.suggest.enabled": False, "sources.mail.suggest.minutes": 30,
    "sinks.notion_notes.enabled": False, "sinks.notion_tasks_mirror.enabled": False,
    "sinks.notion_tasks_mirror.sweep_minutes": 10,
    "scoring.project_weights": {},
    "claude.models": {}, "claude.planner.enabled": False, "claude.budgets_usd.chat_turn": 1.0,
    "claude.summary.auto": False, "claude.summary.auto_minutes": 10,
    "claude.ripple.enabled": True, "claude.ripple.model": "sonnet", "claude.ripple.delay_seconds": 60,
    "claude.ripple.hourly_cap": 10, "claude.budgets_usd.ripple": 0.30,
    "server.oauth.enabled": False, "server.mcp_writes": "stage",
    "links.suggestions_per_review": 5,                                    # stage 7
    "claude.filing.enabled": True,                                        # stage 7
}
PAID_SWITCHES = ("notify.morning_brief.enabled", "claude.planner.enabled", "claude.summary.auto", "sinks.notion_notes.enabled")
# On when absent, and new in this release (2026-11-11): before it nothing of the kind ran.
ON_WHEN_NEW = ("claude.ripple.enabled", "claude.filing.enabled")
# The code's defaults before 2026-11-10 (the four paid / mailing switches were on when absent; no ripple or
# filing run existed), for `factotum effective --as-before` only: diff it against the plain output when deploying.
OLD_DEFAULTS: dict[str, Any] = {**DEFAULTS, **{k: True for k in PAID_SWITCHES}, **{k: False for k in ON_WHEN_NEW}}
# What `factotum effective` reports, and the values it never prints (shown as a fingerprint).
REPORT_KEYS = ["owner.timezone", *SETTINGS_KEYS]
MASKED = {"notify.ntfy.topic", "notify.morning_brief.to", "sources.calendar.calendar_id"}
_MISSING = object()


def machine_timezone() -> str:
    """This machine's IANA zone: TZ, /etc/timezone, the /etc/localtime link, else UTC. Always a name ZoneInfo accepts."""
    guesses = (
        lambda: (os.environ.get("TZ") or "").lstrip(":"),
        lambda: Path("/etc/timezone").read_text(encoding="utf-8").splitlines()[0].strip(),
        lambda: os.path.realpath("/etc/localtime").split("zoneinfo/", 1)[1],
    )
    for guess in guesses:
        try:
            name = guess()
            if name:
                ZoneInfo(name)
                return name
        except Exception:               # any failure means "not this one"
            continue
    return "UTC"


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
            global _starter_told
            cfg["_path"] = str(p)
            cfg["_starter"] = p.resolve() == STARTER_CONFIG.resolve()
            if cfg["_starter"] and not _starter_told:
                print(STARTER_NOTE, file=sys.stderr, flush=True)
                _starter_told = True
            if not isinstance(cfg.get("owner"), dict):
                cfg["owner"] = {}
            name = cfg["owner"].get("timezone")
            ok = False
            if name:
                try:
                    ZoneInfo(str(name))
                    ok = True
                except Exception:
                    pass
            if not ok:
                tz = machine_timezone()
                if name:
                    print(f"factotum: owner.timezone {name!r} is not a time zone; using {tz}", file=sys.stderr, flush=True)
                cfg["owner"]["timezone"] = tz
                cfg["_tz_from_machine"] = True
            return cfg
    raise FileNotFoundError("no config found; copy config/factotum.example.yaml to config/factotum.yaml")


def get(cfg: dict, dotted: str, default: Any = None) -> Any:
    cur: Any = cfg
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return cur


def value(cfg: dict, key: str) -> Any:
    """A Settings key's value with its default from DEFAULTS; a key not in DEFAULTS raises KeyError on purpose."""
    return get(cfg, key, DEFAULTS[key])


def explain(cfg: dict, overrides: dict, key: str, defaults: dict | None = None) -> tuple[Any, str]:
    """(value, origin) for one key. Origin: settings (a Settings-tab override), machine (the time zone
    taken from this machine), file, default (from `defaults`, DEFAULTS when None) or unset (value None)."""
    if key in SETTINGS_KEYS and key in overrides:
        return overrides[key], "settings"
    if key == "owner.timezone" and cfg.get("_tz_from_machine"):
        return get(cfg, key), "machine"
    v = get(cfg, key, _MISSING)
    if v is not _MISSING:
        return v, "file"
    table = DEFAULTS if defaults is None else defaults
    if key in table:
        return table[key], "default"
    return None, "unset"


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
