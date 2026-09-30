import json
import os
import time
from datetime import date, datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

APP_VERSION = "3.0.0"                            # v3.0.0: 헬스체크에서 노출하는 앱 버전
STARTED_AT = time.monotonic()                    # v3.0.0: 가동 시간(uptime) 계산 기준

BASE_DIR = Path(__file__).resolve().parent       # main.py 가 있는 폴더
TODO_FILE = BASE_DIR / "todo.json"
INDEX_FILE = BASE_DIR / "templates" / "index.html"

if not TODO_FILE.exists():                       # 없으면 빈 목록으로 만들어 둔다
    TODO_FILE.write_text("[]", encoding="utf-8")

app = FastAPI(title="To-Do List API", version=APP_VERSION)


class TodoIn(BaseModel):                         # 클라이언트가 보내는 데이터 (id 없음)
    title: str = Field(min_length=1, max_length=100)
    description: str = ""
    completed: bool = False
    due_date: date | None = None                 # v2.0.0: 마감일 (없으면 null)


class TodoItem(TodoIn):                          # 서버가 돌려주는 데이터 (id 있음)
    id: int


def load_todos() -> list[TodoItem]:
    raw = TODO_FILE.read_text(encoding="utf-8") if TODO_FILE.exists() else "[]"
    return [TodoItem(**t) for t in json.loads(raw)]


def save_todos(todos: list[TodoItem]) -> None:
    data = json.dumps([t.model_dump(mode="json") for t in todos], indent=2, ensure_ascii=False)
    TODO_FILE.write_text(data, encoding="utf-8")


def find_index(todos: list[TodoItem], todo_id: int) -> int:
    for i, todo in enumerate(todos):
        if todo.id == todo_id:
            return i
    raise HTTPException(404, "To-Do item not found")


@app.get("/todos")                               # 목록 조회
def get_todos() -> list[TodoItem]:
    return load_todos()


@app.post("/todos", status_code=201)             # 추가 — id 는 서버가 매긴다
def create_todo(payload: TodoIn) -> TodoItem:
    todos = load_todos()
    new_id = max((t.id for t in todos), default=0) + 1
    todo = TodoItem(id=new_id, **payload.model_dump())
    save_todos(todos + [todo])
    return todo


@app.put("/todos/{todo_id}")                     # 수정
def update_todo(todo_id: int, payload: TodoIn) -> TodoItem:
    todos = load_todos()
    todo = TodoItem(id=todo_id, **payload.model_dump())
    todos[find_index(todos, todo_id)] = todo
    save_todos(todos)
    return todo


@app.delete("/todos/{todo_id}", status_code=204)  # 삭제
def delete_todo(todo_id: int) -> None:
    todos = load_todos()
    del todos[find_index(todos, todo_id)]
    save_todos(todos)


@app.get("/health")                              # v3.0.0: 헬스체크 — 컨테이너/로드밸런서가 상태를 확인
def health_check() -> JSONResponse:
    """서비스가 실제로 일할 수 있는 상태인지 점검한다.

    프로세스가 떠 있다는 사실만으로는 정상이라고 볼 수 없으므로,
    데이터 파일을 실제로 읽고 쓸 수 있는지까지 확인한 뒤 상태를 돌려준다.
    정상이면 200 OK("ok"), 데이터 저장소에 문제가 있으면 503("degraded").
    """
    body = {
        "status": "ok",
        "version": APP_VERSION,
        "uptime_seconds": round(time.monotonic() - STARTED_AT, 1),
        "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    try:
        todos = load_todos()                     # 읽기 가능 여부 확인
        if not os.access(TODO_FILE, os.W_OK):    # 쓰기 권한 확인
            raise PermissionError(f"{TODO_FILE.name} is not writable")
        body["storage"] = "ok"
        body["todo_count"] = len(todos)
    except Exception as exc:                     # 저장소 이상 → 트래픽을 받으면 안 되는 상태
        body["status"] = "degraded"
        body["storage"] = "error"
        body["detail"] = f"{type(exc).__name__}: {exc}"
        return JSONResponse(body, status_code=503)
    return JSONResponse(body, status_code=200)


@app.get("/", include_in_schema=False)           # 화면 서빙
def read_root() -> FileResponse:
    return FileResponse(INDEX_FILE, media_type="text/html")