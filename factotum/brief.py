"""The morning brief (SPEC.md §7): one email a day, built in Python from the store. Phase 4 adds
the planner run that writes the verdict and `why_now`; until then the brief is the plain list.

Sections: Top · Overdue hard deadlines · Due today · Due tomorrow · Reminders today · Hard
deadlines this week · Calendar (today and tomorrow, when the calendar is on) · Closed since
yesterday (with who closed it) · Waiting on · how many items Review holds.

SMTP: SMTP_HOST / SMTP_PORT / SMTP_USER / SMTP_PASSWORD in .env, falling back to the IMAP account
(a Gmail app password works for both). The sender is SMTP_USER; the recipient is
`notify.morning_brief.to`.
"""
from __future__ import annotations

import os
import smtplib
import sqlite3
from datetime import date, datetime, timedelta
from email.message import EmailMessage
from html import escape

from . import config as C, lifecycle, scoring
from .dates import parse_date
from .db import rows


def smtp_settings() -> dict:
    return {"host": os.environ.get("SMTP_HOST") or "smtp.gmail.com",
            "port": int(os.environ.get("SMTP_PORT") or 465),
            "user": os.environ.get("SMTP_USER") or os.environ.get("IMAP_USER") or "",
            "password": os.environ.get("SMTP_PASSWORD") or os.environ.get("IMAP_PASSWORD") or ""}


def smtp_mailer(cfg: dict, to: str, subject: str, text: str, html: str) -> None:
    s = smtp_settings()
    if not s["user"] or not s["password"]:
        raise RuntimeError("no SMTP credentials: set SMTP_USER/SMTP_PASSWORD or IMAP_USER/IMAP_PASSWORD in .env")
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = s["user"], to, subject
    msg.set_content(text)
    msg.add_alternative(html, subtype="html")
    with smtplib.SMTP_SSL(s["host"], s["port"], timeout=30) as smtp:
        smtp.login(s["user"], s["password"])
        smtp.send_message(msg)


# --- building ---------------------------------------------------------------------------------

def _fmt_due(t: dict, today: date) -> str:
    d = parse_date(t.get("due"))
    if not d:
        return ""
    n = (d - today).days
    when = "today" if n == 0 else "tomorrow" if n == 1 else d.strftime("%a %b ") + str(d.day)
    return f"{when}{' · hard' if t.get('hard') else ''}"


def build(conn: sqlite3.Connection, cfg: dict, today: date, events: list[dict] | None = None, calendar_note: str | None = None, planner: dict | None = None) -> dict:
    open_tasks = rows(conn, "SELECT * FROM tasks WHERE status = 'open'")
    for t in open_tasks:
        t["score"], t["priority"], _ = scoring.score(t, today, cfg)
    days = lambda t: (parse_date(t["due"]) - today).days if t.get("due") and parse_date(t["due"]) else None  # noqa: E731
    by_score = lambda ts: sorted(ts, key=lambda t: (-(t["score"] or 0), t["id"]))  # noqa: E731
    top = sorted((t for t in open_tasks if t.get("top_rank")), key=lambda t: t["top_rank"])
    overdue = by_score(t for t in open_tasks if t.get("hard") and days(t) is not None and days(t) < 0)
    due_today = by_score(t for t in open_tasks if days(t) == 0)
    due_tomorrow = by_score(t for t in open_tasks if days(t) == 1)
    week = sorted((t for t in open_tasks if t.get("hard") and days(t) is not None and 1 < days(t) <= 7), key=lambda t: t["due"])
    waiting = [t for t in open_tasks if t.get("legacy_status") == "Waiting" or t.get("waiting_on")]
    rem = rows(conn, "SELECT r.fire_at, r.label, t.id, t.name FROM reminders r JOIN tasks t ON t.id = r.task_id "
                     "WHERE r.status = 'pending' AND substr(r.fire_at, 1, 10) = ? ORDER BY r.fire_at", (today.isoformat(),))
    since = (today - timedelta(days=1)).isoformat()
    closed = rows(conn, "SELECT DISTINCT t.id, t.name, t.closed_by, c.new FROM changes c JOIN batches b ON b.id = c.batch_id JOIN tasks t ON t.id = c.task_id "
                        "WHERE c.field = 'status' AND c.new IN ('\"done\"','\"dropped\"','\"faded\"','\"lapsed\"') AND b.applied_at >= ? AND b.undone_at IS NULL ORDER BY t.id", (since,))
    st = {r["key"]: r["value_json"] for r in rows(conn, "SELECT key, value_json FROM settings")}
    last = st.get("state.last_review_at", "null").strip('"')
    review_n = len(lifecycle.build_review_queue(open_tasks, today, cfg, parse_date(last), cap=10**9))

    subject = f"Factotum · {today.strftime('%a %b')} {today.day} · {len(due_today)} due today" + (f" · {len(overdue)} overdue" if overdue else "")
    T, H = [], []   # text lines, html parts

    def section(title: str, lines: list[tuple[str, str]], empty: str | None = None):
        if not lines and not empty:
            return
        T.append(f"\n{title}"); T.append("-" * len(title))
        H.append(f'<h3 style="margin:18px 0 6px;font-size:15px;color:#333">{escape(title)}</h3>')
        if not lines:
            T.append(f"  {empty}"); H.append(f'<p style="margin:0;color:#888">{escape(empty)}</p>'); return
        H.append('<ul style="margin:0;padding-left:18px">')
        for main, meta in lines:
            T.append(f"  {main}" + (f"  ({meta})" if meta else ""))
            H.append(f'<li style="margin:2px 0">{escape(main)}' + (f' <span style="color:#777;font-size:12px">{escape(meta)}</span>' if meta else '') + '</li>')
        H.append('</ul>')

    row = lambda t, meta=None: (f"Task-{t['id']} {t['name']}", meta if meta is not None else " · ".join(x for x in (t.get("project"), _fmt_due(t, today), t.get("why_now")) if x))  # noqa: E731
    if planner:
        if planner.get("ok") and planner.get("verdict"):
            T.append(planner["verdict"])
            H.append(f'<p style="margin:10px 0 14px;font-size:15px;line-height:1.4"><b>{escape(planner["verdict"])}</b></p>')
        elif not planner.get("ok"):
            note = f"The planner did not run: {planner.get('error') or 'unknown error'}"
            T.append(note); H.append(f'<p style="margin:10px 0;color:#a33;font-size:12px">{escape(note)}</p>')
    section("Top", [row(t) for t in top], "Nothing pinned. Pin up to 7 from the Today tab.")
    section("Overdue · hard deadlines", [row(t) for t in overdue])
    section("Due today", [row(t) for t in due_today], "Nothing due today.")
    section("Due tomorrow", [row(t) for t in due_tomorrow])
    section("Reminders today", [(f"{r['fire_at'][11:16]}  Task-{r['id']} {r['name']}", r["label"] or "") for r in rem])
    section("Hard deadlines this week", [row(t) for t in week])
    if events is not None or calendar_note:
        ev_lines = []
        for e in events or []:
            day = e["start"][:10]
            when = ("today" if day == today.isoformat() else "tomorrow" if day == (today + timedelta(days=1)).isoformat() else day) + ("" if e.get("all_day") else " " + e["start"][11:16])
            ev_lines.append((f"{when}  {e.get('summary') or '(no title)'}", "accepted" if e.get("self_status") == "accepted" else ""))
        section("Calendar · today and tomorrow", ev_lines, calendar_note or "Nothing on the calendar.")
    section("Closed since yesterday", [(f"Task-{c['id']} {c['name']}", f"{c['new'].strip(chr(34))} · by {c['closed_by'] or '?'}") for c in closed])
    if planner and planner.get("ok"):
        section("Done by the planner", [(a, "") for a in planner.get("applied") or []])
        section("For Review · the planner suggests", [(a, "") for a in planner.get("staged") or []])
    section("Waiting on", [row(t, t.get("waiting_on") or "") for t in waiting])
    foot = f"Review holds {review_n} item{'' if review_n == 1 else 's'}." if review_n else "Review is empty."
    base = str(C.get(cfg, "server.public_url", "")).rstrip("/")
    T.append(f"\n{foot}")
    html = (f'<!doctype html><html><body style="font:14px/1.45 -apple-system,Segoe UI,Roboto,sans-serif;color:#1a1a1a;max-width:640px;margin:0 auto;padding:16px">'
            f'<h2 style="margin:0 0 4px;font-size:18px">{escape(subject)}</h2>'
            f'<p style="margin:0 0 8px;color:#777;font-size:12px">{escape(foot)}' + (f' <a href="{escape(base)}/#review" style="color:#2a6">Open Review</a>' if base else '') + '</p>'
            + "".join(H) +
            f'<p style="margin-top:22px;color:#999;font-size:11px">Sent by Factotum. Replies are not read; decisions go through the app' + (f' at <a href="{escape(base)}" style="color:#999">{escape(base)}</a>' if base else '') + '.</p></body></html>')
    return {"subject": subject, "text": "\n".join([subject] + T), "html": html}


def send(conn: sqlite3.Connection, cfg: dict, today: date, mailer, calendar=None, planner: dict | None = None) -> str:
    """Build and send today's brief. `mailer(cfg, to, subject, text, html)`; `calendar` is the gcal
    module or None. Returns a one-line summary for the runs log."""
    to = C.get(cfg, "notify.morning_brief.to")
    if not to:
        raise RuntimeError("notify.morning_brief.to is empty")
    events, note = None, None
    if calendar is not None and C.get(cfg, "sources.calendar.enabled"):
        try:
            events = calendar.events(cfg, datetime.combine(today, datetime.min.time()), datetime.combine(today + timedelta(days=2), datetime.min.time()))
        except Exception as e:   # the brief still goes out without the calendar
            note = f"calendar unavailable: {type(e).__name__}: {str(e)[:120]}"
    b = build(conn, cfg, today, events, note, planner=planner)
    mailer(cfg, to, b["subject"], b["text"], b["html"])
    return f"sent to {to}: {b['subject']}"
