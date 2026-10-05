# 배포된 서버에 실제 HTTP 요청을 보내 API를 확인하는 통합 테스트
# 실행: BASE_URL=http://163.239.77.77:5012 pytest integration_tests
import os
import time
import uuid

import httpx2
import pytest

BASE_URL = os.getenv("BASE_URL", "http://163.239.77.77:5012")

def wait_until_up(http, seconds=30):
    # 배포 직후에는 컨테이너가 아직 뜨는 중일 수 있으므로 응답이 올 때까지 기다림
    for _ in range(seconds):
        try:
            if http.get("/todos").status_code == 200:
                return
        except httpx2.TransportError:  # 연결 거부, 타임아웃 등
            pass
        time.sleep(1)
    pytest.fail(f"{BASE_URL} 에 접속할 수 없음 (컨테이너 실행 여부, 포트, 방화벽 확인)")


@pytest.fixture(scope="session")
def client():
    # 단위 테스트의 TestClient(app) 대신, 실제 배포 주소로 요청을 보내는 클라이언트
    with httpx2.Client(base_url=BASE_URL, timeout=5) as http:
        wait_until_up(http)
        yield http


@pytest.fixture
def todo(client):
    # 테스트용 항목을 하나 만들고, 테스트가 끝나면(실패해도) 지움 → 배포 서버의 실제 데이터는 그대로
    title = f"통합테스트-{uuid.uuid4().hex[:8]}"
    response = client.post("/todos", json={"title": title, "description": "integration test"})
    assert response.status_code == 201
    item = response.json()
    yield item
    client.delete(f"/todos/{item['id']}")  # 테스트 안에서 이미 지웠다면 404가 오지만 상관없음


def test_index_page(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]

def test_health(client):
    # v3.0.0 에서 추가한 헬스체크가 배포 환경에서도 정상인지 확인
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["storage"] == "ok"

def test_create_and_get(client, todo):
    response = client.get("/todos")
    assert response.status_code == 200
    assert todo["id"] in [t["id"] for t in response.json()]


def test_update(client, todo):
    payload = {"title": todo["title"], "description": "updated", "completed": True}
    response = client.put(f"/todos/{todo['id']}", json=payload)
    assert response.status_code == 200
    assert response.json()["completed"] is True
    # 수정 결과가 실제로 저장됐는지 다시 조회해서 확인
    saved = [t for t in client.get("/todos").json() if t["id"] == todo["id"]][0]
    assert saved["description"] == "updated"


def test_delete(client, todo):
    assert client.delete(f"/todos/{todo['id']}").status_code == 204
    assert client.delete(f"/todos/{todo['id']}").status_code == 404  # 이미 지운 항목은 404


def test_create_invalid(client):
    response = client.post("/todos", json={"description": "제목 없음"})  # 필수 필드 title 누락
    assert response.status_code == 422

def test_update_not_found(client):
    response = client.put("/todos/999999", json={"title": "없는 항목"})
    assert response.status_code == 404