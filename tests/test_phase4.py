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
