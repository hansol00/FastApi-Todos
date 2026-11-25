"""v10.0.0: v9.0.0 까지 쓰던 todo.json 을 DB 로 한 번만 옮긴다.

실행 (컨테이너 안):
    docker exec FastApi-app-011 python migrate_json.py /app/data/todo.json

- id 를 그대로 유지한다 (화면·로그·Loki 기록과 번호가 이어지도록).
- 한 트랜잭션으로 처리한다. 중간에 실패하면 아무것도 들어가지 않는다.
- 완료 기록(app_meta.legacy_json_imported)을 남겨 두 번 실행해도 다시 넣지 않는다.
  → 배포 스크립트에서 매번 실행해도 안전하다. 사용자가 일부러 전부 지운 뒤에
    재배포해도 옛 데이터가 되살아나지 않는다.
- todos 테이블에 이미 데이터가 있는데 기록이 없으면(이관 전에 새로 쓰기 시작한 경우)
  id 가 겹칠 수 있으므로 손대지 않고 멈춘다.
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

import db
from main import TodoItem

MARKER = "legacy_json_imported"


class MigrationConflict(RuntimeError):
    """DB 에 이미 데이터가 있어 그대로 합치면 id 가 겹칠 수 있음."""


def import_legacy_json(session: Session, path: Path) -> int | None:
    """옮긴 건수를 돌려준다. 이미 옮긴 적이 있으면 None."""
    if session.get(db.AppMeta, MARKER) is not None:
        return None
    if session.scalar(select(func.count()).select_from(db.TodoRow)):
        raise MigrationConflict("todos 테이블이 비어 있지 않음 — 이관하지 않고 멈춤")

    raw = path.read_text(encoding="utf-8") if path.exists() else "[]"
    # TodoItem 으로 한 번 검증한다: 길이·우선순위 제한을 어긴 옛 데이터는 DB 에 들어가기 전에 걸린다.
    # priority 가 없는 v8.0.0 이전 항목은 기본값 medium 으로 채워진다.
    items = [TodoItem(**t) for t in json.loads(raw)]
    session.add_all(db.TodoRow(**item.model_dump()) for item in items)
    session.flush()
    db.sync_id_sequence(session)
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    session.add(db.AppMeta(key=MARKER, value=f"{len(items)} items from {path.name} at {stamp}"))
    session.commit()
    return len(items)


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: python migrate_json.py <todo.json 경로>", file=sys.stderr)
        return 2
    db.init_db()
    with db.SessionLocal() as session:
        try:
            count = import_legacy_json(session, Path(argv[1]))
        except MigrationConflict as exc:
            print(f"migration skipped: {exc}", file=sys.stderr)
            return 1
    print("migration already done" if count is None else f"migrated {count} items")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main(sys.argv))
