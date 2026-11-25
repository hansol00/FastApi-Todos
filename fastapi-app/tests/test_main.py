"""To-Do List API 테스트.

실행: 저장소 루트에서  pytest fastapi-app/tests --cov=main --cov-report=term
"""

import logging
import re

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import insert, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import QueuePool

import db
import main
from main import TodoItem, app

client = TestClient(app)

# v10.0.0: DB 격리는 conftest.py 의 isolated_db(autouse) 가 맡는다 — 테스트마다 빈 테이블.


def save_todos(todos: list[TodoItem]) -> None:
    """테스트 데이터를 id 그대로 DB 에 넣는다 (v9.0.0 까지의 save_todos 자리)."""
    with db.SessionLocal() as s:
        s.add_all(db.TodoRow(**t.model_dump()) for t in todos)
        s.flush()
        db.sync_id_sequence(s)                   # PostgreSQL: 직접 넣은 id 다음부터 시퀀스가 이어지게
        s.commit()


def load_todos() -> list[TodoItem]:
    """DB 에 실제로 저장된 내용을 id 순으로 읽는다 — API 응답이 아니라 저장 결과를 확인할 때."""
    with db.SessionLocal() as s:
        return [TodoItem.model_validate(r) for r in s.scalars(select(db.TodoRow).order_by(db.TodoRow.id))]


def broken_session_factory(engine):
    """열 수 없는 경로의 SQLite — DB 서버가 꺼진 것과 같은 OperationalError 를 '진짜로' 일으킨다."""
    factory = sessionmaker(engine)

    def override():
        with factory() as session:
            yield session
    return override


def sample(todo_id: int = 1, title: str = "Test", **kwargs) -> TodoItem:
    """테스트용 TodoItem 하나를 만든다."""
    kwargs.setdefault("description", "Test description")
    kwargs.setdefault("completed", False)
    return TodoItem(id=todo_id, title=title, **kwargs)


#조회 (GET)

def test_get_todos_empty():
    response = client.get("/todos")
    assert response.status_code == 200
    assert response.json() == []


def test_get_todos_with_items():
    save_todos([sample()])
    response = client.get("/todos")
    assert response.status_code == 200
    assert len(response.json()) == 1
    assert response.json()[0]["title"] == "Test"


#추가 (POST)

def test_create_todo():
    payload = {"title": "Test", "description": "Test description", "completed": False}
    response = client.post("/todos", json=payload)
    assert response.status_code == 201
    assert response.json()["title"] == "Test"
    assert response.json()["id"] == 1          # id 는 서버가 부여
    assert len(load_todos()) == 1              # DB 에도 저장됐는지 확인


def test_create_todo_assigns_sequential_ids():
    client.post("/todos", json={"title": "첫 번째"})
    response = client.post("/todos", json={"title": "두 번째"})
    assert response.json()["id"] == 2
    assert [t.id for t in load_todos()] == [1, 2]


def test_ids_are_not_reused_after_delete():
    """v10.0.0: 마지막 항목을 지워도 그 id 를 새 항목에 다시 주지 않는다.

    재사용하면 지우기 전 화면을 보던 사용자가 '삭제' 를 눌렀을 때 새 항목이 지워진다.
    (SQLite 기본 동작은 재사용 → db.TodoRow 에 sqlite_autoincrement 를 켜서 PostgreSQL 과 맞춤)
    """
    client.post("/todos", json={"title": "a"})
    client.post("/todos", json={"title": "b"})
    client.delete("/todos/2")
    assert client.post("/todos", json={"title": "c"}).json()["id"] == 3


def test_create_after_seeded_ids_continues_sequence():
    """id 를 직접 넣은 데이터(이관 등) 뒤에 추가해도 id 가 부딪히지 않는다 (PostgreSQL 시퀀스)."""
    save_todos([sample(1), sample(5)])
    response = client.post("/todos", json={"title": "새 항목"})
    assert response.status_code == 201
    assert response.json()["id"] == 6


def test_create_todo_with_due_date():
    response = client.post("/todos", json={"title": "과제 제출", "due_date": "2026-10-06"})
    assert response.status_code == 201
    assert response.json()["due_date"] == "2026-10-06"
    assert load_todos()[0].due_date.isoformat() == "2026-10-06"


def test_create_todo_defaults():
    """description / completed / due_date 를 생략했을 때의 기본값."""
    response = client.post("/todos", json={"title": "제목만"})
    body = response.json()
    assert response.status_code == 201
    assert body["description"] == ""
    assert body["completed"] is False
    assert body["due_date"] is None


#유효성 검사 (422)

def test_create_todo_missing_title():
    response = client.post("/todos", json={"description": "Test description"})
    assert response.status_code == 422


def test_create_todo_empty_title():
    """title 은 min_length=1 이라 빈 문자열을 거부해야 한다."""
    response = client.post("/todos", json={"title": ""})
    assert response.status_code == 422
    assert len(load_todos()) == 0              # 거부된 요청은 저장되지 않는다


def test_create_todo_title_too_long():
    """title 은 max_length=100."""
    assert client.post("/todos", json={"title": "가" * 100}).status_code == 201
    assert client.post("/todos", json={"title": "가" * 101}).status_code == 422


def test_create_todo_description_at_limit():
    """v5.1.0 (M-2): description 경계값 500 자는 정상 처리된다."""
    response = client.post("/todos", json={"title": "제목", "description": "가" * 500})
    assert response.status_code == 201
    assert len(response.json()["description"]) == 500


def test_create_todo_description_too_long():
    """v5.1.0 (M-2): 길이 제한이 없으면 대용량 입력으로 디스크를 채울 수 있다."""
    response = client.post("/todos", json={"title": "제목", "description": "가" * 501})
    assert response.status_code == 422
    assert len(load_todos()) == 0              # 거부된 요청은 저장되지 않는다


def test_create_todo_invalid_due_date():
    response = client.post("/todos", json={"title": "Test", "due_date": "2026-13-45"})
    assert response.status_code == 422


#수정 (PUT)

def test_update_todo():
    save_todos([sample()])
    payload = {"title": "Updated", "description": "Updated description", "completed": True}
    response = client.put("/todos/1", json=payload)
    assert response.status_code == 200
    assert response.json()["title"] == "Updated"
    assert response.json()["completed"] is True
    assert load_todos()[0].title == "Updated"


def test_update_todo_keeps_position():
    """여러 건 중 하나만 수정해도 순서와 나머지 항목이 유지돼야 한다."""
    save_todos([sample(1, "첫 번째"), sample(2, "두 번째"), sample(3, "세 번째")])
    client.put("/todos/2", json={"title": "수정됨"})
    assert [t.title for t in load_todos()] == ["첫 번째", "수정됨", "세 번째"]


def test_update_todo_not_found():
    payload = {"title": "Updated", "description": "Updated description", "completed": True}
    response = client.put("/todos/1", json=payload)
    assert response.status_code == 404
    assert response.json()["detail"] == "To-Do item not found"


#삭제 (DELETE)

def test_delete_todo():
    save_todos([sample()])
    response = client.delete("/todos/1")
    assert response.status_code == 204         # 204 는 응답 본문이 없다
    assert response.content == b""
    assert load_todos() == []


def test_delete_todo_not_found():
    response = client.delete("/todos/1")
    assert response.status_code == 404


def test_delete_one_of_many():
    save_todos([sample(1), sample(2), sample(3)])
    assert client.delete("/todos/2").status_code == 204
    assert [t.id for t in load_todos()] == [1, 3]


#감사 로그 (v5.1.0, OWASP A09)

def test_crud_writes_audit_log(caplog):
    """변경 이력이 서버 로그에 남아야 무단 조작 시 복구 근거가 된다."""
    with caplog.at_level("INFO", logger="todo"):
        client.post("/todos", json={"title": "기록 대상"})
        client.put("/todos/1", json={"title": "수정됨"})
        client.delete("/todos/1")
    assert "todo created" in caplog.text
    assert "todo updated" in caplog.text
    assert "todo deleted" in caplog.text


#저장소 (v10.0.0: DB)

def test_db_rejects_invalid_priority_directly():
    """API 를 거치지 않고(psql, 스크립트) 넣는 잘못된 값도 DB CHECK 제약이 막는다."""
    with db.SessionLocal() as s:
        with pytest.raises(IntegrityError):
            s.execute(insert(db.TodoRow).values(title="x", priority="urgent"))


def test_search_treats_wildcards_literally():
    """검색어의 % 와 _ 는 LIKE 와일드카드가 아니라 글자 그대로 찾는다."""
    save_todos([sample(1, "진행률 100%"), sample(2, "1000원"), sample(3, "a_b"), sample(4, "axb")])
    assert ids(client.get("/todos", params={"q": "100%"})) == [1]
    assert ids(client.get("/todos", params={"q": "a_b"})) == [3]


def test_lifespan_initializes_database(monkeypatch):
    """기동(lifespan) 때 init_db 가 불린다 — 빈 DB 에 테이블이 없어 첫 요청이 실패하는 일을 막는다."""
    called = []
    monkeypatch.setattr(main, "init_db", lambda: called.append(True))
    with TestClient(app):
        pass
    assert called == [True]


#DB 장애 (v10.0.0)

def test_api_returns_503_when_database_down(tmp_path, new_engine):
    """DB 가 꺼지면 500(버그)이 아니라 503 + Retry-After(잠시 후 재시도)."""
    app.dependency_overrides[db.get_session] = broken_session_factory(new_engine(f"sqlite:///{tmp_path}/no/such/dir/x.db"))
    for response in (client.get("/todos"), client.post("/todos", json={"title": "x"})):
        assert response.status_code == 503
        assert response.headers["retry-after"] == "5"
        assert response.json() == {"detail": "Database unavailable"}   # 내부 정보(경로·SQL) 미노출


def test_api_returns_503_when_pool_exhausted(tmp_path, new_engine):
    """커넥션 풀이 모두 사용 중이면 DB_POOL_TIMEOUT 만큼만 기다리고 503 — 무한정 매달리지 않는다."""
    engine = new_engine(f"sqlite:///{tmp_path}/pool.db", poolclass=QueuePool,
                           pool_size=1, max_overflow=0, pool_timeout=0.1)
    db.Base.metadata.create_all(engine)
    factory = sessionmaker(engine)

    def override():
        with factory() as session:
            yield session

    app.dependency_overrides[db.get_session] = override
    with engine.connect():                       # 하나뿐인 연결을 붙잡아 둔다
        response = client.get("/todos")
    assert response.status_code == 503
    assert client.get("/todos").status_code == 200   # 연결을 돌려주면 바로 회복


#헬스체크

def test_health_ok():
    save_todos([sample(1), sample(2)])
    response = client.get("/health")
    body = response.json()
    assert response.status_code == 200
    assert body["status"] == "ok"
    assert body["storage"] == "ok"
    assert body["database"] in ("sqlite", "postgresql")
    assert body["todo_count"] == 2
    assert body["version"] == main.APP_VERSION
    assert body["uptime_seconds"] >= 0


def test_health_degraded_when_database_down(tmp_path, caplog, new_engine):
    """DB 에 질의가 안 되면 503 으로 트래픽을 거절하되, 예외 원문은 노출하지 않아야 한다."""
    app.dependency_overrides[db.get_session] = broken_session_factory(new_engine(f"sqlite:///{tmp_path}/no/such/dir/x.db"))
    response = client.get("/health")
    assert response.status_code == 503
    assert response.json()["status"] == "degraded"
    assert response.json()["storage"] == "error"
    # v5.1.0 (L-1): 예외 상세는 응답이 아니라 서버 로그에만 남는다
    assert "detail" not in response.json()
    assert "OperationalError" in caplog.text


#화면 서빙

def test_read_root_serves_html():
    response = client.get("/")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")


#검색·필터 (v6.0.0)

@pytest.fixture
def sample_todos():
    """검색/필터 테스트용 고정 데이터."""
    save_todos([
        TodoItem(id=1, title="운영체제 과제", description="스케줄러 정리", completed=False),
        TodoItem(id=2, title="장보기", description="우유와 계란", completed=True),
        TodoItem(id=3, title="Docker 복습", description="compose 명령어", completed=False),
    ])


def test_get_todos_without_filters_returns_all(sample_todos):
    """파라미터가 없으면 기존처럼 전체를 돌려준다."""
    assert len(client.get("/todos").json()) == 3


def test_search_matches_title(sample_todos):
    found = client.get("/todos", params={"q": "과제"}).json()
    assert [t["id"] for t in found] == [1]


def test_search_matches_description(sample_todos):
    """설명에만 있는 단어도 찾는다."""
    found = client.get("/todos", params={"q": "계란"}).json()
    assert [t["id"] for t in found] == [2]


def test_search_is_case_insensitive(sample_todos):
    assert [t["id"] for t in client.get("/todos", params={"q": "docker"}).json()] == [3]
    assert [t["id"] for t in client.get("/todos", params={"q": "DOCKER"}).json()] == [3]


def test_search_with_no_match_returns_empty(sample_todos):
    assert client.get("/todos", params={"q": "없는단어"}).json() == []


def test_blank_search_is_ignored(sample_todos):
    """공백만 입력한 경우는 검색하지 않은 것으로 본다."""
    assert len(client.get("/todos", params={"q": "   "}).json()) == 3


def test_filter_by_completed(sample_todos):
    assert [t["id"] for t in client.get("/todos", params={"completed": True}).json()] == [2]
    assert [t["id"] for t in client.get("/todos", params={"completed": False}).json()] == [1, 3]


def test_search_and_filter_combined(sample_todos):
    """검색어와 완료 여부는 함께 적용된다."""
    assert client.get("/todos", params={"q": "복습", "completed": True}).json() == []
    assert [t["id"] for t in
            client.get("/todos", params={"q": "복습", "completed": False}).json()] == [3]


def test_search_rejects_too_long_query(sample_todos):
    """100자를 넘는 검색어는 422로 거부한다."""
    assert client.get("/todos", params={"q": "ㄱ" * 101}).status_code == 422


#페이지네이션 (v7.0.0)

@pytest.fixture
def many_todos():
    """페이지네이션 테스트용 10건."""
    save_todos([
        TodoItem(id=i, title=f"항목 {i}", description=f"설명 {i}",
                 completed=(i % 2 == 0))
        for i in range(1, 11)
    ])


def test_pagination_omitted_returns_all(many_todos):
    """limit/offset 을 안 주면 v6.0.0 과 똑같이 전체를 돌려준다."""
    assert len(client.get("/todos").json()) == 10


def test_limit_returns_first_n(many_todos):
    found = client.get("/todos", params={"limit": 3}).json()
    assert [t["id"] for t in found] == [1, 2, 3]


def test_offset_skips_from_front(many_todos):
    found = client.get("/todos", params={"offset": 7}).json()
    assert [t["id"] for t in found] == [8, 9, 10]


def test_limit_with_offset_walks_pages(many_todos):
    """limit 과 offset 을 함께 주면 페이지 단위로 넘어간다."""
    page1 = client.get("/todos", params={"limit": 4, "offset": 0}).json()
    page2 = client.get("/todos", params={"limit": 4, "offset": 4}).json()
    page3 = client.get("/todos", params={"limit": 4, "offset": 8}).json()
    assert [t["id"] for t in page1] == [1, 2, 3, 4]
    assert [t["id"] for t in page2] == [5, 6, 7, 8]
    assert [t["id"] for t in page3] == [9, 10]


def test_limit_larger_than_total_is_harmless(many_todos):
    assert len(client.get("/todos", params={"limit": 100}).json()) == 10


def test_offset_past_the_end_returns_empty(many_todos):
    """마지막 페이지를 넘어가면 404 가 아니라 빈 목록이다."""
    assert client.get("/todos", params={"offset": 50}).json() == []


def test_pagination_applies_after_filters(many_todos):
    """거르기를 먼저 하고, 그 결과를 자른다."""
    found = client.get(
        "/todos", params={"completed": True, "limit": 2}
    ).json()
    assert [t["id"] for t in found] == [2, 4]


def test_pagination_applies_after_search(many_todos):
    found = client.get("/todos", params={"q": "항목 1", "limit": 1}).json()
    assert [t["id"] for t in found] == [1]


def test_limit_zero_is_rejected(many_todos):
    """0건 요청은 의미가 없으므로 422 로 거부한다."""
    assert client.get("/todos", params={"limit": 0}).status_code == 422


def test_limit_above_cap_is_rejected(many_todos):
    """상한(100)을 넘는 요청은 거부한다 — 요청 하나가 서버를 묶지 못하게."""
    assert client.get("/todos", params={"limit": 101}).status_code == 422


def test_negative_offset_is_rejected(many_todos):
    """음수 offset 은 파이썬 슬라이스에서 뒤에서부터 세므로 반드시 막아야 한다."""
    assert client.get("/todos", params={"offset": -1}).status_code == 422


#선택 항목 일괄 삭제 (v8.0.0)

def test_delete_selected_removes_only_given_ids():
    save_todos([sample(1), sample(2), sample(3), sample(4)])
    response = client.delete("/todos", params={"ids": [1, 3]})
    assert response.status_code == 200
    assert response.json() == {"deleted": 2, "ids": [1, 3]}
    assert [t.id for t in load_todos()] == [2, 4]


def test_delete_selected_ignores_duplicate_ids():
    save_todos([sample(1), sample(2)])
    response = client.delete("/todos", params={"ids": [2, 2, 2]})
    assert response.json() == {"deleted": 1, "ids": [2]}
    assert [t.id for t in load_todos()] == [1]


def test_delete_selected_with_missing_id_deletes_nothing():
    """없는 id 가 섞이면 404 — 일부만 지워진 채 남지 않아야 한다."""
    save_todos([sample(1), sample(2)])
    response = client.delete("/todos", params={"ids": [1, 999]})
    assert response.status_code == 404
    assert "999" in response.json()["detail"]
    assert [t.id for t in load_todos()] == [1, 2]


def test_delete_todos_without_ids_rejected():
    """파라미터 없는 DELETE /todos 가 '전부 삭제' 로 동작하면 안 된다."""
    save_todos([sample(1)])
    assert client.delete("/todos").status_code == 422
    assert len(load_todos()) == 1


def test_delete_selected_rejects_too_many_ids():
    params = {"ids": list(range(1, main.MAX_BULK_DELETE + 2))}
    assert client.delete("/todos", params=params).status_code == 422


def test_delete_selected_writes_audit_log(caplog):
    save_todos([sample(1), sample(2), sample(3)])
    with caplog.at_level("INFO", logger="todo"):
        client.delete("/todos", params={"ids": [3, 1]})
    assert "todos deleted count=2 ids=[3, 1]" in caplog.text


#액세스 로그 (v8.0.0, Loki + Alloy)

# Grafana LogQL 의 pattern `<_> - "<method> <path> <_>" <status> <_>` 와 같은 형식
ACCESS_LINE = re.compile(
    r'^\S+ - "(?P<method>[A-Z]+) (?P<path>\S+) HTTP/[\d.]+" (?P<status>\d{3}) \d+\.\d{3}s$'
)


@pytest.fixture
def access_log(caplog, monkeypatch):
    """액세스 로거는 propagate=False 라 caplog 에 안 잡힌다. 테스트 동안만 전파를 켠다."""
    monkeypatch.setattr(main.access_logger, "propagate", True)
    with caplog.at_level("INFO", logger="custom.access"):
        yield caplog


def access_lines(caplog) -> list[re.Match]:
    lines = [r.getMessage() for r in caplog.records if r.name == "custom.access"]
    matches = [ACCESS_LINE.match(line) for line in lines]
    assert all(matches), lines                   # 한 줄이라도 형식이 다르면 Grafana 집계에서 빠진다
    return matches


def test_access_log_one_line_per_request(access_log):
    client.get("/todos", params={"q": "비밀검색어"})
    client.post("/todos", json={"title": "로그 확인"})
    client.delete("/todos/999")

    got = [(m["method"], m["path"], m["status"]) for m in access_lines(access_log)]
    assert got == [
        ("GET", "/todos", "200"),
        ("POST", "/todos", "201"),
        ("DELETE", "/todos/999", "404"),
    ]
    assert "비밀검색어" not in access_log.text   # 쿼리스트링(사용자 입력)은 남기지 않는다


def test_access_log_records_500_on_unhandled_error(access_log, monkeypatch):
    """처리 중 예외가 나도 요청이 로그에서 빠지지 않아야 Loki 로 장애를 추적할 수 있다."""
    def broken(*args, **kwargs):
        raise RuntimeError("storage down")

    monkeypatch.setattr(main, "query_todos", broken)
    failing_client = TestClient(app, raise_server_exceptions=False)
    assert failing_client.get("/todos").status_code == 500

    [m] = access_lines(access_log)
    assert (m["method"], m["path"], m["status"]) == ("GET", "/todos", "500")


def test_uvicorn_access_log_disabled():
    """켜져 있으면 요청 1건이 2줄로 남아 Path 별 호출 수가 2배가 된다."""
    assert logging.getLogger("uvicorn.access").disabled


#우선순위 + 정렬 (v9.0.0)

def test_create_todo_priority_defaults_to_medium():
    response = client.post("/todos", json={"title": "기본값"})
    assert response.status_code == 201
    assert response.json()["priority"] == "medium"


def test_create_todo_with_priority():
    response = client.post("/todos", json={"title": "급함", "priority": "high"})
    assert response.json()["priority"] == "high"
    assert load_todos()[0].priority == "high"


def test_invalid_priority_rejected():
    response = client.post("/todos", json={"title": "x", "priority": "urgent"})
    assert response.status_code == 422
    assert load_todos() == []                  # 잘못된 요청은 저장되지 않는다


def test_update_changes_priority():
    save_todos([sample()])
    response = client.put("/todos/1", json={"title": "Test", "priority": "low"})
    assert response.json()["priority"] == "low"
    assert load_todos()[0].priority == "low"


@pytest.fixture
def mixed_todos():
    """정렬 테스트용 — 일부러 id 순서와 우선순위·마감일 순서를 어긋나게 둔다."""
    save_todos([
        sample(1, "보통-마감없음", priority="medium"),
        sample(2, "낮음-11/30", priority="low", due_date="2026-11-30"),
        sample(3, "높음-12/10", priority="high", due_date="2026-12-10"),
        sample(4, "높음-11/25", priority="high", due_date="2026-11-25", completed=True),
        sample(5, "보통-11/30", priority="medium", due_date="2026-11-30"),
    ])


def ids(response) -> list[int]:
    return [t["id"] for t in response.json()]


def test_no_sort_keeps_insertion_order(mixed_todos):
    """sort 를 생략하면 기존처럼 추가한 순서(id)."""
    assert ids(client.get("/todos")) == [1, 2, 3, 4, 5]


def test_sort_by_priority(mixed_todos):
    """high → medium → low, 같은 우선순위는 마감일이 빠른 것 먼저(마감일 없으면 뒤)."""
    assert ids(client.get("/todos", params={"sort": "priority"})) == [4, 3, 5, 1, 2]


def test_sort_by_due_date(mixed_todos):
    """마감일이 빠른 것 먼저, 같은 날이면 우선순위 높은 것 먼저, 마감일 없으면 맨 뒤."""
    assert ids(client.get("/todos", params={"sort": "due_date"})) == [4, 5, 2, 3, 1]


def test_sort_ties_fall_back_to_id():
    """모든 조건이 같으면 id 순 — 요청마다 순서가 바뀌지 않아야 페이지가 겹치지 않는다."""
    save_todos([sample(i, f"같음{i}") for i in (3, 1, 2)])
    assert ids(client.get("/todos", params={"sort": "priority"})) == [1, 2, 3]
    assert ids(client.get("/todos", params={"sort": "due_date"})) == [1, 2, 3]


def test_sort_applies_before_pagination(mixed_todos):
    """정렬한 '다음' 잘라야 2페이지가 정렬 순서의 이어지는 부분이 된다."""
    first = ids(client.get("/todos", params={"sort": "priority", "limit": 2}))
    second = ids(client.get("/todos", params={"sort": "priority", "limit": 2, "offset": 2}))
    assert first + second == [4, 3, 5, 1]


def test_sort_combined_with_filter(mixed_todos):
    """완료 필터로 거른 뒤 정렬한다."""
    response = client.get("/todos", params={"sort": "priority", "completed": "false"})
    assert ids(response) == [3, 5, 1, 2]


def test_invalid_sort_rejected(mixed_todos):
    assert client.get("/todos", params={"sort": "title"}).status_code == 422
