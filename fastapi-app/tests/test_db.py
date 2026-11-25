"""db.py — 접속 주소 결정, 엔진 옵션, 기동 시 재시도 (v10.0.0)."""

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

import db


#접속 주소 (build_url)

def test_database_url_has_top_priority():
    env = {"DATABASE_URL": "postgresql+psycopg://db.example/d", "DB_HOST": "ignored"}
    assert db.build_url(env) == "postgresql+psycopg://db.example/d"


def test_db_host_builds_postgres_url_with_escaped_password():
    """비밀번호에 @ : / 가 있어도 주소가 깨지지 않아야 한다 (문자열 이어붙이기로 만들면 깨진다)."""
    url = db.build_url({"DB_HOST": "postgres", "DB_USER": "todo", "DB_PASSWORD": "p@ss:w/rd",
                        "DB_NAME": "todo", "DB_PORT": "5433"})
    assert url.drivername == "postgresql+psycopg"
    assert (url.host, url.port, url.username, url.database) == ("postgres", 5433, "todo", "todo")
    assert url.password == "p@ss:w/rd"
    assert "p%40ss%3Aw%2Frd" in url.render_as_string(hide_password=False)


def test_db_host_defaults():
    url = db.build_url({"DB_HOST": "postgres"})
    assert (url.port, url.username, url.database) == (5432, "todo", "todo")


def test_falls_back_to_sqlite_in_data_dir(tmp_path):
    assert db.build_url({"DATA_DIR": str(tmp_path)}) == f"sqlite:///{tmp_path / 'todo.db'}"


def test_falls_back_to_sqlite_next_to_source():
    assert db.build_url({}) == f"sqlite:///{db.BASE_DIR / 'todo.db'}"


#엔진 옵션 (make_engine)

def test_postgres_engine_fails_fast():
    """장애 시 오래 매달리지 않도록 풀 대기·연결 대기 시간이 짧게 잡혀 있어야 한다."""
    engine = db.make_engine("postgresql+psycopg://127.0.0.1:1/x", env={})
    assert engine.pool.size() == 5
    assert engine.pool._max_overflow == 5
    assert engine.pool._timeout == 3
    assert engine.pool._pre_ping is True


def test_postgres_engine_options_from_env():
    """카오스 실험에서 풀 크기·타임아웃을 코드 수정 없이 바꿀 수 있어야 한다."""
    env = {"DB_POOL_SIZE": "2", "DB_MAX_OVERFLOW": "0", "DB_POOL_TIMEOUT": "0.5"}
    engine = db.make_engine("postgresql+psycopg://127.0.0.1:1/x", env=env)
    assert (engine.pool.size(), engine.pool._max_overflow, engine.pool._timeout) == (2, 0, 0.5)


def test_make_engine_reads_os_environ_by_default(monkeypatch):
    monkeypatch.setenv("DB_POOL_SIZE", "7")
    engine = db.make_engine("postgresql+psycopg://127.0.0.1:1/x")
    assert engine.pool.size() == 7


def test_sqlite_engine_allows_threadpool(tmp_path):
    engine = db.make_engine(f"sqlite:///{tmp_path}/t.db", env={})
    assert engine.dialect.name == "sqlite"
    with engine.connect():                       # 연결이 실제로 열린다
        pass
    engine.dispose()


#기동 (init_db)

def test_init_db_creates_tables_and_data_dir(tmp_path, new_engine):
    engine = new_engine(f"sqlite:///{tmp_path}/new/dir/todo.db")
    db.init_db(engine)
    assert {"todos", "app_meta"} <= set(inspect(engine).get_table_names())


def test_init_db_uses_module_engine_by_default(test_engine):
    db.Base.metadata.drop_all(test_engine)
    db.init_db()                                 # conftest 가 db.engine 을 테스트 DB 로 바꿔 둠
    assert "todos" in inspect(test_engine).get_table_names()


def test_init_db_in_memory_sqlite_needs_no_directory(new_engine):
    engine = new_engine()
    db.init_db(engine)
    assert "todos" in inspect(engine).get_table_names()


def test_init_db_retries_until_database_is_up(monkeypatch, tmp_path, caplog, new_engine):
    """DB 가 늦게 뜨면 기다렸다가 성공해야 한다 (앱이 먼저 뜨는 docker run 배포)."""
    engine = new_engine(f"sqlite:///{tmp_path}/late.db")
    real_create_all = db.Base.metadata.create_all
    calls = []

    def flaky(bind):
        calls.append(bind)
        if len(calls) < 3:
            raise OperationalError("SELECT 1", {}, Exception("connection refused"))
        real_create_all(bind)

    monkeypatch.setattr(db.Base.metadata, "create_all", flaky)
    db.init_db(engine, retries=5, delay=0)
    assert len(calls) == 3
    assert "database not ready (attempt 2/5)" in caplog.text


def test_init_db_gives_up_after_retries(monkeypatch, new_engine):
    """끝내 안 되면 예외를 올려 프로세스를 끝낸다 → 컨테이너 restart 정책이 다시 띄운다."""
    calls = []

    def always_down(bind):
        calls.append(bind)
        raise OperationalError("SELECT 1", {}, Exception("connection refused"))

    monkeypatch.setattr(db.Base.metadata, "create_all", always_down)
    engine = new_engine()                        # python:S5778 — 예외를 낼 수 있는 호출은 블록 안에 하나만
    with pytest.raises(OperationalError):
        db.init_db(engine, retries=2, delay=0)
    assert len(calls) == 2


#세션

def test_get_session_yields_and_closes():
    gen = db.get_session()
    session = next(gen)
    assert isinstance(session, Session)
    with pytest.raises(StopIteration):
        next(gen)


def test_sync_id_sequence_is_noop_on_sqlite_or_aligns_postgres(session):
    session.add_all([db.TodoRow(id=10, title="a"), db.TodoRow(id=3, title="b")])
    session.flush()
    db.sync_id_sequence(session)
    session.add(db.TodoRow(title="next"))
    session.commit()
    assert max(r.id for r in session.query(db.TodoRow)) == 11


#스키마 업그레이드 (v10.0.0: 반복 일정 컬럼)

def test_upgrade_adds_missing_column_to_existing_table(test_engine):
    """반복 일정 기능 '전' 에 만들어진 todos 테이블(운영 서버)에 컬럼이 추가되고 기존 행은 'none'."""
    db.Base.metadata.drop_all(test_engine)
    pk = "SERIAL PRIMARY KEY" if test_engine.dialect.name == "postgresql" else "INTEGER PRIMARY KEY AUTOINCREMENT"
    with test_engine.begin() as conn:
        conn.execute(text(
            f"CREATE TABLE todos (id {pk}, title VARCHAR(100) NOT NULL, "
            "description VARCHAR(500) NOT NULL, completed BOOLEAN NOT NULL, due_date DATE, "
            "priority VARCHAR(6) NOT NULL)"
        ))
        conn.execute(text("INSERT INTO todos (title, description, completed, priority) "
                          "VALUES ('예전 항목', '', false, 'medium')"))

    db.init_db(test_engine)

    with test_engine.connect() as conn:
        assert conn.execute(text("SELECT recurrence FROM todos")).scalar_one() == "none"
    invalid = text("UPDATE todos SET recurrence = 'yearly'")   # CHECK 제약도 함께 붙는다
    with test_engine.connect() as conn:
        with pytest.raises(IntegrityError):
            conn.execute(invalid)


def test_upgrade_is_idempotent(test_engine):
    """이미 최신이면 아무것도 하지 않는다 — 앱이 기동할 때마다 실행되기 때문."""
    assert db.upgrade_schema(test_engine) == []
