"""The final review's findings on configuration, the deploy check, the documents and the skills (2026-11-11)."""
import importlib.util
import subprocess
from pathlib import Path

import pytest

from factotum import config as C
from factotum.db import connect, init_db

ROOT = C.ROOT


# --- 1. links-check reads only: no init_db, so no migration of a database the old service still runs on

def _old_db_module(tmp_path):
    """`factotum/db.py` as the release before linked tasks had it (main at f95e390)."""
    try:
        text = subprocess.run(["git", "-C", str(ROOT), "show", "f95e390:factotum/db.py"], capture_output=True, text=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("git history not available here")
    p = tmp_path / "old_db.py"
    p.write_text(text, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("old_db_f95e390", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _files(d: Path) -> set[str]:
    return {str(p.relative_to(d)) for p in d.rglob("*")}


def test_links_check_on_a_database_from_before_this_release_migrates_nothing_and_says_how_to_preview(tmp_path, monkeypatch, capsys):
    from conftest import FIXTURE
    from factotum.__main__ import main
    old = _old_db_module(tmp_path)
    data = tmp_path / "data"
    db = data / "factotum.db"
    c = old.connect(db)
    old.init_db(c)
    now = "2026-10-01T00:00:00+00:00"
    for i in (1, 2):
        c.execute("INSERT INTO tasks (id, name, status, created_at, edited_at) VALUES (?, ?, 'open', ?, ?)", (i, f"t{i}", now, now))
    c.execute("INSERT INTO links (task_id, kind, ref, created_at) VALUES (2, 'task', '1', ?)", (now,))   # the import's kind the migration rewrites
    c.commit()
    c.close()
    before, listing = db.read_bytes(), _files(data)
    monkeypatch.setenv("FACTOTUM_CONFIG", str(FIXTURE)); monkeypatch.setenv("FACTOTUM_DB", str(db))
    capsys.readouterr()
    assert main(["links-check"]) == 0
    out = capsys.readouterr().out.strip().splitlines()
    assert db.read_bytes() == before and _files(data) == listing                 # no -wal, -shm or backups/ left
    assert len(out) == 1 and "not migrated yet" in out[0]
    assert "s.backup(d)" in out[0] and f"FACTOTUM_DB=" in out[0] and "init-db" in out[0] and out[0].endswith("links-check")


def test_links_check_on_a_migrated_database_reads_it_and_writes_nothing(tmp_path, monkeypatch, capsys):
    from conftest import FIXTURE
    from factotum.__main__ import main
    from stage4_helpers import mk
    data = tmp_path / "data"
    db = data / "factotum.db"
    c = connect(db); init_db(c)
    mk(c, 1, name="Take COMPE 510 Quiz 7", source="Canvas", due="2026-11-10T23:59"); mk(c, 2, name="Quiz 7 prep", source="Plaud", due="2026-11-10")
    c.commit(); c.close()
    before, listing = db.read_bytes(), _files(data)
    monkeypatch.setenv("FACTOTUM_CONFIG", str(FIXTURE)); monkeypatch.setenv("FACTOTUM_DB", str(db))
    capsys.readouterr()
    assert main(["links-check"]) == 0
    out = capsys.readouterr().out
    assert "twin    Task-2 → Task-1  same as" in out and "would write" in out and "twin 1 (1 would write)" in out
    assert db.read_bytes() == before and _files(data) == listing


def test_links_check_never_creates_a_database(tmp_path, monkeypatch, capsys):
    from factotum.__main__ import main
    monkeypatch.setenv("FACTOTUM_DB", str(tmp_path / "none" / "factotum.db"))
    assert main(["links-check"]) == 0
    assert "no database" in capsys.readouterr().out and not (tmp_path / "none").exists()
