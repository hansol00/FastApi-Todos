import json
import logging
import os
import tempfile
import threading
import time
from datetime import date, datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

APP_VERSION = "5.1.0"                            # v5.1.0: 정적분석 발견사항 개선
STARTED_AT = time.monotonic()                    # v3.0.0: 가동 시간(uptime) 계산 기준

logger = logging.getLogger("todo")               # v5.1.0: 감사 로그 (OWASP A09)

BASE_DIR = Path(__file__).resolve().parent       # main.py 가 있는 폴더
# v4.0.0: 데이터 위치를 환경변수로 분리. 컨테이너에서는 볼륨 경로를 주입한다.
DATA_DIR = Path(os.getenv("DATA_DIR", BASE_DIR))
TODO_FILE = DATA_DIR / "todo.json"
INDEX_FILE = BASE_DIR / "templates" / "index.html"

# v5.1.0: 읽기-수정-쓰기 구간 직렬화 (OWASP A04, CWE-362 Race Condition)
#         FastAPI 는 동기(def) 엔드포인트를 스레드풀에서 동시 실행하므로,
#         잠금이 없으면 두 요청이 같은 목록을 읽고 각자 덮어써 변경이 유실된다.
_lock = threading.Lock()


def ensure_todo_file() -> None:
    """데이터 파일이 없으면 빈 목록으로 만들어 둔다."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)   # v4.0.0: 볼륨 마운트 경로 대비
    if not TODO_FILE.exists():
        TODO_FILE.write_text("[]", encoding="utf-8")


ensure_todo_file()                               # 앱 기동 시 1회

app = FastAPI(title="To-Do List API", version=APP_VERSION)


class TodoIn(BaseModel):                         # 클라이언트가 보내는 데이터 (id 없음)
    title: str = Field(min_length=1, max_length=100)
    # v5.1.0: 길이 제한 추가 (OWASP A04, CWE-770) — title 과 검증 수준을 맞춤
    description: str = Field(default="", max_length=500)
    completed: bool = False
    due_date: date | None = None                 # v2.0.0: 마감일 (없으면 null)


class TodoItem(TodoIn):                          # 서버가 돌려주는 데이터 (id 있음)
    id: int


def load_todos() -> list[TodoItem]:
    raw = TODO_FILE.read_text(encoding="utf-8") if TODO_FILE.exists() else "[]"
    return [TodoItem(**t) for t in json.loads(raw)]


def save_todos(todos: list[TodoItem]) -> None:
    """임시 파일에 먼저 쓴 뒤 원자적으로 교체한다.

    v5.1.0 (OWASP A04): write_text() 는 파일을 먼저 비우므로, 쓰는 도중
    다른 요청이 읽으면 빈 파일이나 절반만 쓰인 파일을 보게 된다. 쓰는 중
    프로세스가 중단되면 파일이 영구 손상되어 모든 API 가 500 을 반환한다.
    os.replace() 는 같은 파일시스템 안에서 원자적으로 동작하므로,
    읽는 쪽은 언제나 완전한 파일만 보게 된다.
    """
    data = json.dumps(
        [t.model_dump(mode="json") for t in todos], indent=2, ensure_ascii=False
    )
    # 교체 대상과 같은 디렉터리여야 os.replace 가 원자적으로 동작한다
    fd, tmp = tempfile.mkstemp(dir=TODO_FILE.parent, prefix=".todo-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())                 # 디스크까지 내려쓰기
        os.replace(tmp, TODO_FILE)
    except BaseException:
        os.unlink(tmp)                           # 실패 시 임시 파일 정리
        raise


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
    with _lock:                                  # v5.1.0: 읽기-수정-쓰기를 한 덩어리로
        todos = load_todos()
        new_id = max((t.id for t in todos), default=0) + 1
        todo = TodoItem(id=new_id, **payload.model_dump())
        save_todos(todos + [todo])
    logger.info("todo created id=%s title=%r", todo.id, todo.title)
    return todo


@app.put(                                        # 수정
    "/todos/{todo_id}",
    responses={404: {"description": "To-Do item not found"}},  # v5.1.0: OpenAPI 문서화
)
def update_todo(todo_id: int, payload: TodoIn) -> TodoItem:
    with _lock:
        todos = load_todos()
        index = find_index(todos, todo_id)       # v5.1.0: 존재 확인을 객체 생성보다 먼저
        todo = TodoItem(id=todo_id, **payload.model_dump())
        todos[index] = todo
        save_todos(todos)
    logger.info("todo updated id=%s title=%r", todo.id, todo.title)
    return todo


@app.delete(                                     # 삭제
    "/todos/{todo_id}",
    status_code=204,
    responses={404: {"description": "To-Do item not found"}},
)
def delete_todo(todo_id: int) -> None:
    with _lock:
        todos = load_todos()
        removed = todos.pop(find_index(todos, todo_id))   # 로그에 남길 내용 확보
        save_todos(todos)
    logger.info("todo deleted id=%s title=%r", removed.id, removed.title)


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
    except Exception:                            # 저장소 이상 → 트래픽을 받으면 안 되는 상태
        # v5.1.0 (OWASP A05, CWE-209): 예외 원문에는 서버 절대경로·라이브러리 버전·
        #         저장된 데이터 값이 섞여 나오므로 응답에 넣지 않고 로그로만 남긴다.
        logger.exception("health check failed")
        body["status"] = "degraded"
        body["storage"] = "error"
        return JSONResponse(body, status_code=503)
    return JSONResponse(body, status_code=200)


@app.get("/", include_in_schema=False)           # 화면 서빙
def read_root() -> FileResponse:
    return FileResponse(INDEX_FILE, media_type="text/html")