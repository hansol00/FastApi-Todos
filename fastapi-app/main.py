import calendar
import logging
import sys
import time
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from prometheus_fastapi_instrumentator import Instrumentator   # v6.0.0: 메트릭 수집
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import case, delete, func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError
from sqlalchemy.orm import Session

from db import TodoRow, get_session, init_db     # v10.0.0: 저장소를 파일 → DB 로

APP_VERSION = "10.0.0"                           # v10.0.0: todo.json → PostgreSQL, 반복 일정
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
INDEX_FILE = BASE_DIR / "templates" / "index.html"

# v10.0.0: v5.1.0 의 threading.Lock 과 원자적 파일 교체(os.replace)는 없앴다.
#   동시성은 DB 트랜잭션이 맡는다 — 잠금은 프로세스 하나 안에서만 통하므로
#   uvicorn 워커를 늘리거나 컨테이너를 2개 띄우면 파일 방식은 변경이 유실된다.

SessionDep = Annotated[Session, Depends(get_session)]


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """v10.0.0: 기동 시 테이블 준비 (모듈 최상단에서 하면 import 만 해도 DB 에 붙으려 한다)."""
    init_db()
    yield


app = FastAPI(title="To-Do List API", version=APP_VERSION, lifespan=lifespan)

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


# v9.0.0: 우선순위. 문자열 3개로만 받는다 (그 밖의 값은 422).
Priority = Literal["high", "medium", "low"]
PRIORITY_RANK: dict[str, int] = {"high": 0, "medium": 1, "low": 2}   # 정렬 순서 (작을수록 앞)

# v10.0.0: 반복 일정. 완료하면 다음 회차가 새 항목으로 생긴다.
Recurrence = Literal["none", "daily", "weekly", "monthly"]


class TodoIn(BaseModel):                         # 클라이언트가 보내는 데이터 (id 없음)
    title: str = Field(min_length=1, max_length=100)
    # v5.1.0: 길이 제한 추가 (OWASP A04, CWE-770) — title 과 검증 수준을 맞춤
    description: str = Field(default="", max_length=500)
    completed: bool = False
    due_date: date | None = None                 # v2.0.0: 마감일 (없으면 null)
    # v9.0.0: 기본값 medium — 필드가 없는 기존 todo.json 도 그대로 읽힌다(마이그레이션 불필요)
    priority: Priority = "medium"
    # v10.0.0: 반복 주기. 기본값 none — 필드가 없는 예전 요청·todo.json 도 그대로 받는다
    recurrence: Recurrence = "none"

    @model_validator(mode="after")
    def recurring_needs_due_date(self) -> "TodoIn":
        """다음 회차 날짜를 마감일에서 계산하므로, 반복 일정에는 마감일이 있어야 한다."""
        if self.recurrence != "none" and self.due_date is None:
            raise ValueError("반복 일정에는 마감일(due_date)이 필요합니다")
        return self


class TodoItem(TodoIn):                          # 서버가 돌려주는 데이터 (id 있음)
    model_config = ConfigDict(from_attributes=True)   # v10.0.0: DB 행(TodoRow)에서 바로 변환
    id: int


NOT_FOUND = "To-Do item not found"


# v10.0.0: DB 장애는 500(서버 버그)이 아니라 503(일시적으로 처리 불가)으로 돌려준다.
#   - OperationalError: DB 가 꺼졌거나 연결이 끊김, statement_timeout 초과
#   - PoolTimeoutError: 커넥션 풀이 모두 사용 중이라 DB_POOL_TIMEOUT 안에 못 받음
#   Retry-After 로 클라이언트에게 '잠시 후 다시' 를 알린다. 예외 원문(호스트·SQL)은 응답에
#   넣지 않고 로그로만 남긴다 (OWASP A05, CWE-209 — /health 와 같은 원칙).
@app.exception_handler(OperationalError)
@app.exception_handler(PoolTimeoutError)
async def database_unavailable(request: Request, exc: Exception) -> JSONResponse:
    logger.error("database unavailable: %s %s (%s)", request.method, request.url.path,
                 type(exc).__name__)
    return JSONResponse({"detail": "Database unavailable"}, status_code=503,
                        headers={"Retry-After": "5"})


def next_due(due: date, recurrence: str) -> date:
    """v10.0.0: 다음 회차 마감일.

    매월은 같은 '일' 로 넘기되, 그 달에 그날이 없으면 말일로 맞춘다 (1/31 → 2/28, 윤년 2/29).
    """
    if recurrence == "daily":
        return due + timedelta(days=1)
    if recurrence == "weekly":
        return due + timedelta(weeks=1)
    year, month = (due.year + 1, 1) if due.month == 12 else (due.year, due.month + 1)
    return due.replace(year=year, month=month, day=min(due.day, calendar.monthrange(year, month)[1]))


def to_item(row: TodoRow) -> TodoItem:
    return TodoItem.model_validate(row)


def order_by_for(sort: str | None) -> list:
    """v9.0.0 정렬 규칙을 SQL ORDER BY 로 옮긴 것 (v10.0.0).

    - priority: high → medium → low. 같으면 마감일이 빠른 것, 그다음 id 순.
    - due_date: 마감일이 빠른 것부터, 마감일 없는 항목은 맨 뒤. 같으면 우선순위, 그다음 id 순.
    - 생략: 추가한 순서(id).
    마지막 기준을 id 로 두어 같은 조건이면 언제나 같은 순서가 나오게 한다.
    순서가 요청마다 바뀌면 limit/offset 으로 페이지를 넘길 때 항목이 빠지거나 겹친다.
    (DB 는 ORDER BY 가 없으면 순서를 보장하지 않는다 — 파일 시절의 '저장된 순서' 같은 건 없다)

    마감일 없음을 맨 뒤로: NULLS LAST 는 DB 마다 기본값이 달라(PostgreSQL ASC 는 NULL 이 뒤,
    SQLite 는 앞) `due_date IS NULL` 을 먼저 정렬 키로 둬서 두 DB 결과를 같게 만든다.
    """
    rank = case(PRIORITY_RANK, value=TodoRow.priority)
    due = [TodoRow.due_date.is_(None), TodoRow.due_date]
    if sort == "priority":
        return [rank, *due, TodoRow.id]
    if sort == "due_date":
        return [*due, rank, TodoRow.id]
    return [TodoRow.id]


def query_todos(
    session: Session,
    q: str | None = None,
    completed: bool | None = None,
    sort: str | None = None,
    limit: int | None = None,
    offset: int = 0,
) -> list[TodoItem]:
    """거르기 → 정렬 → 자르기를 SQL 한 번으로 처리한다 (v10.0.0).

    파일 방식은 매 요청마다 전체를 읽어 파이썬에서 걸렀다. 이제 DB 가 필요한 행만 돌려준다.
    """
    stmt = select(TodoRow)
    if q and (needle := q.strip()):
        # autoescape: 검색어 속 % 와 _ 를 LIKE 와일드카드가 아닌 글자 그대로 찾는다.
        #   빠뜨리면 '100%' 검색이 '100' 으로 시작하는 모든 항목에 걸린다.
        stmt = stmt.where(
            TodoRow.title.icontains(needle, autoescape=True)
            | TodoRow.description.icontains(needle, autoescape=True)
        )
    if completed is not None:
        stmt = stmt.where(TodoRow.completed.is_(completed))
    stmt = stmt.order_by(*order_by_for(sort)).offset(offset).limit(limit)
    return [to_item(row) for row in session.scalars(stmt)]


@app.get("/todos")                               # 목록 조회 — v6.0.0: 검색·상태 필터
def get_todos(
    session: SessionDep,
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
    sort: Literal["priority", "due_date"] | None = Query(
        default=None,
        description="정렬 기준. 생략하면 추가한 순서(id)",
    ),
) -> list[TodoItem]:
    """검색어·완료 여부로 거르고, 필요하면 잘라서 돌려준다.

    v6.0.0: 검색(q)과 완료 여부(completed) 필터.
    v7.0.0: limit/offset 페이지네이션.
    v9.0.0: sort 정렬. 거른 '다음', 자르기 '전에' 정렬해야 페이지가 정렬 순서대로 나뉜다.
        JMeter 부하 테스트에서 `GET /todos` 가 가장 무거운 요청으로 나왔다.
        항목이 늘수록 전체를 직렬화해 내려주기 때문에, 응답 시간과 전송량이
        데이터 건수에 그대로 비례한다. 화면은 한 번에 수십 개만 보여주므로
        서버가 잘라서 주는 편이 맞다.

        모든 파라미터가 선택적이라 기존 `GET /todos` 동작은 그대로다.
        le=100 으로 상한을 둔 것은 거르기 전 전체를 요구하는 요청 하나가
        서버를 묶어두지 못하게 하기 위함이다 (OWASP A04, CWE-770).
    v10.0.0: 같은 순서(거르기 → 정렬 → 자르기)를 SQL 로 옮겼다 → query_todos.
    """
    return query_todos(session, q=q, completed=completed, sort=sort, limit=limit, offset=offset)


@app.post("/todos", status_code=201)             # 추가 — id 는 서버가 매긴다
def create_todo(payload: TodoIn, session: SessionDep) -> TodoItem:
    # v10.0.0: id 는 DB 시퀀스가 매긴다. 지운 id 는 다시 쓰지 않는다 —
    #   파일 시절(max+1)에는 마지막 항목을 지우면 같은 id 가 새 항목에 붙어,
    #   오래된 화면에서 '삭제' 를 누르면 엉뚱한 새 항목이 지워질 수 있었다.
    row = TodoRow(**payload.model_dump())
    session.add(row)
    session.commit()
    todo = to_item(row)
    logger.info("todo created id=%s title=%r priority=%s", todo.id, todo.title, todo.priority)
    return todo


@app.put(                                        # 수정
    "/todos/{todo_id}",
    responses={404: {"description": "To-Do item not found"}},  # v5.1.0: OpenAPI 문서화
)
def update_todo(todo_id: int, payload: TodoIn, session: SessionDep) -> TodoItem:
    row = session.get(TodoRow, todo_id, with_for_update=True)   # 커밋까지 행 잠금
    if row is None:
        raise HTTPException(404, NOT_FOUND)
    was_completed = row.completed
    for field, value in payload.model_dump().items():
        setattr(row, field, value)
    nxt = spawn_next(row) if row.completed and not was_completed else None
    if nxt is not None:
        session.add(nxt)
    session.commit()                             # 완료 처리와 다음 회차 생성은 한 트랜잭션
    todo = to_item(row)
    logger.info("todo updated id=%s title=%r", todo.id, todo.title)
    if nxt is not None:
        logger.info("todo repeated id=%s next_id=%s due=%s", todo.id, nxt.id, nxt.due_date)
    return todo


def spawn_next(row: TodoRow) -> TodoRow | None:
    """v10.0.0: 반복 일정을 '방금' 완료했으면 다음 회차를 만든다.

    - 완료된 회차는 반복을 끄고 기록으로만 남긴다. 그래야 체크를 풀었다가 다시 체크해도
      다음 회차가 또 생기지 않는다 (반복은 언제나 가장 최근 회차 하나만 갖는다).
    - 마감일이 너무 멀어 다음 날짜를 만들 수 없으면(9999-12-31 근처) 422.
    """
    if row.recurrence == "none":
        return None
    try:
        due = next_due(row.due_date, row.recurrence)
    except (OverflowError, ValueError):
        raise HTTPException(422, "다음 회차 마감일을 계산할 수 없습니다") from None
    nxt = TodoRow(title=row.title, description=row.description, priority=row.priority,
                  recurrence=row.recurrence, due_date=due, completed=False)
    row.recurrence = "none"
    return nxt


@app.delete(                                     # 삭제
    "/todos/{todo_id}",
    status_code=204,
    responses={404: {"description": "To-Do item not found"}},
)
def delete_todo(todo_id: int, session: SessionDep) -> None:
    # 조회 후 삭제가 아니라 DELETE ... RETURNING 한 번으로: 두 요청이 동시에 같은 항목을
    # 지우면 한쪽만 행을 돌려받고, 다른 쪽은 404 가 된다.
    removed = session.execute(
        delete(TodoRow).where(TodoRow.id == todo_id).returning(TodoRow.id, TodoRow.title)
    ).first()
    if removed is None:
        raise HTTPException(404, NOT_FOUND)
    session.commit()
    logger.info("todo deleted id=%s title=%r", removed.id, removed.title)


MAX_BULK_DELETE = 100                            # v8.0.0: 한 번에 지울 수 있는 최대 개수


@app.delete(                                     # v8.0.0: 선택한 항목 일괄 삭제
    "/todos",
    responses={404: {"description": "없는 id 가 섞여 있음 (아무것도 지우지 않음)"}},
)
def delete_selected_todos(
    session: SessionDep,
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

    v10.0.0: 일단 지워 보고(DELETE ... RETURNING) 지워진 개수가 모자라면 롤백한다.
      '있는지 확인 → 삭제' 두 단계로 하면 그 사이에 다른 요청이 지웠을 때 일부만 지워진다.
    """
    wanted = list(dict.fromkeys(ids))            # 중복 제거 (순서 유지)
    deleted = set(session.scalars(
        delete(TodoRow).where(TodoRow.id.in_(wanted)).returning(TodoRow.id)
    ))
    if missing := [i for i in wanted if i not in deleted]:
        session.rollback()                       # 하나라도 없으면 아무것도 지우지 않는다
        raise HTTPException(404, f"{NOT_FOUND}: {missing}")
    session.commit()
    logger.info("todos deleted count=%d ids=%s", len(wanted), wanted)
    return {"deleted": len(wanted), "ids": wanted}


@app.get("/health")                              # v3.0.0: 헬스체크 — 컨테이너/로드밸런서가 상태를 확인
def health_check(session: SessionDep) -> JSONResponse:
    """서비스가 실제로 일할 수 있는 상태인지 점검한다.

    프로세스가 떠 있다는 사실만으로는 정상이라고 볼 수 없으므로,
    저장소에 실제로 질의가 되는지까지 확인한 뒤 상태를 돌려준다.
    정상이면 200 OK("ok"), 데이터 저장소에 문제가 있으면 503("degraded").
    v10.0.0: 파일 읽기·쓰기 권한 확인 → DB 에 COUNT 질의.
    """
    body = {
        "status": "ok",
        "version": APP_VERSION,
        "uptime_seconds": round(time.monotonic() - STARTED_AT, 1),
        "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    try:
        count = session.scalar(select(func.count()).select_from(TodoRow))
        body["storage"] = "ok"
        body["database"] = session.get_bind().dialect.name   # postgresql / sqlite
        body["todo_count"] = count
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