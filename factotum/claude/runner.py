"""`claude -p` as a function (SPEC.md §8). One call per run kind; the model and the budget come
from config (`claude.models.<kind>`, `claude.budgets_usd.<kind>`), the prompt goes over stdin
(never argv: it can be large and `ps` would show it), the final message is parsed as JSON.

    run(cfg, kind, prompt, allowed_tools=(), workdir=None) -> RunResult            # one JSON envelope
    run(..., mcp_servers={...}, resume=session_id, tools="", on_event=fn)         # stream-json, events as they come

Lessons carried from plaud-sync (CLAUDE.md gotchas): a dead login exits 1 in ~400 ms with an
empty stderr and the reason only in the envelope's `result`, so that text is always kept; exit 0
is not success, the final message must parse as the JSON the prompt asked for; only the MCP servers
named for this run are loaded (`--strict-mcp-config`), so nothing can stall on a permission prompt.
Every run also passes `--setting-sources ""` and `--disable-slash-commands`: without them the user's
own hooks, plugins and skills load into the headless session (a probe on 2026-11-02 showed ~5 KB of
SessionStart hook output and the user's permission mode in every run).
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import threading
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
    session_id: str | None = None   # for --resume on the next turn of a chat thread


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


def command(cfg: dict, kind: str, allowed_tools: list[str] | tuple[str, ...] = (), *, mcp_servers: dict | None = None,
            resume: str | None = None, tools: str | None = None, stream: bool = False) -> list[str]:
    model = str(C.get(cfg, f"claude.models.{kind}", "sonnet"))
    budget = float(C.get(cfg, f"claude.budgets_usd.{kind}", 1.0))
    cmd = [str(C.get(cfg, "claude.binary", "claude")), "-p", "--model", model, "--output-format", "stream-json" if stream else "json",
           "--max-budget-usd", str(budget), "--max-turns", str(int(C.get(cfg, f"claude.max_turns.{kind}", DEFAULT_MAX_TURNS))),
           "--permission-mode", "default", "--setting-sources", "", "--disable-slash-commands",
           "--strict-mcp-config", "--mcp-config", json.dumps({"mcpServers": mcp_servers or {}}, separators=(",", ":")),
           "--allowedTools", ",".join(allowed_tools) if allowed_tools else ""]
    if stream:
        cmd.append("--verbose")                      # stream-json in print mode requires it
    if tools is not None:
        cmd += ["--tools", tools]                    # "" = no built-in tools at all (a chat turn has only its MCP server)
    if resume:
        cmd += ["--resume", str(resume)]
    return cmd


def _finish(env: dict, returncode: int, stderr: str) -> RunResult:
    """One envelope (the json path's object or the stream's `result` event) → RunResult."""
    text = env.get("result") or ""
    cost = float(env.get("total_cost_usd") or 0.0)
    ms = int(env.get("duration_ms") or 0)
    sid = env.get("session_id")
    if returncode != 0 or env.get("is_error"):
        auth = bool(_AUTH_RE.search(text))
        why = text[-400:] or (stderr or "")[-400:] or f"exit {returncode}"
        return RunResult(False, None, text, cost, ms, error=("auth: " if auth else "claude: ") + why, auth_failed=auth, envelope=env, session_id=sid)
    data = extract_json(text)
    if data is None:
        return RunResult(False, None, text, cost, ms, error=f"final message has no JSON object: {text[-300:]}", envelope=env, session_id=sid)
    return RunResult(True, data, text, cost, ms, envelope=env, session_id=sid)


def _env(workdir) -> dict:
    env = dict(os.environ)
    if workdir:
        env["FACTOTUM_RUN_DIR"] = str(workdir)     # the MCP server records drafts and seen threads there
    return env


def run(cfg: dict, kind: str, prompt: str, *, allowed_tools: list[str] | tuple[str, ...] = (), workdir: Path | str | None = None,
        timeout_s: int | None = None, exec_=subprocess.run, mcp_servers: dict | None = None, resume: str | None = None,
        tools: str | None = None, on_event=None, popen_=subprocess.Popen) -> RunResult:
    """`on_event` set → stream-json: every event (system init, assistant blocks, tool results, the
    final result) is passed to it as it arrives, so a chat turn can show "searching mail…" live."""
    timeout_s = timeout_s or int(C.get(cfg, f"claude.timeouts_s.{kind}", DEFAULT_TIMEOUT_S))
    cwd = str(workdir) if workdir else None
    cmd = command(cfg, kind, allowed_tools, mcp_servers=mcp_servers, resume=resume, tools=tools, stream=on_event is not None)
    if on_event is not None:
        return _run_stream(cmd, prompt, cwd, timeout_s, on_event, popen_, _env(workdir))
    try:
        res = exec_(cmd, input=prompt, capture_output=True, text=True, timeout=timeout_s, cwd=cwd, env=_env(workdir))
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
    return _finish(env, res.returncode, res.stderr or "")


def _run_stream(cmd: list[str], prompt: str, cwd: str | None, timeout_s: int, on_event, popen_, env: dict) -> RunResult:
    try:
        proc = popen_(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=cwd, env=env)
    except FileNotFoundError as e:
        return RunResult(False, None, "", 0.0, 0, error=f"claude binary not found ({e}): set claude.binary to the absolute path")
    timed_out = threading.Event()

    def _kill():
        timed_out.set()
        try:
            proc.kill()
        except Exception:
            pass
    timer = threading.Timer(timeout_s, _kill)
    timer.daemon = True
    timer.start()
    final: dict | None = None
    try:
        proc.stdin.write(prompt)
        proc.stdin.close()
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(ev, dict):
                continue
            if ev.get("type") == "result":
                final = ev
            try:
                on_event(ev)
            except Exception:
                pass                                 # a UI listener that went away must not kill the run
        rc = proc.wait(timeout=30)
    finally:
        timer.cancel()
    stderr = ""
    try:
        stderr = proc.stderr.read() or ""
    except Exception:
        pass
    if timed_out.is_set():
        return RunResult(False, None, "", float((final or {}).get("total_cost_usd") or 0.0), timeout_s * 1000, error=f"timeout after {timeout_s}s")
    if final is None:
        return RunResult(False, None, stderr[-1500:], 0.0, 0, error=f"claude exited {rc} with no result event: {stderr[-300:]}")
    return _finish(final, rc or 0, stderr)
