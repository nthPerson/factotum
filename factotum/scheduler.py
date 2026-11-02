"""The automations' clock (SPEC.md §10, §14 phase 2). One thread, one tick every 30 s, no job
library: each tick re-reads the effective config (Settings-tab overrides included), so changing
the review time or turning the brief off takes effect at the next tick.

    reminders       every tick: fire due rows (reminders.fire) when ntfy is configured
    review_push     once a day at notify.review_time; quiet when reviewed today or the queue is empty
    morning_brief   once a day at notify.morning_brief.time when enabled (brief.send)
    expire          once a day at lifecycle.expire_time (03:30): fade / lapse what Review has shown
    calendar_sweep  every sources.calendar.sweep_minutes when the calendar is on

Daily jobs are "at or after HH:MM, once per day", so a service that was down at 06:30 still sends
the brief when it comes back. A failed job retries after an hour, not the next day. Everything
that did something, or failed, lands in `runs` (cost 0) so the Log tab shows it.
"""
from __future__ import annotations

import sqlite3
import threading
import time
import traceback
from datetime import date, datetime, timedelta

from . import batches, config as C, lifecycle, notify, reminders
from .db import get_settings, log_run, rows, set_setting

RETRY_AFTER = timedelta(hours=1)


class Scheduler:
    def __init__(self, cfg: dict, conn: sqlite3.Connection, push=notify.publish, mailer=None, calendar=None):
        self.cfg, self.conn, self.push, self.mailer, self.calendar = cfg, conn, push, mailer, calendar
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._rem_error_at: datetime | None = None

    # ---- the tick -------------------------------------------------------------------------

    def tick(self, now_local: datetime | None = None) -> list[str]:
        now = now_local or reminders.local_now(self.cfg)
        today = now.date()
        st = get_settings(self.conn)
        ecfg = C.effective(self.cfg, st)
        did: list[str] = []
        self._reminders(ecfg, now, did)
        self._daily("expire", C.get(ecfg, "lifecycle.expire_time", "03:30"), now, st, did, lambda: self._expire(ecfg, today))
        if C.get(ecfg, "notify.morning_brief.enabled", True) and self.mailer:
            self._daily("morning_brief", C.get(ecfg, "notify.morning_brief.time", "06:30"), now, st, did, lambda: self._brief(ecfg, today))
        self._daily("review_push", C.get(ecfg, "notify.review_time", "19:30"), now, st, did, lambda: self._review_push(ecfg, today, st))
        if C.get(ecfg, "sources.calendar.enabled") and self.calendar:
            self._every("calendar_sweep", int(C.get(ecfg, "sources.calendar.sweep_minutes", 30)), now, st, did, lambda: self.calendar(self.conn, ecfg, now))
        return did

    def _reminders(self, ecfg: dict, now: datetime, did: list[str]) -> None:
        if not notify.enabled(ecfg):
            return
        started = time.monotonic()
        try:
            fired = reminders.fire(self.conn, ecfg, now, self.push)
        except Exception as e:   # ntfy down: leave the rows pending, say so once an hour
            self.conn.rollback()
            if not self._rem_error_at or now - self._rem_error_at > RETRY_AFTER:
                self._rem_error_at = now
                log_run(self.conn, "reminder", int((time.monotonic() - started) * 1000), ok=False, error=f"{type(e).__name__}: {e}"[:500])
            return
        if fired:
            summary = ", ".join(f"Task-{f['task_id']} {f['task_name']}"[:60] for f in fired)
            log_run(self.conn, "reminder", int((time.monotonic() - started) * 1000), ok=True, summary=summary)
            did.append(f"reminders: {summary}")

    def _daily(self, name: str, hhmm: str | None, now: datetime, st: dict, did: list[str], fn) -> None:
        if not hhmm or now.strftime("%H:%M") < str(hhmm):
            return
        if st.get(f"state.last_run.{name}") == now.date().isoformat():
            return
        self._run(name, now, st, did, fn, mark=now.date().isoformat())

    def _every(self, name: str, minutes: int, now: datetime, st: dict, did: list[str], fn) -> None:
        last = st.get(f"state.last_run.{name}")
        if last and now - datetime.fromisoformat(last) < timedelta(minutes=minutes):
            return
        self._run(name, now, st, did, fn, mark=now.strftime("%Y-%m-%dT%H:%M"))

    def _run(self, name: str, now: datetime, st: dict, did: list[str], fn, mark: str) -> None:
        failed = st.get(f"state.last_fail.{name}")
        if failed and now - datetime.fromisoformat(failed) < RETRY_AFTER:
            return
        started = time.monotonic()
        try:
            summary = fn()
        except Exception as e:
            self.conn.rollback()
            set_setting(self.conn, f"state.last_fail.{name}", now.strftime("%Y-%m-%dT%H:%M"))
            log_run(self.conn, name, int((time.monotonic() - started) * 1000), ok=False, error=f"{type(e).__name__}: {e}\n{traceback.format_exc()[-800:]}")
            self.conn.commit()
            did.append(f"{name}: failed ({e})")
            return
        set_setting(self.conn, f"state.last_run.{name}", mark)
        if summary:   # None means "nothing to do today" and is not worth a log line
            log_run(self.conn, name, int((time.monotonic() - started) * 1000), ok=True, summary=summary)
            did.append(f"{name}: {summary}")
        self.conn.commit()

    # ---- the jobs --------------------------------------------------------------------------

    def _open(self) -> list[dict]:
        return rows(self.conn, "SELECT * FROM tasks WHERE status = 'open'")

    def _expire(self, ecfg: dict, today: date) -> str | None:
        due = lifecycle.expire(self._open(), today, ecfg)
        if not due:
            return None
        batches.apply(self.conn, [{"task_id": tid, "op": "fade" if st == "faded" else "lapse"} for tid, st, _ in due], "automation", today)
        faded = [tid for tid, st, _ in due if st == "faded"]
        lapsed = [tid for tid, st, _ in due if st == "lapsed"]
        parts = [f"faded {len(faded)}: " + " ".join(f"Task-{t}" for t in faded)] if faded else []
        parts += [f"lapsed {len(lapsed)}: " + " ".join(f"Task-{t}" for t in lapsed)] if lapsed else []
        return "; ".join(parts)

    def _review_push(self, ecfg: dict, today: date, st: dict) -> str | None:
        if st.get("state.last_review_at") == today.isoformat():
            return None
        last = st.get("state.last_review_at")
        queue = lifecycle.build_review_queue(self._open(), today, ecfg, date.fromisoformat(last) if last else None, cap=10**9)
        if not queue:
            return None
        n = len(queue)
        base = str(C.get(ecfg, "server.public_url", "")).rstrip("/")
        first = "; ".join(i["task"]["name"][:40] for i in queue[:3])
        self.push(ecfg, f"Review: {n} item{'' if n == 1 else 's'}", f"{n} item{'' if n == 1 else 's'} waiting: {first}" + (" …" if n > 3 else ""),
                  click=f"{base}/#review", tags=["clipboard"])
        return f"{n} items"

    def _brief(self, ecfg: dict, today: date) -> str | None:
        from . import brief
        return brief.send(self.conn, ecfg, today, self.mailer, self.calendar)

    # ---- the thread --------------------------------------------------------------------------

    def start(self, interval_s: int = 30) -> None:
        def loop():
            while not self._stop.is_set():
                try:
                    self.tick()
                except Exception:
                    traceback.print_exc()
                self._stop.wait(interval_s)
        self._thread = threading.Thread(target=loop, name="factotum-scheduler", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
