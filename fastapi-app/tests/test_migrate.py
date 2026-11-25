"""migrate_json.py — v9.0.0 의 todo.json 을 DB 로 한 번만 옮기기 (v10.0.0)."""

import json

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import select

import db
import migrate_json
from main import app

client = TestClient(app)

LEGACY = [
    {"id": 3, "title": "세 번째", "description": "", "completed": True,
     "due_date": "2026-11-30", "priority": "high"},
    # v8.0.0 이전 항목: priority 필드가 없다 → medium 으로 채워져야 한다
    {"id": 7, "title": "예전 항목", "description": "설명", "completed": False, "due_date": None},
]


@pytest.fixture
def legacy_file(tmp_path):
    path = tmp_path / "todo.json"
    path.write_text(json.dumps(LEGACY, ensure_ascii=False), encoding="utf-8")
    return path


def rows(session):
    return session.scalars(select(db.TodoRow).order_by(db.TodoRow.id)).all()


def test_import_keeps_ids_and_fills_defaults(session, legacy_file):
    assert migrate_json.import_legacy_json(session, legacy_file) == 2
    got = rows(session)
    assert [(r.id, r.title, r.priority) for r in got] == [(3, "세 번째", "high"), (7, "예전 항목", "medium")]
    assert str(got[0].due_date) == "2026-11-30"


def test_new_todo_after_import_continues_ids(session, legacy_file):
    migrate_json.import_legacy_json(session, legacy_file)
    assert client.post("/todos", json={"title": "이관 후 추가"}).json()["id"] == 8


def test_import_runs_only_once(session, legacy_file):
    """배포마다 실행해도 한 번만 — 사용자가 전부 지운 뒤 재배포해도 옛 데이터가 되살아나지 않는다."""
    migrate_json.import_legacy_json(session, legacy_file)
    client.delete("/todos", params={"ids": [3, 7]})
    assert migrate_json.import_legacy_json(session, legacy_file) is None
    assert rows(session) == []


def test_import_refuses_when_db_already_has_data(session, legacy_file):
    client.post("/todos", json={"title": "이관 전에 생긴 항목"})
    with pytest.raises(migrate_json.MigrationConflict):
        migrate_json.import_legacy_json(session, legacy_file)


def test_missing_file_marks_done_with_zero(session, tmp_path):
    assert migrate_json.import_legacy_json(session, tmp_path / "없음.json") == 0
    assert session.get(db.AppMeta, migrate_json.MARKER).value.startswith("0 items")


def test_invalid_legacy_data_imports_nothing(session, tmp_path):
    """한 건이라도 규칙(우선순위·길이)을 어기면 전부 거부 — 일부만 들어간 상태로 남지 않는다."""
    path = tmp_path / "todo.json"
    path.write_text(json.dumps([LEGACY[0], {"id": 9, "title": "x", "priority": "urgent"}]),
                    encoding="utf-8")
    with pytest.raises(ValidationError):
        migrate_json.import_legacy_json(session, path)
    assert rows(session) == []
    assert session.get(db.AppMeta, migrate_json.MARKER) is None


#명령행

def test_cli_migrates_then_reports_already_done(legacy_file, capsys):
    assert migrate_json.main(["migrate_json.py", str(legacy_file)]) == 0
    assert migrate_json.main(["migrate_json.py", str(legacy_file)]) == 0
    out = capsys.readouterr().out
    assert "migrated 2 items" in out
    assert "migration already done" in out


def test_cli_conflict_exits_1(legacy_file, capsys):
    client.post("/todos", json={"title": "먼저 생긴 항목"})
    assert migrate_json.main(["migrate_json.py", str(legacy_file)]) == 1
    assert "migration skipped" in capsys.readouterr().err


def test_cli_usage(capsys):
    assert migrate_json.main(["migrate_json.py"]) == 2
    assert "usage" in capsys.readouterr().err
