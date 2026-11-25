"""테스트 공용 DB 설정 (v10.0.0).

기본은 메모리 SQLite — DB 서버 없이 몇 초 안에 끝난다.
TEST_DATABASE_URL 을 주면 같은 테스트를 실제 DB 에 대고 돌린다 (Jenkins 'DB Compatibility Test' 단계):

    TEST_DATABASE_URL=postgresql+psycopg://postgres:test@127.0.0.1:5432/todo_test pytest tests

SQLite 만으로 통과시키면 PostgreSQL 에서만 드러나는 차이(시퀀스, NULL 정렬, LIKE 대소문자,
트랜잭션 실패 후 상태)를 놓친다. 운영 DB 와 같은 엔진으로 한 번 더 돌려야 테스트 결과를 믿을 수 있다.
"""

import os

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import db
import main

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")


def _make_test_engine():
    if TEST_DATABASE_URL:
        return db.make_engine(TEST_DATABASE_URL, env={})
    # 메모리 DB 는 연결마다 따로 생기므로, 연결 하나(StaticPool)를 모든 스레드가 같이 쓰게 한다.
    return create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )


@pytest.fixture(scope="session")
def test_engine():
    engine = _make_test_engine()
    yield engine
    engine.dispose()


@pytest.fixture(autouse=True)
def isolated_db(test_engine, monkeypatch):
    """테스트마다 빈 테이블에서 시작한다. 실제 운영 DB 는 건드리지 않는다.

    - API 요청: get_session 의존성을 테스트용 세션으로 바꿔 끼운다.
    - 이관 스크립트처럼 db.SessionLocal/db.engine 을 직접 쓰는 코드도 같은 DB 를 보게 한다.
    """
    db.Base.metadata.drop_all(test_engine)
    db.Base.metadata.create_all(test_engine)
    factory = sessionmaker(test_engine, expire_on_commit=False)

    def override():
        with factory() as session:
            yield session

    main.app.dependency_overrides[db.get_session] = override
    monkeypatch.setattr(db, "engine", test_engine)
    monkeypatch.setattr(db, "SessionLocal", factory)
    yield factory
    main.app.dependency_overrides.clear()


@pytest.fixture
def session(isolated_db) -> Session:
    with isolated_db() as s:
        yield s


@pytest.fixture
def new_engine():
    """테스트 안에서 따로 만드는 엔진. 끝나면 dispose 해 SQLite 연결을 닫는다(ResourceWarning 방지)."""
    created = []

    def factory(url: str = "sqlite://", **kwargs):
        engine = create_engine(url, **kwargs)
        created.append(engine)
        return engine

    yield factory
    for engine in created:
        engine.dispose()
