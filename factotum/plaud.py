"""The Plaud CLI as a source (SPEC.md §6 "Plaud"). Transcripts and summaries come to disk through
`plaud … -o`, never through a tool result, so the 40 KB MCP spill cannot happen.

    locate(cfg, subject, date)         -> Recording | None   (plaud search, within ±1 day of the mail)
    details(cfg, id)                   -> {name, start_at, created_at, duration, …}
    fetch_files(cfg, id, dest_dir)     -> Files(summary, transcript)
"""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from . import config as C

_LINE = re.compile(r"^\s*(?P<id>\S+)\s{2,}(?P<name>.+?)\s{2,}(?P<date>\d{4}-\d{2}-\d{2})\s{2,}(?P<dur>\S+)\s*$")
_KV = re.compile(r"^\s*(?P<k>[a-z_]+):\s*(?P<v>.*?)\s*$")
_ID = re.compile(r"^[A-Za-z0-9_\-]{6,64}$")   # Plaud ids look like of_<32 hex>; anything else never reaches the CLI


def _checked_id(rec_id: str) -> str:
    if not _ID.match(rec_id or "") or rec_id.startswith("-"):
        raise PlaudError(f"not a Plaud recording id: {rec_id!r}")
    return rec_id


class PlaudError(RuntimeError):
    pass


@dataclass
class Recording:
    id: str
    name: str
    date: str


@dataclass
class Files:
    summary: Path
    transcript: Path


def _cli(cfg: dict) -> str:
    return str(C.get(cfg, "sources.plaud.cli", "plaud"))


def _run(cfg: dict, args: list[str], run_=subprocess.run, timeout: int = 120) -> str:
    try:
        res = run_([_cli(cfg)] + args, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError as e:
        raise PlaudError(f"plaud CLI not found ({e}); see docs/SETUP.md §2") from e
    except subprocess.TimeoutExpired as e:
        raise PlaudError(f"plaud {args[0]} timed out after {timeout}s") from e
    if res.returncode != 0:
        raise PlaudError(f"plaud {args[0]} exited {res.returncode}: {(res.stderr or res.stdout or '')[-300:]}")
    return res.stdout or ""


def strip_subject(cfg: dict, subject: str) -> str:
    prefix = str(C.get(cfg, "sources.plaud.subject_prefix", "") or "")
    s = re.sub(r"\s+", " ", subject or "").strip()
    if prefix and s.lower().startswith(prefix.lower()):
        s = s[len(prefix):].strip()
    return s


def parse_search(out: str) -> list[Recording]:
    recs = []
    for line in out.splitlines():
        m = _LINE.match(line)
        if m and not line.lstrip().startswith("-"):
            recs.append(Recording(m["id"], m["name"].strip(), m["date"]))
    return recs


def locate(cfg: dict, subject: str, received_date: str, run_=subprocess.run) -> Recording | None:
    """The recording a Plaud mail announces: same title, within a day of the mail. Plaud's mail
    never carries the id, so the title is the join (the id becomes the durable key from here on).
    Returns None rather than guessing when nothing matches."""
    title = strip_subject(cfg, subject)
    d = date.fromisoformat(received_date[:10])
    lo, hi = (d - timedelta(days=1)).isoformat(), (d + timedelta(days=1)).isoformat()
    keywords = [title]
    words = sorted((w for w in re.findall(r"[A-Za-z][A-Za-z\-']{4,}", title)), key=len, reverse=True)
    keywords += words[:2]
    # The subject is attacker-controlled mail text: never let it start a CLI flag.
    keywords = [k for k in (kw.lstrip("-").strip() for kw in keywords) if k]
    for kw in keywords:
        recs = [r for r in parse_search(_run(cfg, ["search", kw, "--from", lo, "--to", hi, "--max", "50"], run_)) if lo <= r.date <= hi]
        if not recs:
            continue
        exact = [r for r in recs if r.name.lower() == title.lower()]
        pool = exact or recs
        pool.sort(key=lambda r: r.date, reverse=True)
        return pool[0]
    return None


def details(cfg: dict, rec_id: str, run_=subprocess.run) -> dict:
    out = _run(cfg, ["file", _checked_id(rec_id)], run_)
    d: dict = {}
    for line in out.splitlines():
        m = _KV.match(line)
        if m:
            d[m["k"]] = m["v"]
    if "id" not in d:
        raise PlaudError(f"plaud file {rec_id}: no details in output: {out[-200:]}")
    return d


def fetch_files(cfg: dict, rec_id: str, dest: Path, run_=subprocess.run) -> Files:
    rec_id = _checked_id(rec_id)
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    summary, transcript = dest / "summary.md", dest / "transcript.md"
    _run(cfg, ["summary", rec_id, "-o", str(summary)], run_)
    _run(cfg, ["transcript", rec_id, "-o", str(transcript)], run_, timeout=300)
    for p in (summary, transcript):
        if not p.exists():
            raise PlaudError(f"plaud did not write {p.name}")
    return Files(summary, transcript)
