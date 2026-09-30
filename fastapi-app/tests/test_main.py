"""To-Do List API 테스트.

실행: 저장소 루트에서  pytest fastapi-app/tests --cov=main --cov-report=term
"""

import json

import pytest
from fastapi.testclient import TestClient

import main
from main import TodoItem, app, load_todos, save_todos

client = TestClient(app)


@pytest.fixture(autouse=True)
def isolated_todo_file(tmp_path, monkeypatch):
    """테스트마다 임시 todo.json 을 쓰도록 바꾼다.

    실제 데이터(fastapi-app/todo.json)를 건드리지 않고, 테스트 간에
    상태가 새지 않게 격리한다. monkeypatch 와 tmp_path 는 테스트가
    끝나면 자동으로 원래 상태로 되돌린다.
    """
    monkeypatch.setattr(main, "TODO_FILE", tmp_path / "todo.json")
    save_todos([])
    yield


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


def test_health_degraded_on_corrupt_file():
    """JSON 이 깨져 있으면 degraded 로 잡아야 한다."""
    main.TODO_FILE.write_text("{ 깨진 JSON", encoding="utf-8")
    response = client.get("/health")
    assert response.status_code == 503
    assert "JSONDecodeError" in response.json()["detail"]


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