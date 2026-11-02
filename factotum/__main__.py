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
import json
import sys
from datetime import date

from . import batches, config as C, lifecycle, scoring
from .db import connect, get_settings, init_db, rows


SYSTEMD_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"


def _binary_note(name: str) -> str:
    """Where a configured binary resolves, and whether the service (systemd's PATH) would find it."""
    import shutil
    here, svc = shutil.which(name), shutil.which(name, path=SYSTEMD_PATH)
    if not here:
        return f"{name!r}: NOT FOUND from this shell"
    if "/" in name or svc:
        return f"{name!r} → {here}"
    return f"{name!r} → {here} here, but NOT on the service's PATH: set the absolute path in config"


def _start_plaud_trigger(cfg: dict) -> None:
    """The IMAP IDLE thread (SPEC.md §6 "Plaud"): its own connection, the effective config re-read per mail."""
    from . import plaud_trigger, sync_file
    tconn = connect(C.db_path())
    if not C.get(C.effective(cfg, get_settings(tconn)), "sources.plaud.enabled", False):
        print("plaud trigger: sources.plaud.enabled is off", flush=True)
        return
    imap = plaud_trigger.imap_settings()
    if not imap:
        print("plaud trigger: IMAP_USER / IMAP_PASSWORD not set; not started", flush=True)
        return

    def handle(subject: str, received: str) -> dict:
        ecfg = C.effective(cfg, get_settings(tconn))
        res = sync_file.run(tconn, ecfg, subject=subject, received=received)
        print("sync-file:", res.get("line") or res.get("error"), flush=True)
        return res
    plaud_trigger.PlaudTrigger(cfg, tconn, handle, account=imap["user"]).start(imap)


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
    p = sub.add_parser("sync-file", help="sync one Plaud recording now (phase 3)")
    p.add_argument("plaud_id", nargs="?", help="recording id (plaud today / plaud search); or give --subject")
    p.add_argument("--subject", help="the Plaud mail subject, located within a day of --received (default today)")
    p.add_argument("--received", help="ISO date-time the mail arrived")
    p.add_argument("--dry-run", action="store_true", help="run Claude and print the JSON; write nothing")
    sub.add_parser("claude-check", help="one tiny headless Claude call: is the login alive?")
    sub.add_parser("plaud-check", help="the Plaud CLI, its login, today's recordings, and the trigger mailbox")
    sub.add_parser("mail-check", help="Mailbird's MCP from here: accounts it has versus the monitored ones")
    sub.add_parser("canvas-check", help="Canvas: the token, the items in the window, what a sweep would do")
    p = sub.add_parser("sweep", help="run one sweep now")
    p.add_argument("which", choices=["mail", "canvas", "calendar"])
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
            from . import brief, canvas, gcal, mail_sweep, notify
            from .scheduler import Scheduler
            # its own connection: the API holds one, and SQLite serialises writers with busy_timeout
            Scheduler(cfg, connect(C.db_path()), push=notify.publish, mailer=brief.smtp_mailer, calendar=gcal, canvas=canvas, mail=mail_sweep).start()
            _start_plaud_trigger(cfg)
        uvicorn.run(app, host=a.host or C.get(cfg, "server.host", "127.0.0.1"), port=a.port or int(C.get(cfg, "server.port", 8750)))
    elif a.cmd == "tick":
        from . import brief, gcal, notify
        from . import canvas, mail_sweep
        from .scheduler import Scheduler
        did = Scheduler(cfg, conn, push=notify.publish, mailer=brief.smtp_mailer, calendar=gcal, canvas=canvas, mail=mail_sweep).tick()
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
    elif a.cmd == "sync-file":
        from . import sync_file
        if not a.plaud_id and not a.subject:
            raise SystemExit("give a recording id or --subject")
        res = sync_file.run(conn, ecfg, subject=a.subject, received=a.received, plaud_id=a.plaud_id, dry_run=a.dry_run, today=today)
        if not res["ok"]:
            raise SystemExit(f"failed: {res['error']}")
        if a.dry_run:
            print(json.dumps(res["data"], indent=1, ensure_ascii=False))
            print(f"\n(dry run · ${res['cost_usd']:.2f} · prompt and result kept under data/runs/{res['rec']['id']}/)")
        else:
            print(res["line"], f"· ${res['cost_usd']:.2f}")
    elif a.cmd == "claude-check":
        from .claude import runner
        r = runner.run(ecfg, "check", 'Reply with exactly the JSON object {"ok": true} and nothing else.', timeout_s=120)
        print("claude.binary", _binary_note(str(C.get(ecfg, "claude.binary", "claude"))))
        if r.ok and r.data == {"ok": True}:
            print(f"claude is logged in (model {C.get(ecfg, 'claude.models.check', 'haiku')}, {r.duration_ms} ms, ${r.cost_usd:.4f})")
        else:
            raise SystemExit(f"claude check failed: {r.error}\nRun `claude` once as this user and log in, then try again.")
    elif a.cmd == "plaud-check":
        import subprocess
        from . import plaud, plaud_trigger
        print("sources.plaud.cli", _binary_note(str(C.get(ecfg, "sources.plaud.cli", "plaud"))))
        try:
            out = subprocess.run([C.get(ecfg, "sources.plaud.cli", "plaud"), "today"], capture_output=True, text=True, timeout=90)
            print(out.stdout.strip() or out.stderr.strip())
        except FileNotFoundError:
            raise SystemExit("plaud CLI not on PATH (docs/SETUP.md §2)")
        imap = plaud_trigger.imap_settings()
        if not imap:
            raise SystemExit("IMAP_USER / IMAP_PASSWORD not set: the trigger cannot start (docs/SETUP.md §8)")
        from imapclient import IMAPClient
        with IMAPClient(imap["host"], port=int(imap["port"]), use_uid=True, ssl=True) as server:
            server.login(imap["user"], imap["password"])
            server.select_folder(imap["folder"], readonly=True)
            f = C.get(ecfg, "sources.plaud.from_filter", "plaud.ai")
            n = len(server.search(["FROM", f])); u = len(server.search(["UNSEEN", "FROM", f]))
            print(f"trigger mailbox {imap['user']}/{imap['folder']}: {n} mails from {f}, {u} unseen (an unseen one is processed at the next service start)")
    elif a.cmd == "mail-check":
        import json as _json
        from .mailbird import Client
        client = Client(ecfg)
        have = {str(x.get("email") or "").lower(): x for x in client.call("list_accounts", {})}
        print(f"Mailbird at {client.url}: {len(have)} accounts: " + ", ".join(sorted(have)))
        for acc in rows(conn, "SELECT * FROM accounts"):
            roles = _json.loads(acc.get("roles_json") or "[]")
            state = "ok" if acc["address"].lower() in have else "NOT IN MAILBIRD"
            print(f"  {acc['address']:32} {'on ' if acc['enabled'] else 'off'} {','.join(roles):28} {state}")
        client.close()
    elif a.cmd == "canvas-check":
        import os
        from . import canvas
        if not os.environ.get("CANVAS_TOKEN"):
            raise SystemExit("CANVAS_TOKEN is not set in .env")
        data = canvas.fetch(ecfg, os.environ["CANVAS_TOKEN"], today)
        items = canvas.normalize(ecfg, data["items"])
        print(f"{len(data['items'])} items in the window, {len(items)} after filters, {len(data['announcements'])} announcements")
        for it in items:
            row = conn.execute("SELECT id, status FROM tasks WHERE source_key = ?", (it["key"],)).fetchone()
            print(f"  {it['due'] or 'undated':16} {it['state']:15} {it['project']:12} {it['name'][:50]:50} {'Task-%d %s' % (row[0], row[1]) if row else 'NEW'}")
    elif a.cmd == "sweep":
        from datetime import datetime as _dt
        from . import canvas, gcal, mail_sweep
        now = _dt.now()
        mod = {"mail": mail_sweep, "canvas": canvas, "calendar": gcal}[a.which]
        print(mod.sweep_job(conn, ecfg, now) or "nothing to do")
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
