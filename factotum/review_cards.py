"""Review's suggestion cards and the evidence on its task cards, outside the API (spoken Review, stage 1,
design §5.1). A card is an unjudged evidence row with no task that Review offers as Create / Dismiss: a
planner create, a Slack mention naming no task, a task suggested from mail, a ripple's create
(`review_queue.SUGGESTION_KINDS`). Reads only."""
from __future__ import annotations

import json
import sqlite3

from . import config as C, relations
from .db import rows
from .review_queue import SUGGESTION_KINDS, is_card
from .ripple import ACCOUNT as RIPPLE   # evidence.account of a card a ripple staged
_PENDING = "SELECT id, task_id, kind, ref, account, label, excerpt, at FROM evidence WHERE judged_at IS NULL"


def evidence_by_task(conn: sqlite3.Connection, ids: set[int] | None = None) -> dict[int, list[dict]]:
    """Every unjudged evidence row on a task (`ids`, or every task), by task id, oldest first: what a
    Review decision judges in its batch. A suggested link (`link_suggestion`) is its own list on the card
    and a filing suggestion its chips, so neither is here. A proposal's `change` is its ref parsed."""
    by_task: dict[int, list] = {}
    for e in rows(conn, _PENDING + " ORDER BY id"):
        if e["task_id"] is None or e["kind"] in ("link_suggestion", "filing"):
            continue
        tid = int(e["task_id"])
        if ids is not None and tid not in ids:
            continue
        change = None
        if e["kind"] == "proposal":
            try:
                change = json.loads(e["ref"]) if e["ref"] else None
            except ValueError:
                change = None
        by_task.setdefault(tid, []).append({"id": e["id"], "kind": e["kind"], "label": e["label"], "reason": e["excerpt"], "change": change,
                                            "at": e["at"], "by": "ripple" if e["account"] == RIPPLE else None})
    return by_task


def cards(conn: sqlite3.Connection, cfg: dict) -> list[dict]:
    """Every waiting suggestion card, oldest first."""
    return _cards(conn, cfg, [e for e in rows(conn, _PENDING + " ORDER BY id") if is_card(e)])


def card(conn: sqlite3.Connection, cfg: dict, evidence_id: int) -> dict | None:
    """One waiting card as `cards` ships it; None when the row is not a card or was judged."""
    es = [e for e in rows(conn, _PENDING + " AND id = ?", (evidence_id,)) if is_card(e)]
    return _cards(conn, cfg, es)[0] if es else None


def _cards(conn: sqlite3.Connection, cfg: dict, es: list[dict]) -> list[dict]:
    """The cards as Review ships them, each link with the other task's name."""
    out =[{"key": f"s:{e['id']}", "id": e["id"], "kind": e["kind"], "label": e["label"], "reason": e["excerpt"], "at": e["at"],
            "fields": suggestion_fields(cfg, e), "mail": suggestion_mail(e), "links": suggestion_links(e),
            "by": "ripple" if e["account"] == RIPPLE else None} for e in es]
    others = relations.briefs(conn, {l["other"] for c in out for l in c["links"]})
    for c in out:                                            # a mail suggestion's link, made when Create is pressed
        c["links"] = [dict(l, other_name=(others.get(l["other"]) or {}).get("name")) for l in c["links"]]
    return out


def row(conn: sqlite3.Connection, evidence_id: int) -> dict | None:
    """The evidence row behind a suggestion card (a create with no task behind it), judged or not."""
    e = rows(conn, f"SELECT * FROM evidence WHERE id = ? AND kind IN ({','.join('?' * len(SUGGESTION_KINDS))}) AND task_id IS NULL",
             (evidence_id, *SUGGESTION_KINDS))
    return e[0] if e else None


def suggestion_mail(e: dict) -> dict | None:
    """For a card the mail triage made (SPEC §8.5): the thread it came from, as Mailbird reported it."""
    if e["kind"] != "proposal":
        return None
    try:
        m = (json.loads(e["ref"]) if e.get("ref") else {}).get("mail")
    except (ValueError, AttributeError):
        return None
    return m if isinstance(m, dict) and m.get("ref") else None


def suggestion_links(e: dict) -> list[dict]:
    """The links a mail suggestion carries (mail_triage.py kept only those to rows its run was shown)."""
    if e["kind"] != "proposal":
        return []
    try:
        ls = (json.loads(e["ref"]) if e.get("ref") else {}).get("links")
    except (ValueError, AttributeError):
        return []
    return [{"kind": l.get("kind"), "other": l["other"]} for l in ls if isinstance(l, dict) and type(l.get("other")) is int] if isinstance(ls, list) else []


def suggestion_fields(cfg: dict, e: dict) -> dict:
    """What Create would make: the planner's fields for an uncited create (evidence.ref holds
    them; older rows have only a label), or for a Slack mention the message's text as the name,
    the workspace's project and the permalink as context and source link."""
    if e["kind"] == "slack_mention":
        ws = next((w for w in (C.get(cfg, "sources.slack.workspaces", []) or []) if str(w.get("name") or "").strip() == (e.get("account") or "")), {})
        name = str(e.get("label") or "").split(": ", 1)[-1].strip()[:80] or "(untitled)"
        project = ws.get("project") if ws.get("project") in C.project_names(cfg) else None     # a name outside the taxonomy would become Unknown
        return {"name": name, "project": project, "context": f"{e.get('excerpt') or ''}\n{e.get('ref') or ''}".strip(), "source_link": e.get("ref")}
    try:
        d = json.loads(e["ref"]) if e.get("ref") else {}
    except ValueError:
        d = {}
    f = dict((d or {}).get("fields") or {})
    if not f.get("name"):
        f["name"] = str(e.get("label") or "").split(":", 1)[-1].strip()[:80] or "(untitled)"
    return f
