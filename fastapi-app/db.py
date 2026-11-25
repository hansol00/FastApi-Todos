"""v10.0.0: 데이터 저장소 — todo.json 파일 대신 관계형 DB (운영: PostgreSQL).

연결 주소는 환경변수로 정한다 (우선순위 순).
  1) DATABASE_URL           SQLAlchemy URL 그대로  (예: postgresql+psycopg://user:pw@host/db)
  2) DB_HOST (+ DB_PORT, DB_USER, DB_PASSWORD, DB_NAME)
                            컨테이너 배포용. 비밀번호에 특수문자가 있어도 URL.create 가 이스케이프한다.
  3) 둘 다 없으면           DATA_DIR/todo.db (SQLite) — DB 서버 없이 로컬에서 바로 실행할 때
"""

import logging
import os
import time
from collections.abc import Iterator, Mapping
from datetime import date
from pathlib import Path

from sqlalchemy import (
    URL,
    Boolean,
    CheckConstraint,
    Date,
    Engine,
    Integer,
    String,
    create_engine,
    text,
)
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

logger = logging.getLogger("todo")

BASE_DIR = Path(__file__).resolve().parent
PRIORITIES = ("high", "medium", "low")


class Base(DeclarativeBase):
    pass


class TodoRow(Base):
    """todos 테이블. API 의 TodoItem 과 컬럼이 1:1 로 대응한다."""

    __tablename__ = "todos"
    __table_args__ = (
        # API(pydantic)에서 이미 막지만, DB 에 직접 들어오는 값(psql, 이관 스크립트)도 막는다.
        CheckConstraint(f"priority IN {PRIORITIES}", name="ck_todos_priority"),
        # SQLite 는 기본적으로 '현재 최대 id + 1' 을 주므로 마지막 항목을 지우면 그 id 가 재사용된다.
        # PostgreSQL 시퀀스는 재사용하지 않는다. 단위 테스트(SQLite)와 운영(PostgreSQL)의 동작을
        # 맞추려고 SQLite 에도 AUTOINCREMENT 를 켠다.
        {"sqlite_autoincrement": True},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(100))
    description: Mapped[str] = mapped_column(String(500), default="")
    completed: Mapped[bool] = mapped_column(Boolean, default=False)
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    priority: Mapped[str] = mapped_column(String(6), default="medium")


class AppMeta(Base):
    """한 번만 해야 하는 작업의 기록 (예: todo.json 이관 완료 여부)."""

    __tablename__ = "app_meta"

    key: Mapped[str] = mapped_column(String(50), primary_key=True)
    value: Mapped[str] = mapped_column(String(200))


def build_url(env: Mapping[str, str]) -> URL | str:
    """환경변수에서 DB 접속 주소를 만든다 (우선순위는 모듈 설명 참고)."""
    if env.get("DATABASE_URL"):
        return env["DATABASE_URL"]
    if env.get("DB_HOST"):
        return URL.create(
            "postgresql+psycopg",
            username=env.get("DB_USER", "todo"),
            password=env.get("DB_PASSWORD"),
            host=env["DB_HOST"],
            port=int(env.get("DB_PORT", "5432")),
            database=env.get("DB_NAME", "todo"),
        )
    data_dir = Path(env.get("DATA_DIR", BASE_DIR))
    return f"sqlite:///{data_dir / 'todo.db'}"


def make_engine(url: URL | str, env: Mapping[str, str] | None = None) -> Engine:
    """DB 종류에 맞는 옵션으로 엔진을 만든다.

    PostgreSQL 은 '장애 시 빨리 실패' 가 기본이다. 기본값(풀 대기 30초, 연결 대기 무제한)이면
    DB 가 멈췄을 때 요청이 수십 초씩 매달려 워커 스레드가 전부 묶이고, 헬스체크(3초)도
    응답하지 못한다. 수치는 환경변수로 바꿀 수 있게 열어 둔다.
    """
    env = os.environ if env is None else env
    if str(url).startswith("sqlite"):
        # FastAPI 동기 엔드포인트는 스레드풀에서 돌기 때문에 다른 스레드의 연결 사용을 허용한다.
        return create_engine(url, connect_args={"check_same_thread": False})
    return create_engine(
        url,
        pool_pre_ping=True,                                  # 끊긴 연결(DB 재시작 등)을 꺼내기 전에 걸러낸다
        pool_size=int(env.get("DB_POOL_SIZE", "5")),
        max_overflow=int(env.get("DB_MAX_OVERFLOW", "5")),
        pool_timeout=float(env.get("DB_POOL_TIMEOUT", "3")),  # 풀이 비었을 때 기다리는 최대 시간(초)
        pool_recycle=1800,
        connect_args={
            "connect_timeout": int(env.get("DB_CONNECT_TIMEOUT", "3")),
            # 쿼리 하나가 DB 를 오래 붙잡지 못하게 (밀리초)
            "options": f"-c statement_timeout={int(env.get('DB_STATEMENT_TIMEOUT_MS', '5000'))}",
        },
    )


engine = make_engine(build_url(os.environ))
SessionLocal = sessionmaker(engine, expire_on_commit=False)


def get_session() -> Iterator[Session]:
    """요청 1건 = 세션 1개. 예외로 끝나면 커밋되지 않은 변경은 close() 에서 롤백된다."""
    with SessionLocal() as session:
        yield session


def init_db(bind: Engine | None = None, retries: int = 15, delay: float = 2.0) -> None:
    """테이블이 없으면 만든다. DB 가 아직 뜨는 중이면 잠시 기다렸다 다시 시도한다.

    docker compose 의 depends_on(service_healthy) 가 있어도, 팀서버처럼 docker run 으로
    따로 띄우거나 DB 만 재시작된 경우에는 앱이 먼저 뜰 수 있다.
    끝내 연결되지 않으면 예외를 그대로 올려 프로세스를 종료한다 → 컨테이너 restart 정책이 다시 띄운다.
    """
    bind = engine if bind is None else bind
    if bind.url.get_backend_name() == "sqlite" and bind.url.database not in (None, "", ":memory:"):
        Path(bind.url.database).parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(1, retries):
        try:
            Base.metadata.create_all(bind)
            return
        except OperationalError:
            logger.warning("database not ready (attempt %d/%d), retrying", attempt, retries)
            time.sleep(delay)
    Base.metadata.create_all(bind)               # 마지막 시도 — 실패하면 예외가 그대로 올라간다


def sync_id_sequence(session: Session) -> None:
    """id 를 직접 지정해 넣은 뒤 PostgreSQL 시퀀스를 최대 id 다음으로 맞춘다.

    맞추지 않으면 다음 POST 가 1 번부터 다시 시작해 기존 id 와 부딪힌다(UniqueViolation).
    SQLite(AUTOINCREMENT)는 직접 넣은 id 를 sqlite_sequence 에 자동 반영하므로 할 일이 없다.
    """
    if session.get_bind().dialect.name == "postgresql":
        session.execute(text(
            "SELECT setval(pg_get_serial_sequence('todos', 'id'), "
            "COALESCE((SELECT MAX(id) FROM todos), 0) + 1, false)"
        ))
