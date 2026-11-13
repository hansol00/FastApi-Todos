import json
import logging
import os
import sys
import tempfile
import threading
import time
from datetime import date, datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from prometheus_fastapi_instrumentator import Instrumentator   # v6.0.0: 메트릭 수집
from pydantic import BaseModel, Field

APP_VERSION = "8.0.0"                            # v8.0.0: Loki + Alloy 로그 수집
STARTED_AT = time.monotonic()                    # v3.0.0: 가동 시간(uptime) 계산 기준

logger = logging.getLogger("todo")               # v5.1.0: 감사 로그 (OWASP A09)

# v8.0.0: 로그는 표준 출력(stdout)으로만 내보낸다.
#   Docker 가 stdout 을 컨테이너 로그로 저장하고, Alloy 가 그 로그를 읽어 Loki 로 보낸다.
#   앱은 Loki 주소를 모른다 — 수집·전송은 전부 Alloy 몫이다.
#
#   감사 로거(todo)는 v5.1.0 부터 있었지만 핸들러가 없어 INFO 가 실제로는 출력되지 않았다.
#   이번에 stdout 으로 연결해 생성·수정·삭제 이력도 Loki 에서 검색할 수 있게 한다.
#   propagate 는 그대로 둔다 (uvicorn 은 루트 로거에 핸들러를 달지 않아 중복 출력이 없고,
#   pytest 의 caplog 는 루트로 전파된 기록을 잡는다).
_audit_handler = logging.StreamHandler(sys.stdout)
_audit_handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
logger.addHandler(_audit_handler)
logger.setLevel(logging.INFO)

# v8.0.0: 액세스 로그 — 요청 1건당 1줄. 형식은 Grafana LogQL pattern 파서 기준이므로 바꾸지 않는다.
#   IP - "METHOD PATH HTTP/버전" 상태코드 응답시간
access_logger = logging.getLogger("custom.access")
access_logger.setLevel(logging.INFO)
access_logger.addHandler(logging.StreamHandler(sys.stdout))
access_logger.propagate = False                  # 상위 로거로 넘겨 두 번 찍히는 것을 방지

# v8.0.0: uvicorn 기본 액세스 로그 끄기 (아래 미들웨어가 대신 기록).
#   켜 두면 요청 1번에 2줄이 남아 Grafana 의 Path 별 호출 수가 2배가 된다.
logging.getLogger("uvicorn.access").disabled = True

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

# v6.0.0: 요청 수·응답 시간을 자동 수집해 /metrics 로 공개한다 (Prometheus 가 수집).
#   excluded_handlers: /metrics 자신과 헬스체크(30초마다 호출)를 집계에서 빼
#   실제 사용자 트래픽만 그래프에 남도록 한다.
#   should_group_status_codes: 200/201/204 를 "2xx" 로 묶는다 (과제 PromQL 기준).
Instrumentator(
    should_group_status_codes=True,
    excluded_handlers=["/metrics", "/health"],
).instrument(app).expose(app, endpoint="/metrics", include_in_schema=False)


@app.middleware("http")                          # v8.0.0: 모든 요청을 한 줄씩 기록
async def log_requests(request: Request, call_next):
    """요청 1건을 액세스 로그 1줄로 남긴다.

    - 처리 중 예외가 나도 finally 에서 500 으로 기록한다. 장애 요청이 로그에서
      빠지면 Loki 로 원인을 찾을 수 없다.
    - 경로만 남기고 쿼리스트링(?q=검색어)은 남기지 않는다. 검색어는 사용자 입력이라
      로그에 쌓을 이유가 없고, Path 별 집계도 쿼리스트링이 섞이면 쪼개진다.
    - /health(30초마다)와 /metrics(15초마다)도 기록은 한다. 집계에서 뺄지는
      Grafana 쿼리에서 정한다 (Instrumentator 의 excluded_handlers 와 같은 기준).
    """
    start = time.perf_counter()
    status = 500
    try:
        response = await call_next(request)
        status = response.status_code
        return response
    finally:
        client = request.client.host if request.client else "-"
        access_logger.info(
            '%s - "%s %s HTTP/%s" %d %.3fs',
            client,
            request.method,
            request.url.path,
            request.scope.get("http_version", "1.1"),
            status,
            time.perf_counter() - start,
        )


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


@app.get("/todos")                               # 목록 조회 — v6.0.0: 검색·상태 필터
def get_todos(
    q: str | None = Query(
        default=None,
        max_length=100,                          # 길이 제한 (OWASP A04, CWE-770)
        description="제목·설명 부분 일치 검색어 (대소문자 무시)",
    ),
    completed: bool | None = Query(
        default=None, description="완료 여부로 거르기. 생략하면 전체"
    ),
    limit: int | None = Query(
        default=None,
        ge=1,
        le=100,                                  # 한 번에 받아갈 수 있는 최대치를 서버가 정한다
        description="한 번에 돌려줄 최대 개수. 생략하면 전체",
    ),
    offset: int = Query(
        default=0, ge=0, description="건너뛸 개수. limit 과 함께 페이지를 넘긴다"
    ),
) -> list[TodoItem]:
    """검색어·완료 여부로 거르고, 필요하면 잘라서 돌려준다.

    v6.0.0: 검색(q)과 완료 여부(completed) 필터.
    v7.0.0: limit/offset 페이지네이션.
        JMeter 부하 테스트에서 `GET /todos` 가 가장 무거운 요청으로 나왔다.
        항목이 늘수록 전체를 직렬화해 내려주기 때문에, 응답 시간과 전송량이
        데이터 건수에 그대로 비례한다. 화면은 한 번에 수십 개만 보여주므로
        서버가 잘라서 주는 편이 맞다.

        네 파라미터 모두 선택적이라 기존 `GET /todos` 동작은 그대로다.
        le=100 으로 상한을 둔 것은 거르기 전 전체를 요구하는 요청 하나가
        서버를 묶어두지 못하게 하기 위함이다 (OWASP A04, CWE-770).
    """
    todos = load_todos()
    if q and (needle := q.strip().casefold()):
        todos = [
            t for t in todos
            if needle in t.title.casefold() or needle in t.description.casefold()
        ]
    if completed is not None:
        todos = [t for t in todos if t.completed is completed]
    if offset:
        todos = todos[offset:]
    if limit is not None:
        todos = todos[:limit]
    return todos


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


MAX_BULK_DELETE = 100                            # v8.0.0: 한 번에 지울 수 있는 최대 개수


@app.delete(                                     # v8.0.0: 선택한 항목 일괄 삭제
    "/todos",
    responses={404: {"description": "없는 id 가 섞여 있음 (아무것도 지우지 않음)"}},
)
def delete_selected_todos(
    ids: list[int] = Query(
        min_length=1,                            # 빈 요청이 '전부 삭제' 로 오해되지 않게 필수
        max_length=MAX_BULK_DELETE,              # 요청 하나가 서버를 오래 붙잡지 못하게 (OWASP A04, CWE-770)
        description="지울 할 일 id. 여러 개는 ?ids=1&ids=3 처럼 반복",
    ),
) -> dict:
    """화면에서 고른 할 일 여러 개를 한 번에 지운다.

    v8.0.0: 하나씩 지우면 요청이 N 번 오가고, 중간에 실패하면 일부만 지워진 채 남는다.

    - 전부 있거나 전부 안 지우거나(원자적). 없는 id 가 하나라도 있으면 404 를 돌려주고
      아무것도 지우지 않는다. 다른 사람이 먼저 지운 항목이 섞여 있다는 뜻이라
      화면을 새로 고친 뒤 다시 고르게 한다.
    - 같은 id 가 여러 번 와도 한 번만 지운다.
    - 지운 id 를 감사 로그에 남긴다 → Loki 에서 `|= "todos deleted"` 로 이력 조회.
    """
    wanted = list(dict.fromkeys(ids))            # 중복 제거 (순서 유지)
    with _lock:
        todos = load_todos()
        existing = {t.id for t in todos}
        missing = [i for i in wanted if i not in existing]
        if missing:
            raise HTTPException(404, f"To-Do item not found: {missing}")
        remove = set(wanted)
        save_todos([t for t in todos if t.id not in remove])
    logger.info("todos deleted count=%d ids=%s", len(wanted), wanted)
    return {"deleted": len(wanted), "ids": wanted}


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