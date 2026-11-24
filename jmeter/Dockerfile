# JMeter 실행 이미지 (multi-arch: amd64 / arm64 모두 동작)
#
# 베이스: eclipse-temurin
#   Docker 공식 openjdk 이미지는 2022-07 deprecated 되어 보안 패치가 끊겼다.
#   temurin 은 멀티아키 이미지라 arm64v8/ 접두사로 아키텍처를 고정할 필요가 없다.
# v9.0.0: 태그만 쓰면 같은 태그가 다른 이미지로 바뀔 수 있다(보안 패치 재빌드 등).
#   그러면 빌드할 때마다 다른 베이스가 들어가 재현이 안 되고, 그 위에 쌓인 레이어 캐시도 전부 무효가 된다.
#   (실제로 2026-11-24 빌드에서 temurin 베이스가 바뀌어 JMeter 를 처음부터 다시 받았다)
#   digest(@sha256) 로 고정해 언제 빌드해도 같은 베이스를 쓴다.
#   태그와 digest 를 같이 쓰면(이미지:태그@sha256) Docker 는 태그를 무시하므로, 태그가 실제
#   이미지와 달라도 알 수 없다(SonarQube docker:S8431). 그래서 digest 만 쓰고 태그는 아래 주석으로 남긴다.
#   올릴 때: docker pull <태그> 후 docker inspect --format '{{index .RepoDigests 0}}' <태그> 로
#   새 digest 를 확인해 이 줄을 바꾸고 커밋한다 (멀티아키 인덱스 digest 라 amd64/arm64 모두 동작).
# 기준 태그: eclipse-temurin:17-jre-jammy (2026-11-24 확인)
FROM eclipse-temurin@sha256:c6a5dd70fb24f4e07ef88cbda8efb613a5a649a1af071a41de8eaabd1ee6b5da

# JMeter 5.6.3
#   5.4.1 은 log4j-core 2.13.3 을 번들해 Log4Shell(CVE-2021-44228, CVSS 9.8, RCE)에
#   그대로 노출된다. 부하 테스트 도구는 대상 서버의 응답 헤더·본문을 로깅하므로
#   실제로 악용 가능한 경로다.
ENV JMETER_VERSION=5.6.3
ENV JMETER_HOME=/opt/apache-jmeter-${JMETER_VERSION}
ENV PATH=${JMETER_HOME}/bin:$PATH

RUN apt-get update \
 && apt-get install -y --no-install-recommends curl ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# 다운로드 무결성 검증 — 검증 없는 다운로드는 미러 변조·중간자 공격에 무방비다.
# v9.0.0: SHA-512 를 이 파일에 고정한다 (베이스 이미지 digest 고정과 같은 원리).
#   예전에는 체크섬도 같은 서버에서 받아 비교했는데, 서버가 변조되면 파일과 체크섬이
#   함께 바뀌어 검증이 의미가 없다. 값은 dlcdn·archive 두 곳의 .sha512 가 같음을 확인한 것.
ARG JMETER_SHA512=5978a1a35edb5a7d428e270564ff49d2b1b257a65e17a759d259a9283fc17093e522fe46f474a043864aea6910683486340706d745fcdf3db1505fd71e689083
# v9.0.0: 다운로드 서버
#   archive.apache.org 는 학교망에서 약 40~50KB/s 로 느려 83MB 를 받는 데 30분 가까이 걸렸다
#   (FastApi-Todos Pipeline #3: 5분 동안 12~15MB 받고 시간 초과).
#   → Apache CDN(dlcdn) 을 먼저 쓰고, 버전이 내려가 dlcdn 에 없을 때만 archive 로 넘어간다.
#   curl 은 연결 20초·시도당 5분 제한, 1번 재시도 → 서버당 최대 약 10분, 넘기면 실패시킨다.
#   (시간 제한이 없으면 연결이 멈췄을 때 파이프라인 전체 timeout 30분까지 붙잡힌다)
RUN set -eux; \
    cd /tmp; \
    FILE="apache-jmeter-${JMETER_VERSION}.tgz"; \
    CURL="curl -fsSL --connect-timeout 20 --max-time 300 --retry 1 --retry-delay 5"; \
    $CURL -o jmeter.tgz "https://dlcdn.apache.org/jmeter/binaries/${FILE}" \
      || $CURL -o jmeter.tgz "https://archive.apache.org/dist/jmeter/binaries/${FILE}"; \
    echo "${JMETER_SHA512}  jmeter.tgz" | sha512sum -c -; \
    tar -xzf jmeter.tgz -C /opt/; \
    rm -f jmeter.tgz

# JVM 힙. 기본값(1g)은 스레드 수가 늘면 GC 로 응답 시간이 왜곡된다.
ENV JVM_ARGS="-Xms512m -Xmx1g"

# 비(非)root 실행 — 부하 테스트를 root 로 돌릴 이유가 없다.
# uid 1000 은 Ubuntu 기본 사용자와 맞춰 bind mount 쓰기 권한 문제를 피하기 위한 값.
RUN useradd -u 1000 -m -s /bin/bash jmeter \
 && mkdir -p /jmeter \
 && chown -R jmeter:jmeter /jmeter ${JMETER_HOME}

USER jmeter

# 작업 디렉터리를 버전과 무관한 /jmeter 로 고정.
# /opt/apache-jmeter-5.6.3 처럼 버전이 박힌 경로를 쓰면 JMeter 를 올릴 때마다
# Jenkinsfile 의 docker cp 경로까지 전부 고쳐야 한다.
WORKDIR /jmeter

CMD ["jmeter", "-n"]
