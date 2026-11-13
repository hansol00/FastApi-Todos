"""To-Do List API 테스트.

실행: 저장소 루트에서  pytest fastapi-app/tests --cov=main --cov-report=term
"""

import json
import logging
import re

import pytest
from fastapi.testclient import TestClient

import main
from main import TodoItem, app, load_todos, save_todos

client = TestClient(app)


@pytest.fixture(autouse=True)
def isolated_todo_file(tmp_path, monkeypatch):
    """테스트마다 임시 todo.json 을 쓰도록 바꾼다.

    실제 데이터(fastapi-app/todo.json)를 건드리지 않고, 테스트 간에
    상태가 새지 않게 격리한다. monkeypatch 와 tmp_path 가 테스트가
    끝나면 자동으로 되돌리므로 teardown 이 필요 없다. (v5.1.0: 불필요한 yield 제거)
    """
    # v5.1.0: save_todos 의 임시 파일과 ensure_todo_file 의 mkdir 도 tmp_path 안에서 일어나야 한다
    monkeypatch.setattr(main, "DATA_DIR", tmp_path)
    monkeypatch.setattr(main, "TODO_FILE", tmp_path / "todo.json")
    save_todos([])


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
    assert len(load_todos()) == 1              # 파일에도 저장됐는지 확인


def test_create_todo_assigns_sequential_ids():
    client.post("/todos", json={"title": "첫 번째"})
    response = client.post("/todos", json={"title": "두 번째"})
    assert response.json()["id"] == 2
    assert [t.id for t in load_todos()] == [1, 2]


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


#저장소 (load/save_todos)

def test_load_todos_when_file_missing():
    """파일이 없어도 빈 목록으로 동작해야 한다."""
    main.TODO_FILE.unlink()
    assert load_todos() == []


def test_saved_file_is_readable_json():
    """저장 파일이 사람이 읽을 수 있는 UTF-8 JSON 인지 확인."""
    save_todos([sample(1, "한글 제목")])
    raw = main.TODO_FILE.read_text(encoding="utf-8")
    assert "한글 제목" in raw                   # ensure_ascii=False 확인
    assert json.loads(raw)[0]["id"] == 1


def test_save_todos_is_atomic(monkeypatch):
    """v5.1.0 (H-1): 교체가 실패해도 기존 파일이 온전하고 임시 파일이 남지 않아야 한다."""
    save_todos([sample(1, "이전 데이터")])
    original = main.TODO_FILE.read_text(encoding="utf-8")

    def fail_replace(src, dst):
        raise OSError("replace failed")

    # monkeypatch 는 테스트 종료 시 자동 복원된다 (전역 상태를 직접 건드리지 않음)
    monkeypatch.setattr(main.os, "replace", fail_replace)

    # v5.1.0 (python:S5778): 예외를 던질 수 있는 호출이 블록 안에 하나만 있도록 분리
    new_todos = [sample(2, "새 데이터")]
    with pytest.raises(OSError):
        save_todos(new_todos)

    # 교체가 실패했으므로 기존 내용이 그대로 남아 있어야 한다
    assert main.TODO_FILE.read_text(encoding="utf-8") == original
    # 임시 파일도 남기지 않아야 한다
    assert list(main.TODO_FILE.parent.glob(".todo-*.json")) == []


#헬스체크

def test_health_ok():
    save_todos([sample(1), sample(2)])
    response = client.get("/health")
    body = response.json()
    assert response.status_code == 200
    assert body["status"] == "ok"
    assert body["storage"] == "ok"
    assert body["todo_count"] == 2
    assert body["version"] == main.APP_VERSION
    assert body["uptime_seconds"] >= 0


def test_health_degraded_when_file_missing():
    """데이터 파일이 사라지면 503 으로 트래픽을 거절해야 한다."""
    main.TODO_FILE.unlink()
    response = client.get("/health")
    assert response.status_code == 503
    assert response.json()["status"] == "degraded"
    assert response.json()["storage"] == "error"


def test_health_degraded_on_corrupt_file(caplog):
    """JSON 이 깨져 있으면 degraded 로 잡되, 예외 원문은 노출하지 않아야 한다."""
    main.TODO_FILE.write_text("{ 깨진 JSON", encoding="utf-8")
    response = client.get("/health")
    assert response.status_code == 503
    assert response.json()["storage"] == "error"
    # v5.1.0 (L-1): 예외 상세는 응답이 아니라 서버 로그에만 남는다
    assert "detail" not in response.json()
    assert "JSONDecodeError" in caplog.text


#화면 서빙

def test_read_root_serves_html():
    response = client.get("/")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")


#데이터 파일 초기화

def test_ensure_todo_file_creates_missing_file():
    """파일이 없으면 빈 목록으로 새로 만든다."""
    main.TODO_FILE.unlink()
    main.ensure_todo_file()
    assert main.TODO_FILE.read_text(encoding="utf-8") == "[]"


def test_ensure_todo_file_keeps_existing_data():
    """이미 파일이 있으면 덮어쓰지 않는다."""
    main.TODO_FILE.write_text('[{"id": 1, "title": "기존 데이터"}]', encoding="utf-8")
    main.ensure_todo_file()
    assert "기존 데이터" in main.TODO_FILE.read_text(encoding="utf-8")

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


#완료 항목 일괄 삭제 (v8.0.0)

def test_delete_completed_removes_only_completed():
    save_todos([
        sample(1, completed=True),
        sample(2),
        sample(3, completed=True),
        sample(4),
    ])
    response = client.delete("/todos", params={"completed": "true"})
    assert response.status_code == 200
    assert response.json() == {"deleted": 2, "ids": [1, 3]}
    assert [t.id for t in load_todos()] == [2, 4]


def test_delete_completed_when_none_completed(monkeypatch):
    save_todos([sample(1), sample(2)])
    writes = []
    monkeypatch.setattr(main, "save_todos", lambda todos: writes.append(todos))
    response = client.delete("/todos", params={"completed": "true"})
    assert response.json() == {"deleted": 0, "ids": []}
    assert writes == []                          # 지울 게 없으면 파일을 다시 쓰지 않는다
    assert len(load_todos()) == 2


def test_delete_completed_false_rejected():
    save_todos([sample(1), sample(2)])
    response = client.delete("/todos", params={"completed": "false"})
    assert response.status_code == 400
    assert len(load_todos()) == 2                # 진행 중 항목은 그대로


def test_delete_todos_without_param_rejected():
    """파라미터 없는 DELETE /todos 가 '전부 삭제' 로 동작하면 안 된다."""
    save_todos([sample(1, completed=True)])
    assert client.delete("/todos").status_code == 422
    assert len(load_todos()) == 1


def test_delete_completed_writes_audit_log(caplog):
    save_todos([sample(1, completed=True), sample(2), sample(3, completed=True)])
    with caplog.at_level("INFO", logger="todo"):
        client.delete("/todos", params={"completed": "true"})
    assert "todos cleared completed count=2 ids=[1, 3]" in caplog.text


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
    def broken():
        raise RuntimeError("storage down")

    monkeypatch.setattr(main, "load_todos", broken)
    failing_client = TestClient(app, raise_server_exceptions=False)
    assert failing_client.get("/todos").status_code == 500

    [m] = access_lines(access_log)
    assert (m["method"], m["path"], m["status"]) == ("GET", "/todos", "500")


def test_uvicorn_access_log_disabled():
    """켜져 있으면 요청 1건이 2줄로 남아 Path 별 호출 수가 2배가 된다."""
    assert logging.getLogger("uvicorn.access").disabled
