"""Deterministic scoring (SPEC.md §5). Claude never writes scores; this module does.

score(task, today, cfg) -> (score: int, priority: str, reasons: list[str])
"""
from __future__ import annotations

from datetime import date

from . import config as C
from .dates import days_until


def score(task: dict, today: date, cfg: dict) -> tuple[int, str, list[str]]:
    sc = C.get(cfg, "scoring", {}) or {}
    reasons: list[str] = []
    d = days_until(task.get("due"), today)
    hard = bool(task.get("hard"))
    longterm = bool(task.get("longterm"))

    hard_t = sc.get("hard", {})
    if d is None:
        base = sc.get("undated_longterm", 10) if longterm else sc.get("undated", 15)
        reasons.append("no date")
    elif d < 0:
        if hard:
            base = hard_t.get("overdue", 100)
            reasons.append(f"hard, {-d}d overdue")
        else:
            so = sc.get("soft_overdue", {})
            base = max(so.get("floor", 25), so.get("start", 70) - so.get("decay_per_day", 5) * (-d))
            reasons.append(f"soft, {-d}d past, decaying")
    else:
        if d == 0:
            base, why = hard_t.get("today", 90), "due today"
        elif d <= 2:
            base, why = hard_t.get("d2", 80), f"due in {d}d"
        elif d <= 7:
            base, why = hard_t.get("d7", 60), f"due in {d}d"
        elif d <= 14:
            base, why = hard_t.get("d14", 40), f"due in {d}d"
        else:
            base, why = hard_t.get("later", 20), f"due in {d}d"
        if not hard:
            base = max(0, base - sc.get("soft_penalty", 15))
            why = "soft, " + why
        reasons.append(why)

    total = float(base)
    weight = C.project_weight(cfg, task.get("project"))
    w = (sc.get("project_weights", {}) or {}).get(weight, 0)
    if w:
        total += w
        reasons.append(f"{task.get('project')} {weight} +{w}")

    extra_mentions = max(0, int(task.get("mentions") or 1) - 1)
    mb = min(sc.get("mention_cap", 15), extra_mentions * sc.get("mention_bonus", 5))
    if mb:
        total += mb
        reasons.append(f"{extra_mentions + 1} mentions +{mb}")

    if task.get("counterpart") or task.get("waiting_on"):
        total += sc.get("owed_bonus", 10)
        reasons.append(f"owed to {task.get('counterpart') or task.get('waiting_on')} +{sc.get('owed_bonus', 10)}")

    eff = task.get("effort")
    qw = sc.get("quick_win", {})
    sn = sc.get("start_now", {})
    if d is not None and 0 <= d <= qw.get("within_days", 3) and eff in (qw.get("efforts") or []):
        total += qw.get("bonus", 5)
        reasons.append("quick win")
    if d is not None and 0 <= d <= sn.get("within_days", 7) and eff in (sn.get("efforts") or []):
        total += sn.get("bonus", 10)
        reasons.append("start now")

    man = sc.get("manual", {})
    mp = task.get("manual_priority")
    if mp == "P0":
        total = max(total, man.get("P0_floor", 85)); reasons.append("manual P0 floor")
    elif mp == "P1":
        total = max(total, man.get("P1_floor", 65)); reasons.append("manual P1 floor")
    elif mp == "P3":
        if not (hard and d is not None and d <= 2):
            total = min(total, man.get("P3_cap", 40)); reasons.append("manual P3 cap")

    s = int(max(0, min(100, round(total))))
    bands = sc.get("bands", {})
    if s >= bands.get("P0", 80):
        pr = "P0"
    elif s >= bands.get("P1", 60):
        pr = "P1"
    elif s >= bands.get("P2", 35):
        pr = "P2"
    else:
        pr = "P3"
    return s, pr, reasons
