# Python 3.13 slim 베이스 이미지 (3.9는 2025-10 지원 종료, 최신 FastAPI는 Python 3.10 이상 필요)
# v9.0.0: 태그만 쓰면 같은 태그가 다른 이미지로 바뀔 수 있다(보안 패치 재빌드 등).
#   그러면 빌드할 때마다 다른 베이스가 들어가 재현이 안 되고, 그 위에 쌓인 레이어 캐시도 전부 무효가 된다.
#   (실제로 2026-11-24 빌드에서 temurin 베이스가 바뀌어 JMeter 를 처음부터 다시 받았다)
#   digest(@sha256) 로 고정해 언제 빌드해도 같은 베이스를 쓴다.
#   태그와 digest 를 같이 쓰면(이미지:태그@sha256) Docker 는 태그를 무시하므로, 태그가 실제
#   이미지와 달라도 알 수 없다(SonarQube docker:S8431). 그래서 digest 만 쓰고 태그는 아래 주석으로 남긴다.
#   올릴 때: docker pull <태그> 후 docker inspect --format '{{index .RepoDigests 0}}' <태그> 로
#   새 digest 를 확인해 이 줄을 바꾸고 커밋한다 (멀티아키 인덱스 digest 라 amd64/arm64 모두 동작).
# 기준 태그: python:3.13-slim (2026-11-24 확인)
FROM python@sha256:6d68eb6310a906f26c7638eb6ea7557d4261662df3181af2f80f1f9a7e34c549

# .pyc 생성 안 함 / 로그 즉시 출력 / pip 캐시·버전체크 끄기
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DATA_DIR=/app/data \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# root가 아닌 실행 전용 사용자 생성
# v5.1.0 (docker:S6504): chown 대상을 /app 전체가 아니라 데이터 디렉터리로 한정.
#         소스까지 appuser 소유로 두면 앱이 침해됐을 때 main.py 자체를 바꿔
#         지속적인 악성 코드를 심을 수 있다.
RUN useradd --create-home --uid 10001 appuser \
 && mkdir -p /app/data \
 && chown appuser:appuser /app/data

# 작업 디렉토리 설정
WORKDIR /app

# 의존성 먼저 설치 (코드만 바뀌면 이 단계는 캐시 재사용 → 빌드가 빨라짐)
COPY requirements.txt .
RUN pip install -r requirements.txt

# 소스 복사 — root 소유로 두어 appuser 는 읽기만 가능하게 한다.
# 앱이 쓰는 경로는 DATA_DIR(/app/data) 하나뿐이고, 그 디렉터리만 appuser 소유다.
COPY main.py .
COPY templates/ ./templates/

# 이후 명령은 appuser 권한으로 실행
USER appuser

EXPOSE 8000

# v3.0.0: /health 로 컨테이너 상태 점검 (503이면 unhealthy로 표시)
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2).status == 200 else 1)"

# FastAPI 서버 실행
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]