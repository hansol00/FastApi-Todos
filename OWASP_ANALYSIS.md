# OWASP Top 10 (2021) 보안 코드 리뷰 — FastAPI To-Do List

- 분석 대상 커밋: `8174f8b` (main)
- 분석 대상: `fastapi-app/main.py`, `fastapi-app/templates/index.html`, `fastapi-app/Dockerfile`, `fastapi-app/requirements.txt`, `docker-compose.yml`
- 참고로 함께 확인한 파일: `fastapi-app/.dockerignore`
- 전제: 단일 사용자 교육용 앱이며, 인증 기능은 처음부터 설계에 없음
- 분류 표기
  - **실제 결함**: 앱의 의도와 상관없이 고쳐야 하는 문제
  - **설계상 의도된 범위**: 앱 범위(인증 없음, 교육용)를 감안하면 지금 상태가 의도된 것이지만, 공개 환경에 배포하면 위험해지는 문제
- 검증 방법: 코드를 읽은 뒤, 재현이 가능한 항목은 `fastapi.testclient.TestClient`로 임시 데이터 디렉터리(`DATA_DIR`)를 써서 실제로 확인했다. 재현 결과는 각 항목의 "검증" 줄에 적었다.

---

## 1. 요약

### 1.1 카테고리별 요약

| 카테고리 | 발견 건수 | 최고 심각도 |
|---|:---:|:---:|
| A01 Broken Access Control | 1 | Medium |
| A02 Cryptographic Failures | 1 | Low |
| A03 Injection | 0 | 해당 없음 |
| A04 Insecure Design | 2 | **High** |
| A05 Security Misconfiguration | 7 | Medium |
| A06 Vulnerable and Outdated Components | 1 | Low |
| A07 Identification and Authentication Failures | 0 | 해당 없음 |
| A08 Software and Data Integrity Failures | 1 | Low |
| A09 Security Logging and Monitoring Failures | 1 | Low |
| A10 Server-Side Request Forgery | 0 | 해당 없음 |
| **합계** | **15** | **High** |

### 1.2 발견사항 목록 (심각도 순)

| ID | 심각도 | 카테고리 | 위치 | 요약 | 분류 |
|---|:---:|---|---|---|---|
| H-1 | High | A04 | `main.py:44-51, 66-88` | 동시 요청 시 데이터 유실·JSON 파일 손상 (잠금 없음, 원자적 쓰기 아님) | 실제 결함 |
| M-1 | Medium | A01 | `main.py:61-88`, `docker-compose.yml:7-8` | 모든 CRUD API에 인증·인가 없음, 모든 네트워크 인터페이스에 공개 | 설계상 의도 + 배포 설정 결함 |
| M-2 | Medium | A04 | `main.py:35` | `description` 길이·항목 수·요청 빈도 제한 없음 → 디스크 고갈 | 실제 결함 |
| M-3 | Medium | A05 | `docker-compose.yml:25-26` | SonarQube(9000) 포트를 모든 인터페이스에 공개, 초기 계정 상태로 노출 | 실제 결함 |
| M-4 | Medium | A05 | `docker-compose.yml:2-19`, `Dockerfile:12` | 데이터 볼륨·`DATA_DIR` 미설정 → 컨테이너를 다시 만들면 데이터 소실 | 실제 결함 |
| L-1 | Low | A05 | `main.py:111-115` | `/health`가 예외 원문(절대 경로, 저장된 데이터 값, 라이브러리 버전)을 응답에 노출 | 실제 결함 |
| L-2 | Low | A05 | `main.py:30` | `/docs`, `/redoc`, `/openapi.json`이 기본으로 공개됨 | 설계상 의도 (교육용) |
| L-3 | Low | A05 | `main.py:119-121` | 보안 헤더(CSP, X-Frame-Options 등) 없음 → 클릭재킹 가능 | 실제 결함 |
| L-4 | Low | A05 | `docker-compose.yml:2-19` | 앱 컨테이너에 `read_only`, 메모리·PID 제한 없음 | 실제 결함 (하드닝) |
| L-5 | Low | A05 | `Dockerfile:23`, `.dockerignore` | `todo.json`이 빌드 컨텍스트에서 제외되지 않아 로컬 데이터가 이미지에 포함됨 | 실제 결함 |
| L-6 | Low | A06 | `requirements.txt:1-7`, `Dockerfile:19-20` | 테스트 전용 패키지가 운영 이미지에 설치됨, 버전 잠금 없음 | 실제 결함 |
| L-7 | Low | A08 | `Dockerfile:2, 20` | 베이스 이미지 digest 미고정, pip 해시 검증 없음 | 실제 결함 (하드닝) |
| L-8 | Low | A02 | `Dockerfile:35`, `docker-compose.yml:8` | 평문 HTTP로 서비스 (학번·이름 입력을 유도하는 UI) | 설계상 의도 |
| L-9 | Low | A09 | `main.py:61-88, 111-115` | 데이터 변경·삭제 감사 로그 없음, 저장소 오류가 서버 로그에 남지 않음 | 실제 결함 |

### 1.3 심각도 판단 기준

- **High**: 공격자나 일반 사용 중에 데이터가 실제로 유실·손상되며, 재현이 확인된 경우
- **Medium**: 네트워크에 접근할 수 있는 누구나 악용할 수 있거나 데이터 소실로 이어지지만, 단일 사용자·교육용이라는 맥락 때문에 피해 범위가 제한되는 경우
- **Low**: 정보 노출, 하드닝 부족 등 단독으로는 피해가 작지만 다른 문제와 겹치면 위험해지는 경우

---

## 2. 상세 분석

### H-1. 동시 요청 시 데이터 유실 및 JSON 파일 손상

| 항목 | 내용 |
|---|---|
| 카테고리 | A04 Insecure Design (CWE-362 Race Condition, CWE-367 TOCTOU) |
| 심각도 | **High**: 재현해 보니 실제로 데이터가 유실됐다. 쓰는 도중 프로세스가 중단되면 파일이 영구적으로 손상되어 모든 API가 500을 돌려준다. |
| 분류 | 실제 결함 |
| 위치 | `fastapi-app/main.py:44-51` (load/save), `:66-88` (생성·수정·삭제) |

**문제 코드**

```python
# main.py:44-51
def load_todos() -> list[TodoItem]:
    raw = TODO_FILE.read_text(encoding="utf-8") if TODO_FILE.exists() else "[]"
    return [TodoItem(**t) for t in json.loads(raw)]

def save_todos(todos: list[TodoItem]) -> None:
    data = json.dumps([t.model_dump(mode="json") for t in todos], indent=2, ensure_ascii=False)
    TODO_FILE.write_text(data, encoding="utf-8")
```

```python
# main.py:66-72
@app.post("/todos", status_code=201)
def create_todo(payload: TodoIn) -> TodoItem:
    todos = load_todos()
    new_id = max((t.id for t in todos), default=0) + 1
    todo = TodoItem(id=new_id, **payload.model_dump())
    save_todos(todos + [todo])
    return todo
```

**왜 문제인가**

1. FastAPI는 `def`(동기)로 선언한 엔드포인트를 스레드 풀에서 **동시에** 실행한다. 그런데 "읽기 → 수정 → 전체 덮어쓰기" 과정에 잠금이 없어서, 두 요청이 같은 목록을 읽고 각자 덮어쓰면 한쪽 변경이 사라진다.
2. `Path.write_text()`는 파일을 먼저 비우고(truncate) 내용을 쓴다. 그래서 다른 요청이 쓰는 도중의 빈 파일이나 절반만 쓰인 파일을 읽으면 `JSONDecodeError`로 500이 난다.
3. 쓰는 도중 컨테이너가 중단되면(OOM, 재시작 등) `todo.json`이 깨진 JSON으로 남는다. 그러면 `load_todos()`가 계속 예외를 내서 `/todos`의 모든 요청이 500이 되고, 직접 파일을 고치기 전까지 서비스가 복구되지 않는다.
4. M-1(인증 없음)과 겹치면, 외부에서 POST를 동시에 많이 보내는 것만으로 기존 데이터를 지우거나 파일을 손상시킬 수 있다.

**검증**: 스레드 50개로 `POST /todos`를 동시에 보냈더니 **저장된 항목은 14개**뿐이었다. 나머지 요청은 서버에서 `json.decoder.JSONDecodeError: Expecting value: line 1 column 1 (char 0)` 등의 예외로 실패했다.

**수정 방안**: 프로세스 안에서는 잠금으로 직렬화하고, 파일은 임시 파일에 쓴 뒤 `os.replace`로 원자적으로 교체한다.

```python
import os
import tempfile
import threading

_lock = threading.Lock()

def save_todos(todos: list[TodoItem]) -> None:
    data = json.dumps([t.model_dump(mode="json") for t in todos], indent=2, ensure_ascii=False)
    # 같은 디렉터리에 임시 파일을 만든 뒤 교체 → 읽는 쪽은 항상 완전한 파일만 본다
    fd, tmp = tempfile.mkstemp(dir=DATA_DIR, prefix=".todo-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, TODO_FILE)          # 원자적 교체
    except BaseException:
        os.unlink(tmp)
        raise

@app.post("/todos", status_code=201)
def create_todo(payload: TodoIn) -> TodoItem:
    with _lock:                              # 읽기-수정-쓰기 전체를 한 덩어리로
        todos = load_todos()
        new_id = max((t.id for t in todos), default=0) + 1
        todo = TodoItem(id=new_id, **payload.model_dump())
        save_todos(todos + [todo])
    return todo
# update_todo / delete_todo 도 같은 방식으로 `with _lock:` 으로 감싼다.
```

> uvicorn `--workers`를 2 이상으로 늘리면 `threading.Lock`으로는 부족하다. 그때는 파일 잠금(`fcntl.flock`, `filelock` 패키지)을 쓰거나 SQLite로 옮기는 편이 낫다.

---

### M-1. 모든 CRUD API에 인증·인가 없음 + 모든 인터페이스에 공개

| 항목 | 내용 |
|---|---|
| 카테고리 | A01 Broken Access Control (CWE-862 Missing Authorization) |
| 심각도 | **Medium**: 네트워크에 접근할 수 있는 누구나 전체 데이터를 조회·수정·삭제할 수 있다. 다만 단일 사용자 교육용 데이터라 피해 범위가 제한된다. |
| 분류 | **인증 부재 자체는 설계상 의도된 범위**. 다만 그 상태로 **모든 인터페이스(0.0.0.0)에 포트를 공개한 것은 배포 설정 결함**이다. |
| 위치 | `fastapi-app/main.py:61-88`, `docker-compose.yml:7-8` |

**문제 코드**

```python
# main.py:84-88
@app.delete("/todos/{todo_id}", status_code=204)
def delete_todo(todo_id: int) -> None:
    todos = load_todos()
    del todos[find_index(todos, todo_id)]
    save_todos(todos)
```

```yaml
# docker-compose.yml:7-8
    ports:
      - "5012:8000"     # 호스트 IP를 지정하지 않음 → 0.0.0.0:5012 로 바인딩
```

**왜 문제인가**

- 호스트 주소를 생략한 `ports` 설정은 Docker가 호스트의 모든 인터페이스에 바인딩한다. 커밋 이력(`a80ec2a 팀 서버 배포용 포트/컨테이너명 분리`)을 보면 이 앱은 여러 사람이 쓰는 서버에 배포된다. 같은 네트워크의 누구나 `curl -X DELETE http://<서버>:5012/todos/1` 같은 요청을 반복해서 데이터를 전부 지울 수 있다.
- id가 1부터 순차적으로 증가하므로(`main.py:69`) 모든 항목을 차례대로 열거하고 삭제하는 것도 쉽다.
- Docker가 만든 포트 공개 규칙은 호스트 방화벽(ufw 등)보다 먼저 적용되는 경우가 많다. 그래서 "방화벽으로 막았다"고 생각해도 실제로는 열려 있을 수 있다.

**수정 방안**

1) 로컬에서만 쓴다면 루프백에만 바인딩한다.

```yaml
    ports:
      - "127.0.0.1:5012:8000"
```

2) 팀 서버에서 다른 사람이 접근해야 한다면, 최소한 공유 토큰으로 쓰기 API를 보호한다. (인증은 설계 범위 밖이므로 "있으면 좋은" 수준의 대책이다.)

```python
import secrets
from fastapi import Depends, Header

API_TOKEN = os.getenv("API_TOKEN")  # compose 의 environment 로 주입

def require_token(x_api_token: str | None = Header(default=None)) -> None:
    if API_TOKEN and not secrets.compare_digest(x_api_token or "", API_TOKEN):
        raise HTTPException(401, "Unauthorized")

@app.delete("/todos/{todo_id}", status_code=204, dependencies=[Depends(require_token)])
def delete_todo(todo_id: int) -> None:
    ...
```

또는 앱 앞에 리버스 프록시(Nginx/Caddy)를 두고 IP 허용 목록이나 Basic Auth를 적용한다.

---

### M-2. 입력 크기·항목 수·요청 빈도 제한 없음 → 디스크·메모리 고갈

| 항목 | 내용 |
|---|---|
| 카테고리 | A04 Insecure Design (CWE-770 Allocation of Resources Without Limits) |
| 심각도 | **Medium**: 인증이 없으므로(M-1) 누구나 악용할 수 있고, 디스크가 가득 차면 호스트의 다른 서비스(SonarQube 포함)까지 영향을 받는다. |
| 분류 | 실제 결함 (`title`에는 `max_length=100`이 있는데 `description`에는 없어서 검증이 일관되지 않다) |
| 위치 | `fastapi-app/main.py:35` (관련: `:44-51`, `:66-72`) |

**문제 코드**

```python
# main.py:33-37
class TodoIn(BaseModel):
    title: str = Field(min_length=1, max_length=100)
    description: str = ""                 # ← 길이 제한 없음
    completed: bool = False
    due_date: date | None = None
```

**왜 문제인가**

- uvicorn에는 기본 요청 본문 크기 제한이 없다. 그래서 수십 MB짜리 `description`을 담은 POST를 반복하면 `todo.json`이 계속 커진다.
- 요청마다 `load_todos()`가 **파일 전체**를 읽어 파싱하고 `save_todos()`가 **파일 전체**를 다시 쓴다. 파일이 커질수록 모든 요청이 느려지고 메모리 사용량도 늘어나서, 결국 서비스 거부(DoS) 상태가 된다.
- 항목 개수에도 제한이 없다.

**검증**: `description`에 5,000,000자를 넣은 POST가 `201 Created`로 받아들여졌고, `todo.json` 크기는 5,000,120 바이트가 됐다.

**수정 방안**

```python
MAX_TODOS = 1000

class TodoIn(BaseModel):
    title: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=500)
    completed: bool = False
    due_date: date | None = None

@app.post("/todos", status_code=201)
def create_todo(payload: TodoIn) -> TodoItem:
    with _lock:
        todos = load_todos()
        if len(todos) >= MAX_TODOS:
            raise HTTPException(409, "Too many items")
        ...
```

본문 크기 자체도 앞단에서 제한한다(예: Nginx `client_max_body_size 16k;`). uvicorn에서는 `--limit-concurrency 50` 옵션으로 동시 처리 수를 제한할 수 있다.

---

### M-3. SonarQube 포트를 모든 인터페이스에 공개

| 항목 | 내용 |
|---|---|
| 카테고리 | A05 Security Misconfiguration (관련: A07 — 초기 관리자 계정) |
| 심각도 | **Medium**: SonarQube에는 분석한 소스 코드와 취약점 목록이 저장된다. 이것이 외부에 노출되면 공격자에게 이 앱의 약점 목록을 그대로 넘겨주는 셈이다. |
| 분류 | 실제 결함 |
| 위치 | `docker-compose.yml:25-28` |

**문제 코드**

```yaml
# docker-compose.yml:25-28
    ports:
      - "9000:9000"
    environment:
      - SONAR_ES_BOOTSTRAP_CHECKS_DISABLE=true
```

**왜 문제인가**

- M-1과 같은 이유로 `0.0.0.0:9000`에 바인딩되어 같은 네트워크의 누구나 접속할 수 있다.
- SonarQube Community 이미지를 처음 실행하면 관리자 계정은 `admin/admin`이다. 첫 로그인 때 비밀번호 변경을 요구하긴 하지만, **컨테이너를 띄운 뒤 아무도 로그인하지 않은 사이**에 제3자가 먼저 로그인하면 관리자 권한을 가져갈 수 있다. 볼륨(`sonarqube_data`)이 영속화되므로 한번 빼앗긴 계정은 재시작해도 그대로 남는다.
- SonarQube는 개발 중 정적 분석에만 쓰는 도구라서 외부에 공개할 이유가 없다.

**수정 방안**

```yaml
    ports:
      - "127.0.0.1:9000:9000"     # 로컬 브라우저에서만 접속
```

컨테이너를 처음 띄우면 바로 관리자 비밀번호를 바꾼다. 또 `Administration > Security`에서 `Force user authentication`이 켜져 있는지(기본값 ON) 확인한다. 분석할 때만 쓰는 도구이므로 `profiles: ["analysis"]`로 분리해서, 평소 `docker compose up` 때는 뜨지 않게 하는 방법도 있다.

---

### M-4. 데이터 볼륨·`DATA_DIR` 미설정으로 데이터 소실

| 항목 | 내용 |
|---|---|
| 카테고리 | A05 Security Misconfiguration (가용성·무결성) |
| 심각도 | **Medium**: 이미지를 다시 빌드하거나 `docker compose down/up`으로 컨테이너를 다시 만들면 저장된 모든 할 일이 사라진다. 공격 없이 일반 운영만으로도 일어난다. |
| 분류 | 실제 결함 (v4.0.0 커밋 `179f8de`가 의도한 "볼륨 영속성"이 compose에는 반영되지 않았다) |
| 위치 | `docker-compose.yml:2-19`, `fastapi-app/Dockerfile:12`, `fastapi-app/main.py:17` |

**문제 코드**

```python
# main.py:17
DATA_DIR = Path(os.getenv("DATA_DIR", BASE_DIR))   # 미지정 시 /app
```

```dockerfile
# Dockerfile:11-13
RUN useradd --create-home --uid 10001 appuser \
 && mkdir -p /app/data \          # 디렉터리는 만들지만
 && chown -R appuser:appuser /app # DATA_DIR=/app/data 는 어디에서도 설정되지 않음
```

```yaml
# docker-compose.yml:2-19 — fastapi-app 서비스에 environment / volumes 항목이 없음
```

**왜 문제인가**

- 저장소 전체를 `git grep DATA_DIR`로 찾아봐도 환경변수를 설정하는 곳이 없다. 그래서 컨테이너 안에서는 `DATA_DIR=/app`이 되고, 데이터는 컨테이너의 쓰기 가능 레이어(`/app/todo.json`)에 저장된다.
- `docker compose up --build`나 `down`을 하면 이 레이어가 삭제되어 데이터가 사라진다. `/app/data`는 만들어 두기만 하고 쓰지 않는 디렉터리가 됐다.
- (compose를 쓰지 않고 `docker run -e DATA_DIR=... -v ...`로 따로 배포한다면 이 항목은 해당되지 않는다. 다만 저장소 안에는 그런 설정이 없다.)

**수정 방안**

```yaml
services:
  fastapi-app:
    # ...
    environment:
      - DATA_DIR=/app/data
    volumes:
      - todo_data:/app/data

volumes:
  todo_data:
  sonarqube_data:
  # ...
```

Dockerfile에도 기본값을 넣어 두면 compose 없이 실행해도 같은 경로를 쓴다.

```dockerfile
ENV DATA_DIR=/app/data
```

---

### L-1. `/health` 응답에 예외 원문 노출

| 항목 | 내용 |
|---|---|
| 카테고리 | A05 Security Misconfiguration (CWE-209 Error Message Containing Sensitive Information) |
| 심각도 | **Low**: 저장소에 문제가 생긴 경우에만 노출되고, 노출되는 정보도 정찰 수준이다. |
| 분류 | 실제 결함 |
| 위치 | `fastapi-app/main.py:111-115` |

**문제 코드**

```python
# main.py:111-115
    except Exception as exc:
        body["status"] = "degraded"
        body["storage"] = "error"
        body["detail"] = f"{type(exc).__name__}: {exc}"
        return JSONResponse(body, status_code=503)
```

**왜 문제인가**: 인증 없이 누구나 호출할 수 있는 엔드포인트가 예외 메시지를 그대로 돌려준다. 함께 노출되는 `version`, `uptime_seconds`(`:101-102`)와 합치면 공격자가 서버의 내부 구조를 파악하는 데 쓸 수 있다.

**검증**: 실제로 다음 내용이 응답에 담겼다.
- 파일 권한 오류일 때: `PermissionError: [Errno 13] Permission denied: 'C:\\Users\\...\\todo.json'` → **서버 절대 경로 노출**
- 데이터 형식 오류일 때: `... [type=bool_parsing, input_value='maybe', input_type=str] For further information visit https://errors.pydantic.dev/2.13/v/bool_parsing` → **저장된 데이터 값과 Pydantic 버전 노출**

**수정 방안**: 상세 내용은 서버 로그에만 남기고, 응답에는 고정된 문구만 넣는다.

```python
import logging
logger = logging.getLogger("todo")

    except Exception:
        logger.exception("health check failed")   # 상세 내용은 서버 로그로
        body["status"] = "degraded"
        body["storage"] = "error"
        return JSONResponse(body, status_code=503)
```

---

### L-2. API 문서(`/docs`, `/redoc`, `/openapi.json`) 기본 공개

| 항목 | 내용 |
|---|---|
| 카테고리 | A05 Security Misconfiguration |
| 심각도 | **Low**: API 구조는 프론트엔드 JS만 봐도 알 수 있어서 추가로 노출되는 정보가 적다. |
| 분류 | 설계상 의도된 범위 (교육용으로 Swagger UI를 쓰는 것으로 보임). 운영 배포라면 결함이다. |
| 위치 | `fastapi-app/main.py:30` |

**문제 코드**

```python
app = FastAPI(title="To-Do List API", version=APP_VERSION)
```

**왜 문제인가**: Swagger UI에서 `DELETE /todos/{id}` 등을 버튼 하나로 실행할 수 있다. 그래서 M-1의 무단 조작이 도구 없이도 브라우저만으로 가능해진다. **검증**: `/docs`, `/openapi.json` 모두 200을 반환했다.

**수정 방안**: 환경변수로 켜고 끌 수 있게 한다.

```python
SHOW_DOCS = os.getenv("SHOW_DOCS", "0") == "1"
app = FastAPI(
    title="To-Do List API",
    version=APP_VERSION,
    docs_url="/docs" if SHOW_DOCS else None,
    redoc_url="/redoc" if SHOW_DOCS else None,
    openapi_url="/openapi.json" if SHOW_DOCS else None,
)
```

---

### L-3. 보안 응답 헤더 없음 (클릭재킹 등)

| 항목 | 내용 |
|---|---|
| 카테고리 | A05 Security Misconfiguration (CWE-1021 Improper Restriction of Rendered UI Layers) |
| 심각도 | **Low**: 삭제할 때 `confirm()` 대화상자(`index.html:124`)가 한 번 더 막아 주고, 현재 XSS 지점이 없어서 CSP가 없는 것의 실질적인 영향은 작다. |
| 분류 | 실제 결함 |
| 위치 | `fastapi-app/main.py:119-121` |

**문제 코드**

```python
@app.get("/", include_in_schema=False)
def read_root() -> FileResponse:
    return FileResponse(INDEX_FILE, media_type="text/html")
```

**왜 문제인가**: **검증** 결과 `/` 응답 헤더는 `content-type, accept-ranges, content-length, last-modified, etag`뿐이었다. `X-Frame-Options`/`frame-ancestors`가 없으므로, 악성 페이지가 투명한 `<iframe>`으로 이 앱을 덮어씌우고 사용자가 "Delete"나 체크박스를 누르도록 유도할 수 있다. CSP가 없어서 앞으로 XSS가 생겼을 때 피해를 줄여 줄 방어선도 없다.

**수정 방안**

```python
@app.middleware("http")
async def security_headers(request, call_next):
    resp = await call_next(request)
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Referrer-Policy"] = "no-referrer"
    # index.html 이 인라인 <style>/<script> 를 쓰므로 현 구조에서는 'unsafe-inline' 이 필요.
    # 스크립트를 외부 파일로 분리하면 'unsafe-inline' 을 뺄 수 있다.
    resp.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline'; frame-ancestors 'none'"
    )
    return resp
```

---

### L-4. 앱 컨테이너 하드닝 부족 (`read_only`, 리소스 제한 없음)

| 항목 | 내용 |
|---|---|
| 카테고리 | A05 Security Misconfiguration |
| 심각도 | **Low**: `cap_drop: ALL`, `no-new-privileges`, non-root 사용자(`Dockerfile:26`) 등 기본적인 하드닝은 이미 되어 있다. 빠진 부분만 지적한다. |
| 분류 | 실제 결함 (하드닝) |
| 위치 | `docker-compose.yml:2-19` |

**문제 코드**

```yaml
# docker-compose.yml:16-19 — 같은 파일의 sonarqube 에는 read_only: true 가 있으나 앱에는 없음
    security_opt:
      - no-new-privileges:true
    cap_drop:
      - ALL
```

**왜 문제인가**

- 루트 파일시스템이 쓰기 가능하고, 앱 사용자(`appuser`)가 `/app` 전체를 소유한다(`Dockerfile:13, 23`). 그래서 앱이 침해되면 `main.py`나 `templates/index.html` 자체를 바꿔서 지속적인 악성 코드(예: 저장형 스크립트 삽입)를 심을 수 있다.
- 메모리·PID 제한이 없어서, M-2처럼 큰 파일을 파싱하다 메모리가 고갈되면 호스트 전체가 영향을 받는다.

**수정 방안** (M-4의 볼륨을 적용한 뒤)

```yaml
  fastapi-app:
    read_only: true
    tmpfs:
      - /tmp
    volumes:
      - todo_data:/app/data       # 쓰기는 이 경로만
    mem_limit: 256m
    pids_limit: 100
```

Dockerfile에서 소스 파일은 root 소유로 두고, 데이터 디렉터리만 `appuser` 소유로 바꾸면 더 좋다.

```dockerfile
COPY . .                                   # --chown 제거 → 소스는 root 소유, appuser 는 읽기만
RUN mkdir -p /app/data && chown appuser:appuser /app/data
```

---

### L-5. 로컬 `todo.json`이 Docker 이미지에 포함됨

| 항목 | 내용 |
|---|---|
| 카테고리 | A05 Security Misconfiguration (CWE-538 Insertion of Sensitive Information into Externally-Accessible File) |
| 심각도 | **Low**: 현재 로컬 `todo.json` 내용은 `[]`이다. 하지만 개발 중에 학번·이름(UI 예시 `index.html:35-36`) 같은 실제 데이터가 들어 있는 상태로 빌드하면 그대로 이미지에 포함된다. |
| 분류 | 실제 결함 |
| 위치 | `fastapi-app/Dockerfile:23`, `fastapi-app/.dockerignore` |

**문제 코드**

```dockerfile
# Dockerfile:23
COPY --chown=appuser:appuser . .
```

`.gitignore`에는 `fastapi-app/todo.json`이 있지만 `.dockerignore`에는 없다.

**왜 문제인가**: 개발자 PC의 데이터가 이미지 레이어에 영구히 남는다. 이미지를 레지스트리에 푸시하거나 팀원과 공유하면 그 데이터도 함께 배포된다. M-4 때문에 `DATA_DIR`이 `/app`이므로 컨테이너가 이 파일을 **실제 초기 데이터로 사용**하게 된다.

**수정 방안**: `.dockerignore`에 다음을 추가한다.

```
todo.json
app.log
sonar-project.properties
```

---

### L-6. 테스트 전용 패키지가 운영 이미지에 설치됨 / 버전 잠금 없음

| 항목 | 내용 |
|---|---|
| 카테고리 | A06 Vulnerable and Outdated Components |
| 심각도 | **Low**: 현재 지정된 최소 버전들은 보안 패치를 고려해서 고른 것이다(주석 참고). 실행 경로에서 쓰이지 않는 패키지라 직접적인 위험은 작다. |
| 분류 | 실제 결함 |
| 위치 | `fastapi-app/requirements.txt:1-7`, `fastapi-app/Dockerfile:19-20` |

**문제 코드**

```text
# requirements.txt:4-7
pytest>=9.0.3
pytest-cov>=7.1.0
pytest-html>=4.2.0
httpx2>=2.13.1
```

```dockerfile
# Dockerfile:19-20
COPY requirements.txt .
RUN pip install -r requirements.txt
```

**왜 문제인가**

- `.dockerignore`가 `tests/`를 제외하는데도 테스트 도구 4개와 그 의존성이 운영 이미지에 설치된다. 쓰지 않는 패키지만큼 공격 표면과 앞으로 CVE가 나올 대상이 늘어난다.
- 모든 의존성이 `>=`(하한만 지정)이다. 그래서 빌드할 때마다 다른 버전이 설치될 수 있고, 새로 나온 버전에 문제가 있으면 검증 없이 바로 운영에 들어간다. 어떤 버전이 배포됐는지 나중에 확인하기도 어렵다.

**수정 방안**: 운영용과 개발용을 나누고, 운영용은 버전을 고정(lock)한다.

```text
# requirements.txt (운영)
fastapi==0.142.1
starlette==1.7.0
uvicorn[standard]==0.54.0
```

```text
# requirements-dev.txt (개발·CI)
-r requirements.txt
pytest>=9.0.3
pytest-cov>=7.1.0
pytest-html>=4.2.0
httpx2>=2.13.1
```

`pip-compile`(pip-tools)이나 `uv pip compile`로 하위 의존성까지 고정한 lock 파일을 만들어 쓰는 것을 권장한다. CI에서는 `pip-audit -r requirements.txt`로 알려진 취약점을 점검한다.

---

### L-7. 베이스 이미지·패키지 무결성 검증 없음

| 항목 | 내용 |
|---|---|
| 카테고리 | A08 Software and Data Integrity Failures (CWE-494 Download of Code Without Integrity Check) |
| 심각도 | **Low**: 공식 이미지와 PyPI를 쓰고 있어서 실제로 공격받을 가능성은 낮다. 공급망 하드닝 차원의 지적이다. |
| 분류 | 실제 결함 (하드닝) |
| 위치 | `fastapi-app/Dockerfile:2`, `:20` |

**문제 코드**

```dockerfile
FROM python:3.13-slim            # 태그는 계속 다른 이미지로 갱신된다
RUN pip install -r requirements.txt   # 해시 검증 없음
```

**왜 문제인가**: `python:3.13-slim` 태그는 계속 갱신되는 태그(mutable tag)라서, 같은 Dockerfile이라도 빌드 시점마다 다른 이미지가 쓰인다. pip도 패키지 해시를 확인하지 않으므로 미러나 프록시가 바꿔치기한 패키지를 걸러내지 못한다.

**수정 방안**

```dockerfile
FROM python:3.13-slim@sha256:<검증한 digest>
RUN pip install --require-hashes -r requirements.lock
```

`requirements.lock`은 `pip-compile --generate-hashes`로 만든다. digest는 Dependabot이나 Renovate로 주기적으로 갱신한다.

---

### L-8. 평문 HTTP로 서비스

| 항목 | 내용 |
|---|---|
| 카테고리 | A02 Cryptographic Failures (CWE-319 Cleartext Transmission) |
| 심각도 | **Low**: 데이터는 할 일 목록이다. 다만 UI가 학번·이름 입력을 예시로 보여 주므로 개인정보가 오갈 수 있다. |
| 분류 | 설계상 의도된 범위 (교육용 앱이고, TLS는 보통 앱 앞의 프록시에서 처리한다). 공개망에 배포한다면 결함이다. |
| 위치 | `fastapi-app/Dockerfile:35`, `docker-compose.yml:8`, `fastapi-app/templates/index.html:35-36` |

**문제 코드**

```dockerfile
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
```

```html
<input type="text" id="title" placeholder="제목 (예: 20231234)" required>
<input type="text" id="description" placeholder="설명 (예: 홍길동)">
```

**왜 문제인가**: TLS 없이 `0.0.0.0:5012`로 공개되어 있다. 그래서 같은 네트워크(학교 Wi-Fi, 공유 서버망)에 있는 사람이 패킷을 가로채면 입력한 학번·이름을 그대로 볼 수 있고, 응답을 바꿔치기할 수도 있다.

**수정 방안**: 앱은 그대로 두고 앞에 TLS를 처리하는 리버스 프록시를 둔다.

```yaml
  caddy:
    image: caddy:2
    ports: ["443:443"]
    command: caddy reverse-proxy --from todo.example.com --to fastapi-app:8000
  fastapi-app:
    # ports 제거 → 외부에서는 caddy 를 통해서만 접근
    expose: ["8000"]
```

---

### L-9. 보안 관련 이벤트 로깅 부재

| 항목 | 내용 |
|---|---|
| 카테고리 | A09 Security Logging and Monitoring Failures (CWE-778 Insufficient Logging) |
| 심각도 | **Low**: uvicorn 기본 접근 로그(IP, 메서드, 경로, 상태 코드)는 stdout으로 남는다. 다만 무엇이 바뀌었는지는 남지 않는다. |
| 분류 | 실제 결함 |
| 위치 | `fastapi-app/main.py:61-88`, `:111-115` |

**문제 코드**: `main.py` 전체에 `logging`을 쓰는 곳이 없다. `/health`는 예외를 잡아서 응답 본문에만 넣고(L-1) 서버 로그에는 남기지 않는다.

```python
# main.py:84-88 — 삭제되는 데이터에 대한 기록 없음
def delete_todo(todo_id: int) -> None:
    todos = load_todos()
    del todos[find_index(todos, todo_id)]
    save_todos(todos)
```

**왜 문제인가**

- M-1처럼 누군가 무단으로 데이터를 지웠을 때, 무엇이 지워졌는지 알 수 없어 복구할 근거가 없다. 접근 로그에는 `DELETE /todos/3 204` 정도만 남는다.
- H-1의 파일 손상이나 저장소 권한 문제가 생겨도 서버 로그에 기록되지 않는다. `/health`를 직접 호출하거나 컨테이너 상태가 unhealthy로 바뀌기 전까지 운영자가 알아차리기 어렵다.

**수정 방안**

```python
import logging
logger = logging.getLogger("todo")

@app.delete("/todos/{todo_id}", status_code=204)
def delete_todo(todo_id: int) -> None:
    with _lock:
        todos = load_todos()
        removed = todos.pop(find_index(todos, todo_id))
        save_todos(todos)
    logger.info("todo deleted id=%s title=%r", removed.id, removed.title)
```

컨테이너 로그가 쌓여서 디스크를 채우지 않도록 compose에 로그 로테이션도 설정한다.

```yaml
    logging:
      driver: json-file
      options: { max-size: "10m", max-file: "3" }
```

---

## 3. 해당 없음 카테고리

- **A03 Injection**: 해당 없음. 프론트엔드는 모든 출력을 `textContent`로 넣고 `innerHTML`을 쓰지 않는다(`index.html:79, 84, 99, 103`). 서버에는 DB나 셸 호출이 없고, `todo_id`는 `int`로 검증된다.
- **A07 Identification and Authentication Failures**: 해당 없음. 앱에 인증 기능 자체가 없다(접근 통제 부재는 M-1, SonarQube 초기 계정은 M-3에서 다룸).
- **A10 Server-Side Request Forgery**: 해당 없음. 서버가 외부로 보내는 요청은 HEALTHCHECK의 고정 URL(`http://127.0.0.1:8000/health`)뿐이고, 사용자 입력으로 URL을 만드는 곳이 없다.

## 4. 검토했지만 발견사항에서 제외한 항목

오탐을 막기 위해, 의심했지만 검증해 보니 문제가 아니었던 항목을 적어 둔다.

| 항목 | 검증 결과 |
|---|---|
| CSRF (다른 사이트에서 폼을 제출해 할 일 생성·삭제) | `Content-Type: text/plain`으로 보낸 POST는 **422**로 거부됐다. FastAPI는 JSON 이외의 Content-Type 본문을 JSON으로 파싱하지 않는다. `application/json` 요청이나 `PUT/DELETE`는 CORS preflight가 필요한데, CORS 미들웨어가 없어서 preflight(`OPTIONS`)가 **405**로 실패한다. 따라서 브라우저를 통한 교차 출처 변경 요청은 성립하지 않는다. |
| 저장형 XSS (제목·설명에 `<script>` 저장) | 서버는 문자열을 그대로 저장하지만, 화면에 그릴 때 `textContent`만 쓰므로 스크립트가 실행되지 않는다. |
| `httpx2` 패키지의 타이포스쿼팅 의심 | 설치된 패키지 메타데이터(`pip show httpx2`)를 확인해 보니 홈페이지가 `github.com/pydantic/httpx2`이고 저자가 httpx 원저자(Tom Christie)로, 정식 후속 패키지로 보인다. 단, 테스트 전용인데 운영 이미지에 설치되는 문제는 L-6에 포함했다. |
| 컨테이너 root 실행 | `USER appuser`(UID 10001)로 실행되고, `cap_drop: ALL`, `no-new-privileges`가 적용되어 있다. |
