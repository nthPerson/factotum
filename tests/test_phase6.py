"""Phase 6: the Notion tasks mirror, staged proposals from skills, the HTTP MCP route, Slack and
Discord read tools (SPEC.md §7, §14.6, §15; §6 inputs)."""
import json

import httpx
import pytest

from factotum.db import one, rows, upsert_task

from conftest import TODAY, task


# --- the Notion tasks mirror -------------------------------------------------------------------

SCHEMA = {"properties": {
    "Name": {"type": "title"}, "Task ID": {"type": "unique_id"}, "Status": {"type": "select"}, "Project": {"type": "select"}, "Area": {"type": "select"},
    "Due": {"type": "date"}, "Hard deadline": {"type": "checkbox"}, "Effort": {"type": "select"}, "Source": {"type": "select"}, "Source Key": {"type": "rich_text"},
    "Source Link": {"type": "url"}, "Context": {"type": "rich_text"}, "Why now": {"type": "rich_text"}, "Score": {"type": "number"}, "Priority": {"type": "select"},
    "Mentions": {"type": "number"}, "Completed": {"type": "date"}, "Waiting On": {"type": "rich_text"}, "Calendar Event": {"type": "url"}, "Notes": {"type": "relation"},
    "Snoozed Until": {"type": "date"}, "Manual Priority": {"type": "select"}, "Created": {"type": "created_time"}, "Edited": {"type": "last_edited_time"}}}


class FakeNotion:
    """Records every request; answers the schema, page creates and updates."""
    def __init__(self, fail_update_for=()):
        self.calls, self.n, self.fail = [], 0, set(fail_update_for)

    def transport(self):
        def handler(req: httpx.Request):
            body = json.loads(req.content) if req.content else {}
            self.calls.append((req.method, req.url.path, body, req.headers.get("Notion-Version")))
            if req.method == "GET" and req.url.path.startswith("/v1/data_sources/"):
                return httpx.Response(200, json=SCHEMA)
            if req.method == "POST" and req.url.path == "/v1/pages":
                self.n += 1
                return httpx.Response(200, json={"id": f"page-{self.n}", "url": f"https://www.notion.so/page-{self.n}"})
            if req.method == "PATCH" and req.url.path.startswith("/v1/pages/"):
                pid = req.url.path.rsplit("/", 1)[-1]
                if pid in self.fail:
                    return httpx.Response(400, json={"message": "Status is expected to be select."})
                return httpx.Response(200, json={"id": pid})
            return httpx.Response(404, json={"message": "nope"})
        return httpx.MockTransport(handler)

    def pages(self, method):
        return [(p, b) for m, p, b, _ in self.calls if m == method and "/pages" in p]


def test_mirror_properties_follow_the_schema_and_the_config_map(cfg, conn, monkeypatch):
    from factotum import notion_mirror as NM
    monkeypatch.setenv("NOTION_TOKEN", "t")
    upsert_task(conn, task(id=42, name="Reply to Max", project="COMPE 510", area="Academics", due="2026-11-05T14:00", due_is_datetime=1, hard=1, effort="S",
                           source="Chat", source_key="chat:1:0", source_link="https://x", context="ctx", why_now="soon", waiting_on="Max", manual_priority="P1",
                           completed_at=None, snoozed_until="2026-11-04", calendar_event="https://cal/1"))
    conn.execute("INSERT INTO links (task_id, kind, ref, label, created_at) VALUES (42, 'plaud_note', 'abcdef1234567890abcdef1234567890', 'rec', 'now')")
    conn.execute("INSERT INTO links (task_id, kind, ref, label, created_at) VALUES (42, 'note_page', 'https://www.notion.so/Title-0123456789abcdef0123456789abcdef', 'note', 'now')")
    conn.execute("INSERT INTO links (task_id, kind, ref, label, created_at) VALUES (42, 'mail_thread', 'c1', 'mail', 'now')")
    conn.commit()
    schema = NM.schema(cfg, transport=FakeNotion().transport())
    assert schema["Status"] == "select" and schema["Task ID"] == "unique_id"
    t = one(conn, "SELECT * FROM tasks WHERE id = 42")
    p = NM.properties(cfg, t, schema, NM.note_ids(conn, 42), TODAY)
    assert p["Name"] == {"title": [{"type": "text", "text": {"content": "Reply to Max"}}]}
    assert p["Status"] == {"select": {"name": "Next"}} and p["Project"] == {"select": {"name": "COMPE 510"}} and p["Effort"] == {"select": {"name": "S"}}
    assert p["Due"] == {"date": {"start": "2026-11-05T14:00"}} and p["Hard deadline"] == {"checkbox": True} and p["Completed"] == {"date": None}
    assert p["Snoozed Until"] == {"date": {"start": "2026-11-04"}} and p["Manual Priority"] == {"select": {"name": "P1"}}
    assert p["Score"]["number"] > 0 and p["Priority"]["select"]["name"].startswith("P") and p["Mentions"] == {"number": 1}
    assert p["Source Link"] == {"url": "https://x"} and p["Calendar Event"] == {"url": "https://cal/1"} and p["Context"]["rich_text"][0]["text"]["content"] == "ctx"
    assert p["Notes"] == {"relation": [{"id": "abcdef1234567890abcdef1234567890"}, {"id": "0123456789abcdef0123456789abcdef"}]}
    assert "Task ID" not in p and "Created" not in p and "Edited" not in p           # read-only in Notion
    # a dropped-by-automation row maps through status_map; a name is capped; missing text clears the column
    upsert_task(conn, task(id=43, name="x" * 3000, status="faded", context=None)); conn.commit()
    p2 = NM.properties(cfg, one(conn, "SELECT * FROM tasks WHERE id = 43"), schema, [], TODAY)
    assert p2["Status"] == {"select": {"name": "Dropped"}} and len(p2["Name"]["title"][0]["text"]["content"]) == 2000 and p2["Context"] == {"rich_text": []}
    assert p2["Notes"] == {"relation": []}


def test_mirror_sweep_creates_updates_since_the_watermark_caps_and_records_page_ids(cfg, conn, monkeypatch):
    from factotum import notion_mirror as NM
    monkeypatch.setenv("NOTION_TOKEN", "t")
    upsert_task(conn, task(id=1, name="Old mirrored", notion_page_id="page-old", edited_at="2026-10-01T00:00:00+00:00", mirrored_at="2026-11-01T00:00:00+00:00"))
    upsert_task(conn, task(id=2, name="Edited since", notion_page_id="page-2", edited_at="2026-11-02T10:00:00+00:00", mirrored_at="2026-11-01T00:00:00+00:00"))
    upsert_task(conn, task(id=3, name="Never mirrored", edited_at="2026-10-05T00:00:00+00:00"))
    upsert_task(conn, task(id=4, name="Also new", edited_at="2026-11-02T11:00:00+00:00"))
    conn.commit()
    fake = FakeNotion()
    dry = NM.sweep(conn, cfg, dry_run=True, transport=fake.transport())
    assert dry["pending"] == 3 and fake.pages("POST") == [] and fake.pages("PATCH") == []
    res = NM.sweep(conn, cfg, cap=2, transport=fake.transport())
    # creates go first, in id order (Notion's Task ID counter keeps pace with Factotum's ids); the cap leaves the edit for the next pass
    assert res["created"] == 2 and res["updated"] == 0 and res["pending"] == 1
    assert one(conn, "SELECT notion_page_id FROM tasks WHERE id = 3")["notion_page_id"] == "page-1" and one(conn, "SELECT notion_page_id FROM tasks WHERE id = 4")["notion_page_id"] == "page-2"
    assert one(conn, "SELECT edited_at FROM tasks WHERE id = 3")["edited_at"] == "2026-10-05T00:00:00+00:00"     # storing the page id is not an edit
    assert fake.pages("PATCH") == [] and one(conn, "SELECT mirrored_at FROM tasks WHERE id = 3")["mirrored_at"] == "2026-10-05T00:00:00+00:00"
    create_body = fake.pages("POST")[0][1]
    assert create_body["parent"] == {"type": "data_source_id", "data_source_id": cfg["sinks"]["notion_tasks_mirror"]["data_source_id"]} and create_body["properties"]["Name"]["title"][0]["text"]["content"] == "Never mirrored"
    assert sum(1 for m, p, _, _ in fake.calls if m == "GET") == 1                                   # the schema once per sweep
    res2 = NM.sweep(conn, cfg, transport=fake.transport())
    assert res2["updated"] == 1 and res2["created"] == 0 and res2["pending"] == 0 and [p for p, _ in fake.pages("PATCH")] == ["/v1/pages/page-2"]
    # an edit after the push is pending again; a refused row is stamped and reported, not retried every pass
    conn.execute("UPDATE tasks SET edited_at = '2026-11-02T12:00:00+00:00' WHERE id = 1"); conn.commit()
    bad = FakeNotion(fail_update_for={"page-old"})
    res3 = NM.sweep(conn, cfg, transport=bad.transport())
    assert res3["failed"] == ["Task-1: 400 Status is expected to be select."] and res3["pending"] == 0
    assert NM.sweep(conn, cfg, transport=fake.transport())["pending"] == 0
    # --all re-pushes every row
    fake2 = FakeNotion()
    res4 = NM.sweep(conn, cfg, all_rows=True, transport=fake2.transport())
    assert res4["updated"] == 4 and res4["failed"] == [] and one(conn, "SELECT notion_page_id FROM tasks WHERE id = 1")["notion_page_id"] == "page-old"


def test_scheduler_runs_the_mirror_on_its_interval_when_the_sink_is_on(conn, cfg):
    from datetime import datetime
    from types import SimpleNamespace
    from factotum.scheduler import Scheduler
    calls = []
    s = Scheduler(cfg, conn, push=lambda *a, **k: None, mailer=lambda *a, **k: None, mirror=SimpleNamespace(sweep_job=lambda c, e, now: (calls.append(now), "2 rows")[1]))
    s.tick(datetime(2026, 11, 2, 10, 0)); s.tick(datetime(2026, 11, 2, 10, 5)); s.tick(datetime(2026, 11, 2, 10, 11))
    assert len(calls) == 2 and one(conn, "SELECT summary FROM runs WHERE kind = 'notion_mirror'")["summary"] == "2 rows"
    cfg["sinks"]["notion_tasks_mirror"]["enabled"] = False
    s.tick(datetime(2026, 11, 2, 10, 30))
    assert len(calls) == 2
