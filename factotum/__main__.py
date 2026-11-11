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
    python -m factotum chat "text" [--attach 388 23] [--thread N] [--canned search_mail|draft_reply]   # one chat turn from the terminal
    python -m factotum effective [KEY ...] [--as-before]   # each switch's value and origin; read-only, no secrets
    python -m factotum sweep mail|canvas|calendar|slack|links   # one sweep now
    python -m factotum links-check      # what the link detectors would suggest now; read-only
    python -m factotum chat-check       # the MCP server over a pipe, then one tiny Claude run that calls a tool through it
    python -m factotum ripple <task_id> [--dry-run]   # one ripple now from a task (a real Claude run); --dry-run writes nothing
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date

from . import batches, config as C, lifecycle, relations
from .db import connect, get_settings, init_db, rows


SYSTEMD_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"


def _effective(keys: list[str], as_before: bool) -> int:
    """Print what each Settings key is and where it comes from. Reads the config file and the database
    (opened read-only, never created); prints no secret."""
    import hashlib
    import sqlite3
    cfg = C.load()
    path = C.db_path()
    exists = path.exists()
    overrides: dict = {}
    if exists:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            overrides = get_settings(conn)
        finally:
            conn.close()
    print(f"config file: {cfg['_path']}" + ("  (starter settings)" if cfg.get("_starter") else ""))
    print(f"database:    {path}" + ("" if exists else "  (none yet)"))
    defaults = C.OLD_DEFAULTS if as_before else C.DEFAULTS
    for key in keys or C.REPORT_KEYS:
        value, origin = C.explain(cfg, overrides, key, defaults)
        if key in C.MASKED:
            shown = "set #" + hashlib.sha256(json.dumps(value).encode()).hexdigest()[:8] if value else "empty"
        else:
            shown = json.dumps(value, ensure_ascii=False)
        print(f"{key:<42} {shown:<26} {origin}")
    return 0


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
    p = sub.add_parser("add", help="add a task by hand from one line, as the New task line does: #project ~effort ! a date, after N, under N; --dry-run prints how it would be filed")
    p.add_argument("text")
    p.add_argument("--dry-run", action="store_true")
    p = sub.add_parser("find", help="find any task, open or closed, by number or words")
    p.add_argument("q")
    sub.add_parser("mcp", help="the MCP server a chat turn talks to, over stdio (started by the runner; `chat-check` exercises it)")
    p = sub.add_parser("chat", help="one chat turn from the terminal: a real Claude run (~$0.20), stored like a turn from the UI")
    p.add_argument("text", nargs="?", default="")
    p.add_argument("--attach", nargs="*", type=int, default=[])
    p.add_argument("--thread", type=int, default=None)
    p.add_argument("--canned", choices=["search_mail", "draft_reply"], default=None)
    sub.add_parser("chat-check", help="the MCP server over a pipe, then one tiny Claude run that calls tasks_query through it")
    sub.add_parser("claude-check", help="one tiny headless Claude call: is the login alive?")
    sub.add_parser("plaud-check", help="the Plaud CLI, its login, today's recordings, and the trigger mailbox")
    sub.add_parser("mail-check", help="Mailbird's MCP from here: accounts it has versus the monitored ones")
    sub.add_parser("canvas-check", help="Canvas: the token, the items in the window, what a sweep would do")
    p = sub.add_parser("planner", help="the morning planner now: one Claude run, then the brief's verdict and proposals")
    p.add_argument("--dry-run", action="store_true", help="run Claude and print the JSON; write nothing")
    p.add_argument("--send", action="store_true", help="also send the brief with the verdict")
    p = sub.add_parser("file-task", help="one filing run for a task (a real Claude run): its project, effort, a likely link and twin as suggestions; --dry-run prints the JSON and stores nothing")
    p.add_argument("task_id", type=int)
    p.add_argument("--dry-run", action="store_true")
    p = sub.add_parser("ripple", help="one ripple now from a task (a real Claude run): --dry-run prints what it would do and writes nothing")
    p.add_argument("task_id", type=int)
    p.add_argument("--dry-run", action="store_true")
    p = sub.add_parser("sweep", help="run one sweep now")
    p.add_argument("which", choices=["mail", "canvas", "calendar", "slack", "links"])
    sub.add_parser("links-check", help="what the link detectors would suggest now; read-only")
    sub.add_parser("slack-check", help="each configured Slack workspace: token present, auth.test, how many channels it sees")
    sub.add_parser("discord-check", help="the Discord bot: who it is, the servers it is in vs the config, text channels per configured server")
    p = sub.add_parser("mirror", help="push task rows to the Notion Tasks database (one-way); default: rows edited since the last pass")
    p.add_argument("--all", action="store_true", help="every row (the first pass after enabling the mirror)")
    p.add_argument("--task", nargs="+", type=int, metavar="ID", help="re-push these rows only (a row Notion refused, once the cause is fixed)")
    p.add_argument("--dry-run", action="store_true", help="count what would be pushed, write nothing")
    p = sub.add_parser("connectors", help="the OAuth connectors (claude.ai, Claude Desktop) that registered against /mcp; --revoke CLIENT_ID")
    p.add_argument("--revoke", metavar="CLIENT_ID")
    p.add_argument("--arm", action="store_true", help="allow a never-granted connector to be approved for the next 10 minutes (the Settings button)")
    p = sub.add_parser("effective", help="what each switch is and where it comes from (Settings, the config file, the code's default); read-only")
    p.add_argument("keys", nargs="*")
    p.add_argument("--as-before", action="store_true", help="absent keys as the code before 2026-11-10 read them; diff against the plain output when deploying")
    sub.add_parser("score")
    sub.add_parser("review")
    p = sub.add_parser("expire")
    p.add_argument("--apply", action="store_true")
    a = ap.parse_args(argv)
    if a.cmd == "effective":
        return _effective(a.keys, a.as_before)

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
            from . import brief, canvas, gcal, mail_sweep, mail_triage, notify, notion_mirror, planner, ripple, slack_sweep, summary
            from .scheduler import Scheduler
            # its own connection: the API holds one, and SQLite serialises writers with busy_timeout
            sched = Scheduler(cfg, connect(C.db_path()), push=notify.publish, mailer=brief.smtp_mailer, calendar=gcal, canvas=canvas, mail=mail_sweep, planner=planner,
                              mirror=notion_mirror, slack=slack_sweep, summary=summary, triage=mail_triage, ripple=ripple)
            sched.start()
            app.state.scheduler = sched          # the Today tab's Refresh asks this thread; with --no-scheduler the button is off
            _start_plaud_trigger(cfg)
        uvicorn.run(app, host=a.host or C.get(cfg, "server.host", "127.0.0.1"), port=a.port or int(C.get(cfg, "server.port", 8750)))
    elif a.cmd == "tick":
        from . import brief, gcal, notify
        from . import canvas, mail_sweep, notion_mirror, planner, ripple, slack_sweep
        from .scheduler import Scheduler
        did = Scheduler(cfg, conn, push=notify.publish, mailer=brief.smtp_mailer, calendar=gcal, canvas=canvas, mail=mail_sweep, planner=planner, mirror=notion_mirror,
                        slack=slack_sweep, ripple=ripple).tick()
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
    elif a.cmd == "add":
        from . import by_hand
        if a.dry_run:
            print(json.dumps(by_hand.preview(conn, ecfg, a.text, today), indent=1, ensure_ascii=False))
        else:
            try:
                res = by_hand.add(conn, ecfg, text=a.text, today=today)
            except ValueError as e:
                raise SystemExit(str(e))
            print(f"Task-{res['task_id']} {res['name']}")
            for r in res["refused"]:
                print(f"  {r['words']}: {r['why']}")
    elif a.cmd == "find":
        from . import by_hand
        for r in by_hand.search(conn, a.q)["results"]:
            when = f" {r['completed_at'][:10]}" if r["completed_at"] else ""
            merged = f" merged into Task-{r['merged_into']}" if r["merged_into"] else ""
            print(f"Task-{r['id']}  {r['status']}{when}{merged}  {r['name']}")
    elif a.cmd == "mcp":
        from . import mcp_server
        mcp_server.serve_stdio()            # stdout is the protocol from here on; logs go to stderr
    elif a.cmd == "chat":
        from . import chat
        if not a.text and not a.canned:
            raise SystemExit("give a message, or --canned search_mail|draft_reply with --attach <task id>")
        tid = a.thread or chat.new_thread(conn)

        def show(ev):
            if ev.get("type") == "status":
                print("  …", ev.get("text"), flush=True)
        res = chat.turn(conn, ecfg, tid, a.text, a.attach, today=today, canned=a.canned, on_event=show)
        if not res["ok"]:
            raise SystemExit(f"chat turn failed: {res['error']}")
        print(f"\n{res['reply_md']}\n")
        for i, pr in enumerate(res["proposals"]):
            print(f"  proposal {i}: {pr['label']}" + (" (applied: link)" if pr.get("applied") else "") + (f" · {json.dumps(pr['fields'])}" if pr.get("fields") else ""))
        for d in res["drafts"]:
            print(f"  draft in Mailbird: {d.get('kind')} · {d.get('subject') or ''} · {d.get('account') or ''} (message {d.get('draft_message_id')})")
        for r in res["rejected"]:
            print(f"  rejected: {r}")
        print(f"\n(thread {tid} · ${res['cost_usd']:.2f} · stage proposals in the Chat tab; prompt and result under data/runs/chat/)")
    elif a.cmd == "chat-check":
        import io
        from . import chat, mcp_server
        from .claude import runner
        out = io.StringIO()
        ctx = mcp_server.Context(connect(C.db_path()), ecfg)
        mcp_server.serve(ctx, io.StringIO('{"jsonrpc":"2.0","id":1,"method":"tools/list"}\n'), out)
        names = [t["name"] for t in json.loads(out.getvalue())["result"]["tools"]]
        print(f"factotum mcp over a pipe: {len(names)} tools ({', '.join(names)})")
        try:
            mb = ctx.mail_client().call("list_accounts", {})
            print(f"Mailbird: {len(mb)} accounts reachable")
        except Exception as e:  # noqa: BLE001
            print(f"Mailbird: not reachable from here ({str(e)[:120]}); mail tools will report that to the model")
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            r = runner.run(ecfg, "check", 'Call tasks_query with status="open" and limit=1, then reply with only the JSON {"ok": true, "n": <the number of tasks returned>}. '
                           'If the tool is unavailable, reply {"ok": false}.', allowed_tools=chat.allowed_tools(), mcp_servers=chat.mcp_servers(ecfg, td), tools="",
                           on_event=lambda ev: None, workdir=td, timeout_s=120)
        if r.ok and (r.data or {}).get("ok"):
            print(f"Claude reached the server through the runner ({r.duration_ms} ms, ${r.cost_usd:.4f}); chat is ready")
        else:
            raise SystemExit(f"chat check failed: {r.error or r.data}\nIs the service's claude.binary absolute, and is `claude` logged in (claude-check)?")
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
    elif a.cmd == "slack-check":
        import os
        from . import slack
        ws = slack.workspaces(ecfg)
        if not ws:
            raise SystemExit("no workspaces under sources.slack.workspaces (config/factotum.yaml; docs/SETUP.md §12)")
        print(f"Slack {'on' if C.get(ecfg, 'sources.slack.enabled') else 'OFF (Settings → Slack)'}; {len(ws)} workspace(s) configured")
        for w in ws:
            if not w["ok"]:
                print(f"  {w['name']:20} ✗ {w['why']}"); continue
            client = slack.Client(os.environ[w["token_env"]])
            try:
                me = client.call("auth.test")
                chans = client.call("conversations.list", {"types": "public_channel,private_channel", "exclude_archived": "true", "limit": 200}).get("channels") or []
                try:
                    client.call("search.messages", {"query": "a", "count": 1}); search = "search ok (user token)"
                except slack.SlackError as e:
                    search = "no search: " + str(e).split(": ", 1)[-1][:80]
                print(f"  {w['name']:20} ✓ team {me.get('team')} as {me.get('user')} · {len(chans)} channels · {search}")
            except slack.SlackError as e:
                print(f"  {w['name']:20} ✗ {e}")
            finally:
                client.close()
    elif a.cmd == "discord-check":
        from . import discord
        gs = discord.guilds(ecfg)
        client = discord.Client(discord.token())
        try:
            me = client.get("/users/@me")
            mine = {g["id"]: g["name"] for g in client.get("/users/@me/guilds")}
            print(f"Discord {'on' if C.get(ecfg, 'sources.discord.enabled') else 'OFF (Settings → Discord)'}; bot {me.get('username')} is in {len(mine)} server(s); {len(gs)} configured")
            for g in gs:
                if g["id"] not in mine:
                    print(f"  {g['name']:20} ✗ the bot is not in server {g['id']} (invite it: docs/SETUP.md §13)"); continue
                chans = [c for c in client.get(f"/guilds/{g['id']}/channels") if discord.is_text(c)]
                probe = ""
                if chans:
                    try:
                        ms = client.get(f"/channels/{chans[0]['id']}/messages", {"limit": 1})
                        probe = " · content " + ("readable" if (ms and ms[0].get("content")) else "EMPTY: enable the Message Content intent") if ms else " · no messages in #" + str(chans[0].get("name"))
                    except discord.DiscordError as e:
                        probe = f" · cannot read #{chans[0].get('name')}: {e}"
                print(f"  {g['name']:20} ✓ as {mine[g['id']]} · {len(chans)} text channels{probe}")
            for gid, name in mine.items():
                if gid not in {g["id"] for g in gs}:
                    print(f"  (bot is also in {name!r}, id {gid}, not in the config)")
        finally:
            client.close()
    elif a.cmd == "mirror":
        from . import notion_mirror
        if not C.get(ecfg, "sinks.notion_tasks_mirror.enabled") and not a.dry_run:
            print("the mirror is off (Settings → Notion tasks mirror); pushing anyway because you asked")
        total = {"created": 0, "updated": 0, "failed": []}
        if a.task and not a.dry_run:
            fresh = [r["id"] for r in rows(conn, f"SELECT id FROM tasks WHERE notion_page_id IS NULL AND id IN ({','.join('?' * len(a.task))})", tuple(a.task))]
            if fresh:
                print(f"  note: Task-{', Task-'.join(map(str, fresh))} ha{'s' if len(fresh) == 1 else 've'} no Notion page yet; creating out of id order lets Notion's Task ID counter drift from Factotum's ids")
        while True:
            res = notion_mirror.sweep(conn, ecfg, all_rows=a.all, dry_run=a.dry_run, cap=10**6 if a.all or a.task else 200, ids=a.task)
            if a.dry_run:
                print(f"{res['pending']} rows would be pushed (dry run)"); break
            for k in ("created", "updated"):
                total[k] += res[k]
            total["failed"] += res["failed"]
            print(f"  created {res['created']}, updated {res['updated']}, failed {len(res['failed'])}, {res['pending']} pending")
            if a.all or a.task or not res["pending"] or not (res["created"] or res["updated"]):
                break
        for f in total["failed"]:
            print("  !", f)
        if not a.dry_run:
            print(f"done: created {total['created']}, updated {total['updated']}, failed {len(total['failed'])}")
    elif a.cmd == "connectors":
        from . import oauth
        if a.revoke:
            print("revoked" if oauth.revoke_client(conn, a.revoke) else "no such connector (or already revoked)")
        if a.arm:
            print(f"armed until {oauth.arm(conn)}: approve the connector in the app that asked")
        print(f"connector {'on' if oauth.enabled(ecfg) else 'OFF (Settings → Connections)'}; URL for claude.ai: {oauth.resource(ecfg)}" + (f"; armed until {oauth.armed(conn)}" if oauth.armed(conn) else ""))
        for c in oauth.connectors(conn):
            state = f"revoked {c['revoked_at'][:10]}" if c["revoked_at"] else f"{c['tokens']} live token(s)"
            print(f"  {c['client_id']:34} {(c['name'] or '')[:24]:24} registered {c['created_at'][:10]}  last used {(c['last_used_at'] or '—')[:16]}  {state}")
    elif a.cmd == "planner":
        from . import brief, gcal, planner
        res = planner.run(conn, ecfg, today, calendar=gcal, dry_run=a.dry_run)
        if not res["ok"]:
            raise SystemExit(f"planner failed: {res['error']}")
        if a.dry_run:
            print(json.dumps(res["data"], indent=1, ensure_ascii=False)); print(f"\n(dry run · ${res['cost_usd']:.2f} · prompt under data/runs/planner/{today}/)")
        else:
            print(res["line"], f"· ${res['cost_usd']:.2f}")
            for r in res["rejected"]:
                print("  rejected:", r)
            if a.send:
                print(brief.send(conn, ecfg, today, brief.smtp_mailer, gcal, planner=res))
    elif a.cmd == "file-task":
        from . import filing
        res = filing.run(conn, ecfg, a.task_id, dry_run=a.dry_run, today=today)
        if not res["ok"]:
            raise SystemExit(f"filing failed: {res['error']}")
        print(json.dumps(res["data"], indent=1, ensure_ascii=False) if a.dry_run else res["line"])
        print(f"\n({'dry run · ' if a.dry_run else ''}${res['cost_usd']:.2f} · prompt under data/runs/filing/{a.task_id}/)")
    elif a.cmd == "ripple":
        from . import ripple
        res = ripple.run(conn, ecfg, [a.task_id], today, dry_run=a.dry_run)     # whatever claude.ripple.enabled says: an explicit command
        if not res["ok"]:
            raise SystemExit(f"ripple failed: {res['error']}")
        if res.get("dry_run"):
            print(json.dumps(res["data"], indent=1, ensure_ascii=False))
            for p in res["preview"]:
                print(f"Task-{p['task_id']}: {p['effect']} · {p['verdict']}" if "effect" in p else f"  {p['proposal'] or '(no card)'} · {p['verdict']}")
            print(f"(dry run · ${res['cost_usd']:.2f} · prompt under data/runs/ripple/{today}/)")
        else:
            print(res["line"])
    elif a.cmd == "sweep":
        from datetime import datetime as _dt
        from . import canvas, gcal, mail_sweep, slack_sweep, suggestions
        now = _dt.now()
        mod = {"mail": mail_sweep, "canvas": canvas, "calendar": gcal, "slack": slack_sweep, "links": suggestions}[a.which]
        print(mod.sweep_job(conn, ecfg, now) or "nothing to do")
    elif a.cmd == "links-check":
        from . import suggestions
        words = {"same_as": "same as", "part_of": "part of", "waits_on": "waits on", "any": "linked to"}
        total = {"twin": [0, 0], "text": [0, 0], "thread": [0, 0]}
        for c in suggestions.detect(conn):
            why_not = suggestions.why_not(conn, c["kind"], c["a"], c["b"])
            t = total.setdefault(c["signal"], [0, 0])
            t[0] += 1
            t[1] += why_not is None
            print(f"{c['signal']:<7} Task-{c['a']} → Task-{c['b']}  {words[c['kind']]}  {c['why']}  {why_not or 'would write'}")
        print(" · ".join(f"{s} {n} ({w} would write)" for s, (n, w) in total.items()))
    elif a.cmd == "calendar-check":
        from datetime import datetime, timedelta
        from . import gcal
        evs = gcal.events(ecfg, datetime.now(), datetime.now() + timedelta(days=7))
        for e in evs:
            print(f"  {e['start']:<16} {e['summary'] or '(no title)'}{'  · accepted' if e['self_status'] == 'accepted' else ''}")
        print(f"{len(evs)} events in the next 7 days")
    elif a.cmd == "score":
        for t in relations.scored(conn, rows(conn, "SELECT * FROM tasks WHERE status = 'open' ORDER BY COALESCE(due,'9999'), id"), today, ecfg):
            print(f"{t['score']:3d} {t['priority']} Task-{t['id']:<4} {(t['project'] or '-')[:14]:<14} {t['name'][:60]:<60} {'; '.join(t['score_reasons'])}")
    elif a.cmd == "review":
        last = get_settings(conn).get("state.last_review_at")
        q = lifecycle.build_review_queue(relations.annotate(conn, rows(conn, "SELECT * FROM tasks WHERE status = 'open'")), today, ecfg, None if not last else date.fromisoformat(last))
        for i in q:
            t = i["task"]
            print(f"{i['primary']:16s} Task-{t['id']:<4} {t['name'][:70]}")
        print(f"{len(q)} items")
    elif a.cmd == "expire":
        due = lifecycle.expire(relations.annotate(conn, rows(conn, "SELECT * FROM tasks WHERE status = 'open'")), today, ecfg)
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
