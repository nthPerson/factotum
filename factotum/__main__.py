"""CLI entry point.

    python -m factotum init-db
    python -m factotum import-notion [--dry-run | --apply] [--new-only] [--from-json FILE]
    python -m factotum serve [--host H] [--port P]
    python -m factotum score            # print the open list with scores (sanity check)
    python -m factotum review           # print today's review queue
    python -m factotum expire [--apply] # fade / lapse what is due to expire (only rows Review has shown)
    python -m factotum tick             # run one scheduler tick now (reminders, daily jobs that are due)
    python -m factotum brief [--send]   # print today's brief (text), or send it
    python -m factotum google-auth [--manual]   # one-time Google consent (docs/SETUP.md §6)
    python -m factotum calendar-check   # print the next 7 days of events
"""
from __future__ import annotations

import argparse
import sys
from datetime import date

from . import batches, config as C, lifecycle, scoring
from .db import connect, get_settings, init_db, rows


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="factotum")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init-db")
    p = sub.add_parser("import-notion")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--dry-run", action="store_true", default=True)
    g.add_argument("--apply", action="store_true")
    p.add_argument("--new-only", action="store_true", help="only rows whose Task ID is above the store's highest; never overwrites")
    p.add_argument("--from-json")
    p = sub.add_parser("serve")
    p.add_argument("--host")
    p.add_argument("--port", type=int)
    p.add_argument("--no-scheduler", action="store_true", help="API and UI only (a second instance next to the service)")
    sub.add_parser("tick")
    p = sub.add_parser("brief")
    p.add_argument("--send", action="store_true")
    p = sub.add_parser("google-auth")
    p.add_argument("--manual", action="store_true", help="paste the redirect URL instead of a local callback")
    sub.add_parser("calendar-check")
    sub.add_parser("score")
    sub.add_parser("review")
    p = sub.add_parser("expire")
    p.add_argument("--apply", action="store_true")
    a = ap.parse_args(argv)

    cfg = C.load()
    conn = connect(C.db_path())
    init_db(conn)
    ecfg = C.effective(cfg, get_settings(conn))
    today = date.today()

    if a.cmd == "init-db":
        print(f"database ready at {C.db_path()}")
    elif a.cmd == "import-notion":
        from .importers.notion_tasks import run
        run(conn, ecfg, apply=a.apply, from_json=a.from_json, new_only=a.new_only, today=today)
    elif a.cmd == "serve":
        import uvicorn
        from .api import create_app
        app = create_app(cfg)
        if not a.no_scheduler:
            from . import brief, gcal, notify
            from .scheduler import Scheduler
            # its own connection: the API holds one, and SQLite serialises writers with busy_timeout
            Scheduler(cfg, connect(C.db_path()), push=notify.publish, mailer=brief.smtp_mailer, calendar=gcal).start()
        uvicorn.run(app, host=a.host or C.get(cfg, "server.host", "127.0.0.1"), port=a.port or int(C.get(cfg, "server.port", 8750)))
    elif a.cmd == "tick":
        from . import brief, gcal, notify
        from .scheduler import Scheduler
        did = Scheduler(cfg, conn, push=notify.publish, mailer=brief.smtp_mailer, calendar=gcal).tick()
        print("\n".join(did) if did else "nothing due")
    elif a.cmd == "brief":
        from . import brief, gcal
        if a.send:
            print(brief.send(conn, ecfg, today, brief.smtp_mailer, gcal))
        else:
            print(brief.build(conn, ecfg, today)["text"])
    elif a.cmd == "google-auth":
        from . import gcal
        path = gcal.authorize(ecfg, manual=a.manual)
        print(f"token written to {path}")
        for e in gcal.events(ecfg, __import__("datetime").datetime.now(), __import__("datetime").datetime.now() + __import__("datetime").timedelta(days=7))[:10]:
            print(f"  {e['start']:<16} {e['summary'] or '(no title)'}{'  · accepted' if e['self_status'] == 'accepted' else ''}")
    elif a.cmd == "calendar-check":
        from datetime import datetime, timedelta
        from . import gcal
        evs = gcal.events(ecfg, datetime.now(), datetime.now() + timedelta(days=7))
        for e in evs:
            print(f"  {e['start']:<16} {e['summary'] or '(no title)'}{'  · accepted' if e['self_status'] == 'accepted' else ''}")
        print(f"{len(evs)} events in the next 7 days")
    elif a.cmd == "score":
        for t in rows(conn, "SELECT * FROM tasks WHERE status = 'open' ORDER BY COALESCE(due,'9999'), id"):
            s, pr, why = scoring.score(t, today, ecfg)
            print(f"{s:3d} {pr} Task-{t['id']:<4} {(t['project'] or '-')[:14]:<14} {t['name'][:60]:<60} {'; '.join(why)}")
    elif a.cmd == "review":
        last = get_settings(conn).get("state.last_review_at")
        q = lifecycle.build_review_queue(rows(conn, "SELECT * FROM tasks WHERE status = 'open'"), today, ecfg, None if not last else date.fromisoformat(last))
        for i in q:
            t = i["task"]
            print(f"{i['primary']:16s} Task-{t['id']:<4} {t['name'][:70]}")
        print(f"{len(q)} items")
    elif a.cmd == "expire":
        due = lifecycle.expire(rows(conn, "SELECT * FROM tasks WHERE status = 'open'"), today, ecfg)
        for tid, st, why in due:
            print(f"{st:6s} Task-{tid}: {why}")
        if a.apply and due:
            res = batches.apply(conn, [{"task_id": tid, "op": "fade" if st == "faded" else "lapse"} for tid, st, _ in due], "automation", today)
            print(res)
        else:
            print(f"{len(due)} would expire (add --apply)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
