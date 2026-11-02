"""`claude -p` as a function (SPEC.md §8). One call per run kind; the model and the budget come
from config (`claude.models.<kind>`, `claude.budgets_usd.<kind>`), the prompt goes over stdin
(never argv: it can be large and `ps` would show it), the final message is parsed as JSON.

    run(cfg, kind, prompt, allowed_tools=(), workdir=None) -> RunResult

Lessons carried from plaud-sync (CLAUDE.md gotchas): a dead login exits 1 in ~400 ms with an
empty stderr and the reason only in the envelope's `result`, so that text is always kept; exit 0
is not success, the final message must parse as the JSON the prompt asked for; no MCP server is
loaded (`--strict-mcp-config` with an empty config), so nothing can stall on a permission prompt.
"""
from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .. import config as C

DEFAULT_TIMEOUT_S = 600
DEFAULT_MAX_TURNS = 40
_AUTH_RE = re.compile(r"not logged in|please run /login|invalid.*(token|api key)|authentication", re.I)


@dataclass
class RunResult:
    ok: bool
    data: dict | None          # the parsed JSON object the prompt asked for
    result_text: str           # the model's final message, verbatim (the only place a failure explains itself)
    cost_usd: float
    duration_ms: int
    error: str | None = None
    auth_failed: bool = False
    envelope: dict | None = None


def extract_json(text: str | None) -> dict | None:
    """The JSON object in a final message: raw, fenced, or wrapped in stray prose."""
    if not text:
        return None
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    cand = m.group(1) if m else text[text.find("{"): text.rfind("}") + 1]
    if not cand:
        return None
    try:
        v = json.loads(cand)
    except json.JSONDecodeError:
        return None
    return v if isinstance(v, dict) else None


def command(cfg: dict, kind: str, allowed_tools: list[str] | tuple[str, ...] = ()) -> list[str]:
    model = str(C.get(cfg, f"claude.models.{kind}", "sonnet"))
    budget = float(C.get(cfg, f"claude.budgets_usd.{kind}", 1.0))
    cmd = [str(C.get(cfg, "claude.binary", "claude")), "-p", "--model", model, "--output-format", "json",
           "--max-budget-usd", str(budget), "--max-turns", str(int(C.get(cfg, f"claude.max_turns.{kind}", DEFAULT_MAX_TURNS))),
           "--permission-mode", "default", "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
           "--allowedTools", ",".join(allowed_tools) if allowed_tools else ""]
    return cmd


def run(cfg: dict, kind: str, prompt: str, *, allowed_tools: list[str] | tuple[str, ...] = (), workdir: Path | str | None = None,
        timeout_s: int | None = None, exec_=subprocess.run) -> RunResult:
    cmd = command(cfg, kind, allowed_tools)
    timeout_s = timeout_s or int(C.get(cfg, f"claude.timeouts_s.{kind}", DEFAULT_TIMEOUT_S))
    try:
        res = exec_(cmd, input=prompt, capture_output=True, text=True, timeout=timeout_s, cwd=str(workdir) if workdir else None)
    except subprocess.TimeoutExpired:
        return RunResult(False, None, "", 0.0, timeout_s * 1000, error=f"timeout after {timeout_s}s")
    except FileNotFoundError as e:
        return RunResult(False, None, "", 0.0, 0, error=f"claude binary not found ({e}): the service runs with systemd's minimal PATH, "
                                                      "so set claude.binary in config to the absolute path (`which claude`)")
    try:
        env = json.loads(res.stdout or "")
        if not isinstance(env, dict):
            raise ValueError
    except (json.JSONDecodeError, ValueError):
        tail = (res.stdout or "")[-1500:] or (res.stderr or "")[-1500:]
        return RunResult(False, None, tail, 0.0, 0, error=f"claude exited {res.returncode} with no JSON envelope: {tail[-300:]}")
    text = env.get("result") or ""
    cost = float(env.get("total_cost_usd") or 0.0)
    ms = int(env.get("duration_ms") or 0)
    if res.returncode != 0 or env.get("is_error"):
        auth = bool(_AUTH_RE.search(text))
        why = text[-400:] or (res.stderr or "")[-400:] or f"exit {res.returncode}"
        return RunResult(False, None, text, cost, ms, error=("auth: " if auth else "claude: ") + why, auth_failed=auth, envelope=env)
    data = extract_json(text)
    if data is None:
        return RunResult(False, None, text, cost, ms, error=f"final message has no JSON object: {text[-300:]}", envelope=env)
    return RunResult(True, data, text, cost, ms, envelope=env)
