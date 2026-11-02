"""Phase 4: the Canvas sweep, the Mailbird client and mail sweep, evidence and review flags, the
morning planner (SPEC.md §6, §7, §8.2)."""
import json

import httpx
import pytest

from factotum import batches
from factotum.db import one, rows, upsert_task

from conftest import TODAY, task


# --- T1 Canvas --------------------------------------------------------------------------------

def _item(ptype, pid, title, ctx, due, sub=None, override=None, points=5.0, url=None):
    return {"plannable_type": ptype, "plannable": {"id": pid, "title": title, "due_at": due, "points_possible": points},
            "context_name": ctx, "html_url": url or f"/courses/1/{ptype}s/{pid}", "submissions": sub or {}, "planner_override": override or {}}


CANVAS_ITEMS = [
    _item("quiz", 11, "Quiz 7: Clustering", "COMPE510-01:Machine Learning for Engineers", "2026-11-05T06:59:59Z"),                 # new → Take COMPE 510 Quiz 7
    _item("assignment", 22, "Assignment 09", "GEOG582-01:Introduction to GIS Programming", "2026-11-03T06:59:00Z", sub={"submitted": True}),   # existing open → done
    _item("assignment", 33, "Term Project Proposal", "BDA696-01:Process & Model Big Data", "2026-11-20T07:59:00Z"),                # existing, due moved
    _item("assignment", 44, "Attendance: 11/02", "GEOG582-01:Introduction to GIS Programming", "2026-11-02T17:00:00Z"),            # skipped by prefix
    _item("discussion_topic", 55, "Week 10 discussion", "College of Arts & Letters Homeroom", "2026-11-04T00:00:00Z"),            # skipped by context pattern
    _item("assignment", 66, "Dismissed thing", "COMPE510-01:Machine Learning for Engineers", "2026-11-09T00:00:00Z", override={"dismissed": True}),
    _item("assignment", 77, "Old missing lab", "GEOG582-01:Introduction to GIS Programming", "2026-10-28T06:59:00Z", sub={"missing": True}),  # new, overdue → created hard
]


def test_canvas_normalises_items_with_local_due_project_effort_and_state(cfg):
    from factotum import canvas
    items = canvas.normalize(cfg, CANVAS_ITEMS)
    by_key = {i["key"]: i for i in items}
    assert set(by_key) == {"canvas:quiz:11", "canvas:assignment:22", "canvas:assignment:33", "canvas:assignment:66", "canvas:assignment:77"}
    q = by_key["canvas:quiz:11"]
    assert q["name"] == "Take COMPE 510 Quiz 7: Clustering" and q["project"] == "COMPE 510" and q["due"] == "2026-11-04T22:59" and q["effort"] == "S" and q["state"] == "open"
    assert q["url"] == "https://sdsu.instructure.com/courses/1/quizs/11" and q["context"].startswith("COMPE510")
    assert by_key["canvas:assignment:22"]["state"] == "submitted" and by_key["canvas:assignment:22"]["name"] == "Submit GEOG 582 Assignment 09"
    assert by_key["canvas:assignment:33"]["effort"] == "L" and by_key["canvas:assignment:66"]["dismissed"] and by_key["canvas:assignment:77"]["state"] == "missing"


def test_canvas_sweep_creates_closes_and_moves_as_one_automation_batch(conn, cfg):
    from factotum import canvas
    upsert_task(conn, task(id=1, name="Submit GEOG 582 Assignment 09", source="Canvas", source_key="canvas:assignment:22", due="2026-11-02T22:59", hard=1, project="GEOG 582"))
    upsert_task(conn, task(id=2, name="Submit BDA 696 Term Project Proposal", source="Canvas", source_key="canvas:assignment:33", due="2026-11-13T23:59", hard=1, project="BDA 696"))
    upsert_task(conn, task(id=3, name="Done by hand", source="Canvas", source_key="canvas:quiz:11", status="done", completed_at="2026-10-30", closed_by="user"))
    conn.commit()
    summary = canvas.sweep(conn, cfg, canvas.normalize(cfg, CANVAS_ITEMS), TODAY)
    assert "created 1" in summary and "closed 1" in summary and "moved 1" in summary
    t1 = one(conn, "SELECT status, closed_by, completed_at FROM tasks WHERE id = 1")
    assert (t1["status"], t1["closed_by"], t1["completed_at"]) == ("done", "canvas", TODAY.isoformat())
    assert one(conn, "SELECT due FROM tasks WHERE id = 2")["due"] == "2026-11-19T23:59"
    assert one(conn, "SELECT status FROM tasks WHERE id = 3")["status"] == "done"                       # never reopened because Canvas says open
    new = one(conn, "SELECT * FROM tasks WHERE source_key = 'canvas:assignment:77'")
    assert new and new["name"] == "Submit GEOG 582 Old missing lab" and new["hard"] == 1 and new["status"] == "open" and new["source_link"].startswith("https://sdsu")
    assert new["due"] == "2026-10-27T23:59" and new["effort"] == "M" and new["area"] == "Academics" and "5 pts" in new["context"] and new["mentions"] == 1
    assert one(conn, "SELECT COUNT(*) AS n FROM tasks WHERE source_key = 'canvas:assignment:66'")["n"] == 0
    b = rows(conn, "SELECT source FROM batches")
    assert len(b) == 1 and b[0]["source"] == "canvas"
    assert canvas.sweep(conn, cfg, canvas.normalize(cfg, CANVAS_ITEMS), TODAY) is None                   # idempotent: nothing to do


def test_canvas_fetch_pages_items_and_collects_announcements(cfg):
    from factotum import canvas
    calls = []

    def handler(req):
        calls.append(str(req.url))
        p = req.url.path
        if p.endswith("/planner/items"):
            if "page=2" in str(req.url):
                return httpx.Response(200, json=CANVAS_ITEMS[3:])
            return httpx.Response(200, json=CANVAS_ITEMS[:3], headers={"Link": '<https://sdsu.instructure.com/api/v1/planner/items?page=2&per_page=100>; rel="next"'})
        if p.endswith("/courses"):
            return httpx.Response(200, json=[{"id": 9, "name": "COMPE510-01:Machine Learning for Engineers", "course_code": "COMPE510-01"}, {"id": 8, "name": "College of Arts & Letters Homeroom", "course_code": "AL"}])
        if p.endswith("/announcements"):
            return httpx.Response(200, json=[{"context_code": "course_9", "title": "DR quiz now due on Nov. 1", "message": "<p>Moved the <b>quiz</b> deadline.</p>", "posted_at": "2026-10-26T22:30:56Z", "html_url": "https://sdsu.instructure.com/courses/9/discussion_topics/1"}])
        return httpx.Response(404)
    data = canvas.fetch(cfg, "tok", TODAY, transport=httpx.MockTransport(handler))
    assert len(data["items"]) == 7 and any("page=2" in c for c in calls)
    assert data["announcements"] == [{"when": "2026-10-26T15:30", "project": "COMPE 510", "title": "DR quiz now due on Nov. 1", "text": "Moved the quiz deadline.", "url": "https://sdsu.instructure.com/courses/9/discussion_topics/1"}]
    assert all("context_codes" not in c or "course_8" not in c for c in calls)                               # skipped course never queried


def test_scheduler_runs_the_canvas_sweep_hourly_when_enabled(conn, cfg):
    from datetime import datetime
    from types import SimpleNamespace
    from factotum.scheduler import Scheduler
    cfg["notify"]["ntfy"]["topic"] = "t"
    calls = []
    fake = SimpleNamespace(sweep_job=lambda c, e, now: (calls.append(now), "created 1")[1])
    s = Scheduler(cfg, conn, push=lambda *a, **k: None, mailer=lambda *a, **k: None, calendar=None, canvas=fake)
    s.tick(datetime(2026, 11, 2, 10, 0)); s.tick(datetime(2026, 11, 2, 10, 30)); s.tick(datetime(2026, 11, 2, 11, 1))
    assert len(calls) == 2 and one(conn, "SELECT summary FROM runs WHERE kind = 'canvas_sweep'")["summary"] == "created 1"
    cfg["sources"]["canvas"]["enabled"] = False
    s.tick(datetime(2026, 11, 2, 13, 0))
    assert len(calls) == 2


# --- T2 Mailbird client -----------------------------------------------------------------------

def _sse(payload: dict) -> httpx.Response:
    return httpx.Response(200, headers={"content-type": "text/event-stream"}, text="event: message\ndata: " + json.dumps(payload) + "\n\n")


def test_mailbird_client_speaks_streamable_http_and_refuses_write_tools(cfg, monkeypatch):
    from factotum import mailbird
    monkeypatch.setenv("MAILBIRD_TOKEN", "tok")
    seen = []

    def handler(req):
        body = json.loads(req.content); seen.append((req.headers.get("authorization"), body["method"], body.get("params", {}).get("name")))
        if body["method"] == "tools/call":
            return _sse({"jsonrpc": "2.0", "id": body["id"], "result": {"content": [{"type": "text", "text": json.dumps([{"id": 1, "email": "rashe7414@sdsu.edu"}])}], "isError": False}})
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": {}})
    c = mailbird.Client(cfg, transport=httpx.MockTransport(handler))
    assert c.call("list_accounts", {}) == [{"id": 1, "email": "rashe7414@sdsu.edu"}]
    assert seen[0][0] == "Bearer tok" and seen[0][1] == "tools/call" and seen[0][2] == "list_accounts"
    with pytest.raises(mailbird.MailbirdError):
        c.call("send_message_now", {"confirm": True})
    with pytest.raises(mailbird.MailbirdError):
        c.call("trash_conversation", {"conversationId": "x"})
    assert len(seen) == 1                                                       # refused before any request
    cfg["sources"]["mail"]["url"] = "https://mail.example.com/mcp"; cfg["sources"]["mail"]["tls_verify"] = False
    with pytest.raises(mailbird.MailbirdError):
        mailbird.Client(cfg, transport=httpx.MockTransport(handler))             # verify=False only for loopback


# --- T3 the mail sweep ------------------------------------------------------------------------

class FakeMailbird:
    """A read-only Mailbird: accounts → folders → conversations → messages, as the MCP returns them."""
    def __init__(self):
        self.accounts = [{"id": 1, "email": "rashe7414@sdsu.edu"}, {"id": 2, "email": "robertashe2.0@gmail.com"}]
        self.folders = {1: [{"id": 2, "name": "Inbox"}, {"id": 7, "name": "Sent"}], 2: [{"id": 12, "name": "Inbox"}, {"id": 17, "name": "Sent"}]}
        self.convs = {2: [], 7: [], 12: [], 17: []}
        self.messages = {}
        self.calls = []

    def add(self, folder, cid, subject, when, msgs, attachments=()):
        self.convs[folder].append({"conversationId": cid, "folderId": str(folder), "subject": subject, "receivedAtUtc": when, "lastMessageId": str(msgs[-1]["id"]),
                                   "lastSender": msgs[-1]["from"].get("name") or msgs[-1]["from"]["address"], "messageCount": str(len(msgs)), "isRead": "False", "messages": []})
        for m in msgs:
            self.messages[m["id"]] = dict(m, subject=subject, body=m.get("body", "body of " + subject), bodyPreview=(m.get("body") or "")[:40], attachmentCount=len(attachments),
                                          attachments=[{"name": a} for a in attachments])
        self.convs[folder][-1]["_messages"] = [{k: v for k, v in m.items() if k != "body"} for m in msgs]

    def call(self, tool, args):
        self.calls.append((tool, args))
        if tool == "list_accounts":
            return self.accounts
        if tool == "list_folders":
            return self.folders[args["accountId"]]
        if tool == "list_conversations":
            return [{k: v for k, v in c.items() if k != "_messages"} for c in self.convs[args["folderId"]]][: args.get("limit", 50)]
        if tool == "get_conversation":
            for cs in self.convs.values():
                for c in cs:
                    if c["conversationId"] == args["conversationId"]:
                        return dict({k: v for k, v in c.items() if k != "_messages"}, messages=c["_messages"])
            raise KeyError(args)
        if tool == "get_message":
            return self.messages[int(args["messageId"])]
        raise AssertionError(tool)


def _msg(mid, from_addr, to, when, body="", name=None):
    return {"id": mid, "from": {"address": from_addr, "name": name or from_addr}, "to": [{"address": a} for a in to], "cc": [], "bcc": [], "receivedAtUtc": when, "body": body}


def test_mail_sweep_flips_waiting_closes_replies_watches_threads_and_collects_candidates(conn, cfg):
    from factotum import mail_sweep
    from factotum.db import add_link, seed_accounts
    seed_accounts(conn, cfg["sources"]["mail"]["seed_accounts"]); conn.commit()
    upsert_task(conn, task(id=1, name="Sign up for a 30-minute evaluation block", waiting_on="Erica Snider (esnider@sdsu.edu) — slot request emailed Tue Oct 13", project="ZIP Launchpad"))
    upsert_task(conn, task(id=2, name="Reply to Max about the stipend form", counterpart="max@sdsu.edu", project="SCIBER-CT", created_at="2026-10-28T00:00:00+00:00"))
    upsert_task(conn, task(id=3, name="Prepare the slides Max asked for", counterpart="max@sdsu.edu", project="SCIBER-CT", created_at="2026-10-28T00:00:00+00:00"))
    upsert_task(conn, task(id=4, name="Chase the July residuals CSV", project="Caltrans"))
    upsert_task(conn, task(id=5, name="Register for the UVA Link Lab visit", project="SCIBER-CT", hard=1, due="2026-11-10"))
    add_link(conn, 4, "mail_thread", "conv-old", label="Re: July residuals", account="rashe7414@sdsu.edu")
    conn.execute("UPDATE links SET last_seen_at = '2026-10-20T00:00:00' WHERE ref = 'conv-old'"); conn.commit()
    mb = FakeMailbird()
    mb.add(2, "conv-erica", "Re: evaluation block", "2026-11-01T18:00:00", [_msg(101, "esnider@sdsu.edu", ["rashe7414@sdsu.edu"], "2026-11-01T18:00:00", "Thursday 2pm works", "Erica Snider")])
    mb.add(7, "conv-sent-max", "Re: stipend form", "2026-11-01T17:00:00", [_msg(102, "rashe7414@sdsu.edu", ["max@sdsu.edu"], "2026-11-01T17:00:00", "Attached")])
    mb.add(2, "conv-old", "Re: July residuals", "2026-11-01T16:00:00", [_msg(103, "colleague@example.com", ["rashe7414@sdsu.edu"], "2026-11-01T16:00:00", "Here it is")])
    mb.add(2, "conv-conf", "Registration confirmed: UVA Link Lab visit", "2026-11-01T15:00:00", [_msg(104, "noreply@sdsu.edu", ["rashe7414@sdsu.edu"], "2026-11-01T15:00:00", "You are registered for the visit on Nov 10.")])
    mb.add(12, "conv-ics", "Invitation: Dentist", "2026-11-01T14:00:00", [_msg(105, "dentist@example.com", ["robertashe2.0@gmail.com"], "2026-11-01T14:00:00", "See attached")], attachments=["invite.ics"])
    mb.add(2, "conv-noise", "SDSU Athletics: tickets!", "2026-11-01T13:00:00", [_msg(106, "sdsuathletics@engage.ticketmaster.com", ["rashe7414@sdsu.edu"], "2026-11-01T13:00:00", "Buy")])
    mb.add(2, "conv-plain", "Lunch?", "2026-11-01T12:00:00", [_msg(107, "friend@example.com", ["rashe7414@sdsu.edu"], "2026-11-01T12:00:00", "Tomorrow?")])
    from datetime import datetime
    summary = mail_sweep.sweep(conn, cfg, mb, datetime(2026, 11, 1, 19, 0))
    assert "flipped 1" in summary and "closed 1" in summary and "threads 1" in summary and "candidates 3" in summary
    assert one(conn, "SELECT waiting_on FROM tasks WHERE id = 1")["waiting_on"] is None                        # Erica answered
    t2 = one(conn, "SELECT status, closed_by FROM tasks WHERE id = 2"); assert (t2["status"], t2["closed_by"]) == ("done", "mail-evidence")
    assert one(conn, "SELECT status FROM tasks WHERE id = 3")["status"] == "open"                                # not a reply task: a candidate, the planner judges
    ev = {(e["kind"], e["task_id"]): e for e in rows(conn, "SELECT * FROM evidence")}
    assert ("reply_from_waiting", 1) in ev and ev[("reply_from_waiting", 1)]["ref"] == "conv-erica" and ev[("reply_from_waiting", 1)]["account"] == "rashe7414@sdsu.edu"
    assert ("sent_to_counterpart", 2) in ev and ("sent_to_counterpart", 3) in ev
    assert ("thread_updated", 4) in ev and one(conn, "SELECT last_seen_at FROM links WHERE ref = 'conv-old'")["last_seen_at"] == "2026-11-01T16:00:00"
    assert ("confirmation", None) in ev and "registered for the visit" in ev[("confirmation", None)]["excerpt"]
    assert ("ics", None) in ev and ev[("ics", None)]["account"] == "robertashe2.0@gmail.com"
    assert not any(e["ref"] in ("conv-noise", "conv-plain") for e in ev.values())
    assert one(conn, "SELECT COUNT(*) AS n FROM links WHERE task_id = 1 AND kind = 'mail_thread' AND ref = 'conv-erica'")["n"] == 1
    assert [b["source"] for b in rows(conn, "SELECT source FROM batches")] == ["mail"]
    flags = mail_sweep.review_flags(conn)
    assert flags[1]["thread_updated"] and flags[4]["thread_updated"] and 2 not in flags
    # the next sweep sees nothing new: watermarks moved, no twins
    assert mail_sweep.sweep(conn, cfg, mb, datetime(2026, 11, 1, 19, 20)) is None
    assert one(conn, "SELECT COUNT(*) AS n FROM evidence")["n"] == 6
    assert all(t != "get_message" or a["messageId"] in (104, 105) for t, a in mb.calls)                     # bodies fetched for candidates only


def test_scheduler_runs_the_mail_sweep_on_its_interval(conn, cfg):
    from datetime import datetime
    from types import SimpleNamespace
    from factotum.scheduler import Scheduler
    cfg["notify"]["ntfy"]["topic"] = "t"
    calls = []
    s = Scheduler(cfg, conn, push=lambda *a, **k: None, mailer=lambda *a, **k: None, mail=SimpleNamespace(sweep_job=lambda c, e, now: (calls.append(now), None)[1]))
    s.tick(datetime(2026, 11, 2, 10, 0)); s.tick(datetime(2026, 11, 2, 10, 10)); s.tick(datetime(2026, 11, 2, 10, 21))
    assert len(calls) == 2 and one(conn, "SELECT COUNT(*) AS n FROM runs WHERE kind = 'mail_sweep'")["n"] == 0     # None = nothing to log
