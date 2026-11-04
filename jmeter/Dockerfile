# JMeter 실행 이미지 (multi-arch: amd64 / arm64 모두 동작)
#
# 베이스: eclipse-temurin
#   Docker 공식 openjdk 이미지는 2022-07 deprecated 되어 보안 패치가 끊겼다.
#   temurin 은 멀티아키 이미지라 arm64v8/ 접두사로 아키텍처를 고정할 필요가 없다.
FROM eclipse-temurin:17-jre-jammy

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
RUN set -eux; \
    cd /tmp; \
    curl -fsSL -o jmeter.tgz    "https://archive.apache.org/dist/jmeter/binaries/apache-jmeter-${JMETER_VERSION}.tgz"; \
    curl -fsSL -o jmeter.sha512 "https://archive.apache.org/dist/jmeter/binaries/apache-jmeter-${JMETER_VERSION}.tgz.sha512"; \
    EXPECTED="$(tr -d ' \n' < jmeter.sha512 | sed 's/.*://' | grep -oE '[0-9a-fA-F]{128}' | head -1 | tr 'A-Z' 'a-z')"; \
    ACTUAL="$(sha512sum jmeter.tgz | cut -d' ' -f1)"; \
    [ "$EXPECTED" = "$ACTUAL" ] || { echo "checksum mismatch: $ACTUAL != $EXPECTED"; exit 1; }; \
    tar -xzf jmeter.tgz -C /opt/; \
    rm -f jmeter.tgz jmeter.sha512

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
