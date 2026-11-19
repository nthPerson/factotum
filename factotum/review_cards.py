"""Review's suggestion cards and the evidence on its task cards, outside the API (spoken Review, stage 1,
design §5.1). A card is an unjudged evidence row with no task that Review offers as Create / Dismiss: a
planner create, a Slack mention naming no task, a task suggested from mail, a ripple's create
(`review_queue.SUGGESTION_KINDS`). Reads only."""
from __future__ import annotations

import json
import sqlite3
from datetime import date
from typing import NamedTuple

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


class CardPlan(NamedTuple):
    changes: list[dict]          # what one batch writes for the card's Create, creates first
    skipped: list[str]           # why nothing is written (the card was made already)
    post: list[dict]             # follow-up rows beside the batch: a Slack card's message link (`task_id` None: the task the create makes)
    summary: dict                # {task_id (None until the batch makes it), reopened, already, ref}


class NoCard(LookupError):
    """Not a suggestion card, or a card judged with no task made from it."""


def card_changes(conn: sqlite3.Connection, cfg: dict, evidence_id: int, draft: dict | None, today: date) -> CardPlan:
    """What Create on a suggestion card writes, built without writing (design §5.5): the `create` (Source
    Planner / Slack / Mail / Ripple, keyed by the card so a second Create is the same task, ref `card<eid>` so
    two cards in one batch never share one), the links the card shows, a mail card's thread followed
    (`follow_thread`, seen up to the mail) and the card's verdict (`judge_evidence applied`). For Create after
    its Undo: the `reopen`, the draft as one `set`, the thread, the links and the verdict. For a card already
    made: nothing, and why. `draft` is None or {"fields", "links"}: the card as edited on the page, laid over
    its own values by `chat.clean_draft`; `links`, when given, are made instead of the card's. Source, key,
    source link and account stay the card's. Raises NoCard (a LookupError: "no such suggestion", "this
    suggestion was already judged") and ValueError ("a task needs a name") before anything is built, so a
    create cannot be skipped inside the batch while its verdict is written. Reads only."""
    from . import chat                                       # lazy: chat pulls in the MCP server and the runner
    from .sync_file import _clean_task_fields
    e = row(conn, evidence_id)
    if not e:
        raise NoCard("no such suggestion")
    edited = (draft or {}).get("fields")
    links = (draft or {}).get("links")
    ref = f"card{evidence_id}"
    is_slack = e["kind"] == "slack_mention"
    mail = suggestion_mail(e)
    by_ripple = e.get("account") == RIPPLE and not is_slack and not mail
    key = f"{'slack-mention' if is_slack else 'mail-suggestion' if mail else 'ripple-suggestion' if by_ripple else 'planner-suggestion'}:{evidence_id}"
    why = "from the mail it came from"
    card_links = [dict(l, why=why) for l in suggestion_links(e)[:3]]
    over = chat.clean_draft(conn, cfg, dict(edited or {}, **({"links": links} if links is not None else {})))
    if links is not None:                   # the owner's links instead of the card's; one the card had keeps its reason
        had = {(l["kind"], l["other"]) for l in card_links}
        card_links = [dict(l, **({"why": why} if (l["kind"], l["other"]) in had else {})) for l in over.pop("links")][:6]
    if edited and "name" in edited and not over.get("name"):
        raise ValueError("a task needs a name")
    if "due" in over and not over["due"]:
        over["hard"] = 0                    # no date: no hard deadline

    def thread(tid) -> list[dict]:          # a mail card's thread, followed from the mail it came from (the new task's ref or an id)
        return [{"op": "follow_thread", "task_id": tid, "fields": {"conversation_id": str(mail["ref"]), "subject": mail.get("subject"),
                                                                    "account": mail.get("account"), "at": mail.get("at")}}] if mail else []

    def follow(tid) -> list[dict]:          # the thread and the links the card showed, on an existing task
        return thread(tid) + [{"op": "link", "task_id": tid, "fields": {k: l[k] for k in ("kind", "other", "why") if k in l}} for l in card_links]

    judge = {"op": "judge_evidence", "task_id": None, "fields": {"evidence_id": evidence_id, "verdict": "applied"}}
    ex = rows(conn, "SELECT id, status, closed_by FROM tasks WHERE source_key = ?", (key,))
    if ex:
        t = ex[0]
        if e["judged_at"]:                  # a second Create: the same task, nothing written
            return CardPlan([], [f"suggestion {evidence_id}: already created as Task-{t['id']}"], [],
                            {"task_id": t["id"], "reopened": False, "already": True, "ref": ref})
        # The card is back: its create was undone (the task dropped by undo: reopen it, never a second task),
        # or the create's undo was refused because the task had been closed since (leave it; judge the card).
        undone = t["status"] == "dropped" and t["closed_by"] == "undo"
        # A draft edited before this second Create: the edits land on the reopened task, in the same batch.
        sets = [{"op": "set", "task_id": t["id"], "fields": over}] if undone and over else []
        changes = ([{"op": "reopen", "task_id": t["id"]}] + sets + follow(t["id"]) if undone else []) + [judge]
        return CardPlan(changes, [], [], {"task_id": t["id"], "reopened": undone, "already": not undone, "ref": ref})
    if e["judged_at"]:
        raise NoCard("this suggestion was already judged")
    want = suggestion_fields(cfg, e)
    f = _clean_task_fields(cfg, want)
    f["context"] = f.get("context") or (e["excerpt"] or None)
    f.update(over)                          # what the owner changed on the card
    if "due" in over:
        f["due_is_datetime"] = 1 if over["due"] and "T" in str(over["due"]) else 0
    if "project" in over:
        f["area"] = _clean_task_fields(cfg, {"project": over["project"]}).get("area") if over["project"] else None
    f["ref"] = ref
    f["links"] = [{k: l[k] for k in ("kind", "other", "why") if k in l} for l in card_links]
    create = {"op": "create", "fields": f, "source_key": key, "created_source": "Slack" if is_slack else "Mail" if mail else "Ripple" if by_ripple else "Planner",
              "source_link": want.get("source_link"), "account": e.get("account") if is_slack else mail.get("account") if mail else None}
    post = [{"task_id": None, "kind": "slack_message", "ref": e["ref"], "label": str(e.get("label") or "")[:120], "account": e.get("account")}] \
        if is_slack and e.get("ref") else []
    return CardPlan([create] + thread(ref) + [judge], [], post, {"task_id": None, "reopened": False, "already": False, "ref": ref})
