// =====================================================================
//  Jenkins Deploy Jmeter  (v7.0.0 / #8 과제)
//  Jenkins 는 sogang011(163.239.77.84)에서만 돌고, 배포 대상은 팀서버 sogang004.
//  JMeter 컨테이너도 Jenkins 가 있는 sogang011 에서 띄워 팀서버를 때린다.
// =====================================================================
pipeline {
    agent any

    environment {
        DOCKERHUB_CREDENTIALS = 'dockerhub-credentials'
        IMAGE_NAME            = 'baehansol/fastapi-app'

        // --- 배포 대상: 팀서버 ------------------------------------------
        REMOTE_CRED           = 'admin-team'          // 팀서버 SSH 자격증명
        REMOTE_USER           = 'sogang004'
        REMOTE_HOST           = '163.239.77.77'
        // ※ 팀원과 겹치지 않는 내 슬롯. 팀 규칙이 다르면 이 두 줄만 고치면 된다.
        REMOTE_PORT           = '5012'                // 팀서버에서 열 호스트 포트
        REMOTE_CONTAINER      = 'FastApi-app-011'     // 팀서버에서 쓸 컨테이너 이름

        // --- 부하 테스트 ------------------------------------------------
        JMETER_IMAGE_NAME     = 'jmeter-011'          // 멀티아키 베이스라 'arm' 이름 제거
        LOADTEST_NET          = 'loadtest-net'        // docker-compose.yml 에서 이름 고정한 네트워크
        INFLUX_URL            = 'http://influxdb-011:8086/write?db=jmeter'
        // SONAR_TOKEN 같은 비밀값은 전역 environment 에 두지 않는다.
        //   전역에 두면 모든 스테이지 셸에 주입되어 set -x 나 env 덤프로 로그에 남는다.
        //   필요한 스테이지에서만 withCredentials 로 감싸 쓸 것.
    }

    options {
        timestamps()
        buildDiscarder(logRotator(numToKeepStr: '20'))
        timeout(time: 30, unit: 'MINUTES')
    }

    stages {

        stage('Checkout') {
            steps {
                git url: 'https://github.com/hansol00/FastApi-Todos.git', branch: 'main'
            }
        }

        stage('Setup Environment & Install Dependencies') {
            steps {
                sh '''
                    set -eu
                    python3 -m venv venv
                    . venv/bin/activate
                    pip install --upgrade pip
                    pip install -r fastapi-app/requirements.txt

                    # 의존성 취약점 점검(공급망 보안).
                    # requirements.txt 에 '==' 로 버전이 고정돼 있는지도 같이 확인할 것.
                    pip install pip-audit
                    pip-audit -r fastapi-app/requirements.txt || true
                '''
            }
        }

        stage('Test & Coverage') {
            steps {
                sh '''
                    set -eu
                    . venv/bin/activate
                    mkdir -p pytest_report

                    pytest fastapi-app/tests \
                        --html=pytest_report/report.html \
                        --self-contained-html \
                        --cov=fastapi-app \
                        --cov-report=xml:coverage.xml \
                        --cov-report=html:htmlcov
                '''
            }
            post {
                always {
                    publishHTML(target: [
                        reportName           : 'Pytest HTML Report',
                        reportDir            : 'pytest_report',
                        reportFiles          : 'report.html',
                        keepAll              : true,
                        alwaysLinkToLastBuild: true,
                        allowMissing         : true
                    ])
                    publishHTML(target: [
                        reportName           : 'Coverage Report',
                        reportDir            : 'htmlcov',
                        reportFiles          : 'index.html',
                        keepAll              : true,
                        alwaysLinkToLastBuild: true,
                        allowMissing         : true
                    ])
                }
            }
        }

        stage('Build') {
            steps {
                dir('fastapi-app') {
                    script {
                        // :latest 만 빌드하면 어떤 커밋이 배포됐는지 추적도, 롤백도 불가능하다.
                        docker.build("${IMAGE_NAME}:${BUILD_NUMBER}", ".")
                    }
                }
            }
        }

        stage('Push') {
            steps {
                script {
                    docker.withRegistry('https://index.docker.io/v1/', DOCKERHUB_CREDENTIALS) {
                        docker.image("${IMAGE_NAME}:${BUILD_NUMBER}").push()
                        docker.image("${IMAGE_NAME}:${BUILD_NUMBER}").push('latest')
                    }
                }
            }
        }

        // 부하 테스트 대상이 먼저 떠 있어야 하므로 배포가 앞선다.
        stage('Deploy to Team Server') {
            steps {
                sshagent(credentials: ["${REMOTE_CRED}"]) {
                    sh '''
                        set -eu
                        # StrictHostKeyChecking=no 는 어떤 호스트 키든 받아들여 중간자(MITM)를 허용한다.
                        # 배포 스테이지는 SSH 키를 들고 붙으므로, 가짜 서버에 붙으면 배포 자격증명이 통째로 넘어간다.
                        mkdir -p ~/.ssh && chmod 700 ~/.ssh
                        ssh-keyscan -H "$REMOTE_HOST" >> ~/.ssh/known_hosts 2>/dev/null
                        sort -u ~/.ssh/known_hosts -o ~/.ssh/known_hosts

                        ssh -o StrictHostKeyChecking=yes "$REMOTE_USER@$REMOTE_HOST" \
                            "IMAGE='$IMAGE_NAME:$BUILD_NUMBER' NAME='$REMOTE_CONTAINER' PORT='$REMOTE_PORT' bash -s" <<'EOSSH'
set -eu
# 앞 스테이지에서 직접 빌드·푸시한 바로 그 이미지를 배포한다.
# (검증한 것과 배포한 것이 일치해야 의미가 있다)
docker pull "$IMAGE"

docker stop "$NAME" 2>/dev/null || true
docker rm   "$NAME" 2>/dev/null || true

# 컨테이너 내부 포트는 Dockerfile 의 uvicorn 포트(8000)와 같아야 한다.
docker run -d --name "$NAME" --restart unless-stopped \
    -e DATA_DIR=/app/data -v "${NAME}_data:/app/data" \
    --security-opt no-new-privileges:true --cap-drop ALL \
    -p "$PORT:8000" "$IMAGE"

# 헬스체크가 통과할 때까지 최대 30초 대기 — 안 뜬 서버를 때리면 부하 테스트가 전부 실패로 나온다.
for i in $(seq 1 15); do
    if curl -fsS "http://127.0.0.1:$PORT/health" >/dev/null 2>&1; then
        echo "health OK"; exit 0
    fi
    sleep 2
done
echo "deployed container did not become healthy"; exit 1
EOSSH
                    '''
                }
            }
        }

        stage('Build JMeter Image') {
            steps {
                dir('jmeter') {
                    script {
                        docker.build("${JMETER_IMAGE_NAME}:latest", ".")
                    }
                }
            }
        }

        stage('Run JMeter Load Test') {
            steps {
                sh '''
                    set -eu
                    BASE_DIR="$WORKSPACE/jmeter"

                    # 이전 결과 정리 (JMeter 는 결과 파일이 이미 있으면 그냥 실패한다)
                    rm -rf "$BASE_DIR/report" "$BASE_DIR/results.jtl" "$BASE_DIR/jmeter.log"
                    mkdir -p "$BASE_DIR/report"

                    # 서버 IP 하드코딩 금지 — environment 의 REMOTE_HOST 를 그대로 쓴다.
                    TARGET_HOST="$REMOTE_HOST"
                    TARGET_PORT="$REMOTE_PORT"

                    # InfluxDB 가 떠 있을 때만 Backend Listener 를 켠다.
                    # 안 떠 있는데 켜 두면 전송 실패 로그가 쌓이고 결과 해석이 지저분해진다.
                    if docker network inspect "$LOADTEST_NET" >/dev/null 2>&1 \
                       && docker ps --format '{{.Names}}' | grep -qx 'influxdb-011'; then
                        NET_OPT="--network $LOADTEST_NET"
                        INFLUX_OPT="-JINFLUX_URL=$INFLUX_URL"
                        echo "InfluxDB 연동: ON"
                    else
                        NET_OPT=""
                        INFLUX_OPT="-JINFLUX_URL="
                        echo "InfluxDB 연동: OFF (influxdb-011 컨테이너가 없음)"
                    fi

                    # --network host 는 호스트 네트워크 스택을 그대로 써서 외부에 안 열린
                    # 내부 포트까지 접근 가능해지므로 쓰지 않는다. --user root 도 불필요.
                    # 파일 경로가 꼬이지 않도록 컨테이너 안에서는 test.jmx 로 이름을 통일한다.
                    CONTAINER_ID=$(docker create $NET_OPT "$JMETER_IMAGE_NAME:latest" \
                        jmeter -n -t /jmeter/test.jmx \
                               -l /jmeter/results.jtl \
                               -j /jmeter/jmeter.log \
                               -JTARGET_HOST="$TARGET_HOST" \
                               -JTARGET_PORT="$TARGET_PORT" \
                               "$INFLUX_OPT" \
                               -JTEST_TITLE="jenkins-$BUILD_NUMBER" \
                               -Jjmeter.save.saveservice.output_format=csv \
                               -e -o /jmeter/report)

                    # 버전이 박힌 /opt/apache-jmeter-5.6.3 대신 /jmeter 로 통일 →
                    # JMeter 버전을 올려도 이 스크립트를 고칠 필요가 없다.
                    docker cp "$BASE_DIR"/fastapi_test_plan.jmx "$CONTAINER_ID:/jmeter/test.jmx"

                    # 부하 테스트가 실패해도 리포트는 뽑아야 하므로 || true
                    docker start -a "$CONTAINER_ID" || true

                    docker cp "$CONTAINER_ID:/jmeter/report/."    "$BASE_DIR/report/"      || true
                    docker cp "$CONTAINER_ID:/jmeter/results.jtl" "$BASE_DIR/results.jtl"  || true
                    docker cp "$CONTAINER_ID:/jmeter/jmeter.log"  "$BASE_DIR/jmeter.log"   || true

                    docker rm "$CONTAINER_ID" >/dev/null

                    # 에러율을 로그에 바로 찍어 둔다 (리포트를 열지 않아도 성패가 보이게)
                    if [ -f "$BASE_DIR/results.jtl" ]; then
                        awk -F, 'NR>1 {t++; if ($8 != "true") f++} END {
                            printf "JMeter 샘플 %d건, 실패 %d건, 에러율 %.2f%%\\n", t, f+0, (t?f*100/t:0)
                        }' "$BASE_DIR/results.jtl"
                    fi
                '''
            }
            post {
                always {
                    publishHTML(target: [
                        reportName           : 'JMeter HTML Report',
                        reportDir            : 'jmeter/report',
                        reportFiles          : 'index.html',
                        keepAll              : true,
                        alwaysLinkToLastBuild: true,
                        allowMissing         : true
                    ])
                    archiveArtifacts artifacts: 'jmeter/results.jtl, jmeter/jmeter.log',
                                     allowEmptyArchive: true
                }
            }
        }
    }

    post {
        always {
            echo 'Pipeline completed.'
        }
    }
}
