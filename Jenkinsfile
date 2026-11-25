// =====================================================================
//  Jenkins Deploy Jmeter  (v7.0.0 / #8 과제)
//  Jenkins 는 sogang011(163.239.77.84)에서만 돌고, 배포 대상은 팀서버 sogang004.
//  JMeter 컨테이너도 Jenkins 가 있는 sogang011 에서 띄워 팀서버를 때린다.
//
//  v9.0.0: 주차별 Jenkins 잡(Pipeline script)에 흩어져 있던 단계를 이 파일 하나로 합침
//    - SonarQube Analysis / Quality Gate ('Jenkins Deploy Sonarqube' 잡, v5.0.0)
//    - Integration Test                ('Jenkins Deploy Sonarqube' 잡, v4.0.0~)
//    - pip-audit 가 취약점을 찾으면 빌드 실패, requirements.txt '==' 고정 여부 검사
//
//  v10.0.0: 저장소를 todo.json → PostgreSQL 로 전환
//    - DB Compatibility Test: 같은 단위 테스트를 일회용 PostgreSQL 컨테이너에 대고 한 번 더 실행
//    - Deploy: 팀서버에 postgres-011 을 (없으면) 띄우고, 옛 todo.json 을 1회 이관한 뒤 앱 실행
//    - 필요한 Jenkins Credential: 'postgres-011-password' (Secret text, 영문·숫자)
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

        // --- DB (v10.0.0) -----------------------------------------------
        DB_CRED               = 'postgres-011-password'  // Secret text. 팀서버 DB 비밀번호
        DB_CONTAINER          = 'postgres-011'
        DB_IMAGE              = 'postgres:18-alpine'     // docker-compose.yml 과 같은 태그로 맞출 것
        APP_NET               = 'todo-011-net'           // 팀서버에서 앱 ↔ DB 만 쓰는 네트워크

        // --- 부하 테스트 ------------------------------------------------
        JMETER_IMAGE_NAME     = 'jmeter-011'          // 멀티아키 베이스라 'arm' 이름 제거
        LOADTEST_NET          = 'loadtest-net'        // docker-compose.yml 에서 이름 고정한 네트워크
        INFLUX_URL            = 'http://influxdb-011:8086/write?db=jmeter'

        // --- 정적 분석 (v9.0.0) ------------------------------------------
        // 아래 두 값은 비밀값이 아니라 Jenkins 관리 화면에 등록한 '이름'이다.
        //   SONAR_SERVER : Manage Jenkins > System > SonarQube servers 의 Name
        //   SONAR_SCANNER: Manage Jenkins > Tools > SonarQube Scanner installations 의 Name
        SONAR_SERVER          = 'sonarqube'
        SONAR_SCANNER         = 'sonar'
        // SONAR_TOKEN 같은 비밀값은 전역 environment 에 두지 않는다.
        //   전역에 두면 모든 스테이지 셸에 주입되어 set -x 나 env 덤프로 로그에 남는다.
        //   토큰은 SonarQube 서버 설정에 연결된 Jenkins Credential 로만 관리하고,
        //   withSonarQubeEnv 가 분석 블록 안에서만 주입한다(로그에는 마스킹).
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
                    # 1) requirements.txt 의 모든 의존성이 '==' 로 고정돼 있는지 검사.
                    #    '>=' 이면 빌드마다 설치 버전이 달라져 아래 점검 결과를 믿을 수 없다.
                    UNPINNED=$(grep -vE '^[[:space:]]*(#|$)' fastapi-app/requirements.txt \
                               | grep -vE '^[A-Za-z0-9._-]+(\\[[A-Za-z0-9,._-]+\\])?==[^[:space:]]+' || true)
                    if [ -n "$UNPINNED" ]; then
                        echo "버전이 '==' 로 고정되지 않은 의존성:"
                        echo "$UNPINNED"
                        exit 1
                    fi

                    # 2) 알려진 취약점(CVE)이 있으면 여기서 빌드를 멈춘다.
                    #    v7.0.0 까지는 '|| true' 라 취약점이 나와도 통과했다.
                    pip install pip-audit
                    pip-audit -r fastapi-app/requirements.txt
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
                        --cov-report=xml:fastapi-app/coverage.xml \
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

        // v10.0.0: 위 단계는 메모리 SQLite 로 빠르게 돈다. 운영은 PostgreSQL 이므로,
        //   SQLite 에서는 드러나지 않는 차이(id 시퀀스, NULL 정렬, LIKE 대소문자, 트랜잭션 실패 후
        //   상태)를 잡으려고 같은 테스트를 진짜 PostgreSQL 에 대고 한 번 더 돌린다.
        //   커버리지는 --cov-append 로 합친다 — PostgreSQL 에서만 타는 분기(시퀀스 맞추기)가 있어서
        //   SQLite 결과만 SonarQube 에 보내면 그 분기가 '테스트 안 됨' 으로 나온다.
        stage('DB Compatibility Test (PostgreSQL)') {
            steps {
                sh '''
                    set -eu
                    PG="pg-test-$BUILD_NUMBER"
                    # 일회용: --rm + tmpfs(디스크에 안 남김), 호스트 포트는 127.0.0.1 의 빈 포트로 자동 배정
                    docker run -d --rm --name "$PG" \
                        -e POSTGRES_PASSWORD=test -e POSTGRES_DB=todo_test \
                        --tmpfs /var/lib/postgresql \
                        -p 127.0.0.1::5432 "$DB_IMAGE" >/dev/null
                    trap 'docker stop "$PG" >/dev/null 2>&1 || true' EXIT

                    # 초기화 중 임시 서버는 TCP 를 열지 않는다 → -h 127.0.0.1 로 확인해야 진짜 준비 완료
                    for i in $(seq 1 30); do
                        docker exec "$PG" pg_isready -h 127.0.0.1 -U postgres -d todo_test >/dev/null 2>&1 && break
                        [ "$i" = 30 ] && { echo "test postgres not ready"; exit 1; }
                        sleep 1
                    done
                    PORT=$(docker port "$PG" 5432/tcp | head -n1 | awk -F: '{print $NF}')

                    . venv/bin/activate
                    TEST_DATABASE_URL="postgresql+psycopg://postgres:test@127.0.0.1:$PORT/todo_test" \
                    pytest fastapi-app/tests -p no:cacheprovider \
                        --cov=fastapi-app --cov-append \
                        --cov-report=xml:fastapi-app/coverage.xml \
                        --cov-report=html:htmlcov
                '''
            }
        }

        // v9.0.0: 테스트 '다음'에 분석한다. 위 단계가 만든 fastapi-app/coverage.xml 을
        // SonarQube 가 읽어야 커버리지 조건(Quality Gate)이 0% 로 판정되지 않는다.
        stage('SonarQube Analysis') {
            steps {
                script {
                    def scannerHome = tool env.SONAR_SCANNER
                    // 서버 URL·토큰은 withSonarQubeEnv 가 이 블록 안에서만 주입한다.
                    // (sonar-project.properties 의 sonar.host.url 도 여기서 덮어씀)
                    withSonarQubeEnv(env.SONAR_SERVER) {
                        dir('fastapi-app') {
                            sh "${scannerHome}/bin/sonar-scanner"
                        }
                    }
                }
            }
        }

        // Quality Gate 를 통과하지 못하면 이미지 빌드·배포로 넘어가지 않는다.
        // 결과는 SonarQube → Jenkins 웹훅(http://<jenkins>/sonarqube-webhook/)으로 받는다.
        stage('Quality Gate') {
            steps {
                timeout(time: 5, unit: 'MINUTES') {
                    waitForQualityGate abortPipeline: true
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
                // v10.0.0: DB 비밀번호는 이 블록 안에서만 환경변수로 주입된다 (로그에는 **** 로 가려짐)
                withCredentials([string(credentialsId: "${DB_CRED}", variable: 'DB_PASSWORD')]) {
                sshagent(credentials: ["${REMOTE_CRED}"]) {
                    sh '''
                        set -eu
                        # 비밀번호는 셸 변수로 넘기므로 따옴표·공백이 있으면 깨진다 → 영문·숫자만 허용
                        case "$DB_PASSWORD" in
                            ''|*[!A-Za-z0-9]*) echo "postgres-011-password 는 영문·숫자만 (openssl rand -hex 24)"; exit 1 ;;
                        esac

                        # StrictHostKeyChecking=no 는 어떤 호스트 키든 받아들여 중간자(MITM)를 허용한다.
                        # 배포 스테이지는 SSH 키를 들고 붙으므로, 가짜 서버에 붙으면 배포 자격증명이 통째로 넘어간다.
                        mkdir -p ~/.ssh && chmod 700 ~/.ssh
                        ssh-keyscan -H "$REMOTE_HOST" >> ~/.ssh/known_hosts 2>/dev/null
                        sort -u ~/.ssh/known_hosts -o ~/.ssh/known_hosts

                        # v10.0.0: 비밀번호를 ssh 명령줄(원격 서버의 ps 에 보임)이 아니라 표준입력 첫 줄로 보낸다.
                        { echo "DB_PASSWORD=$DB_PASSWORD"; cat <<'EOSSH'
set -eu
# 앞 스테이지에서 직접 빌드·푸시한 바로 그 이미지를 배포한다.
# (검증한 것과 배포한 것이 일치해야 의미가 있다)
docker pull "$IMAGE"

# --- v10.0.0: DB ---------------------------------------------------------
# 앱과 DB 만 쓰는 네트워크. 팀원 컨테이너와 이름(DNS)이 섞이지 않게 따로 만든다.
docker network inspect "$NET" >/dev/null 2>&1 || docker network create "$NET" >/dev/null

# DB 는 배포마다 새로 만들지 않는다 — 데이터가 볼륨에 있어도 컨테이너를 지울 이유가 없다.
if ! docker ps -a --format '{{.Names}}' | grep -qx "$DB"; then
    docker run -d --name "$DB" --restart unless-stopped --network "$NET" \
        -e POSTGRES_DB=todo -e POSTGRES_USER=todo -e POSTGRES_PASSWORD="$DB_PASSWORD" \
        -v "${DB}-data:/var/lib/postgresql" \
        --health-cmd "pg_isready -h 127.0.0.1 -U todo -d todo" --health-interval 5s --health-retries 12 \
        --security-opt no-new-privileges:true --cap-drop ALL \
        --cap-add CHOWN --cap-add DAC_OVERRIDE --cap-add FOWNER --cap-add SETGID --cap-add SETUID \
        "$DB_IMAGE" >/dev/null
fi
docker start "$DB" >/dev/null                      # 멈춰 있었다면 다시 켠다 (이미 떠 있으면 아무 일 없음)

for i in $(seq 1 30); do
    [ "$(docker inspect -f '{{.State.Health.Status}}' "$DB")" = healthy ] && break
    [ "$i" = 30 ] && { echo "database did not become healthy"; exit 1; }
    sleep 2
done

APP_ENV="-e DATA_DIR=/app/data -e DB_HOST=$DB -e DB_USER=todo -e DB_NAME=todo -e DB_PASSWORD=$DB_PASSWORD"

# --- 앱 -------------------------------------------------------------------
# 이전 컨테이너를 먼저 내린다. 옛 버전(v9)이 떠 있는 동안 이관하면, 이관 직후 들어온
# 쓰기가 todo.json 에만 남고 DB 에는 없는 채로 사라진다.
docker stop "$NAME" 2>/dev/null || true
docker rm   "$NAME" 2>/dev/null || true

# 옛 todo.json(v9.0.0 까지의 볼륨)을 트래픽을 받기 '전에' 1회 이관한다.
# 이미 이관했으면 'migration already done' 만 찍고 끝난다 (app_meta 기록).
docker run --rm --network "$NET" $APP_ENV -v "${NAME}_data:/app/data" \
    --security-opt no-new-privileges:true --cap-drop ALL \
    "$IMAGE" python migrate_json.py /app/data/todo.json

# 컨테이너 내부 포트는 Dockerfile 의 uvicorn 포트(8000)와 같아야 한다.
docker run -d --name "$NAME" --restart unless-stopped --network "$NET" \
    $APP_ENV -v "${NAME}_data:/app/data" \
    --security-opt no-new-privileges:true --cap-drop ALL \
    -p "$PORT:8000" "$IMAGE"

# 헬스체크가 통과할 때까지 최대 30초 대기 — 안 뜬 서버를 때리면 부하 테스트가 전부 실패로 나온다.
# v10.0.0: /health 는 DB 에 COUNT 질의까지 하므로, 200 이면 앱 ↔ DB 연결까지 확인된 것이다.
for i in $(seq 1 15); do
    if curl -fsS "http://127.0.0.1:$PORT/health" >/dev/null 2>&1; then
        echo "health OK"; exit 0
    fi
    sleep 2
done
echo "deployed container did not become healthy"; docker logs --tail 30 "$NAME"; exit 1
EOSSH
                        } | ssh -o StrictHostKeyChecking=yes "$REMOTE_USER@$REMOTE_HOST" \
                            "IMAGE='$IMAGE_NAME:$BUILD_NUMBER' NAME='$REMOTE_CONTAINER' PORT='$REMOTE_PORT' DB='$DB_CONTAINER' DB_IMAGE='$DB_IMAGE' NET='$APP_NET' bash -s"
                    '''
                }
                }
            }
        }

        // v9.0.0: 배포된 팀서버에 실제 HTTP 요청을 보내 API 동작을 확인한다.
        // 실패하면 부하 테스트까지 가지 않는다(고장 난 서버에 부하를 걸어 봐야 의미가 없다).
        stage('Integration Test') {
            steps {
                sh '''
                    set -eu
                    . venv/bin/activate
                    mkdir -p integration_report

                    BASE_URL="http://$REMOTE_HOST:$REMOTE_PORT" \
                    pytest integration_tests \
                        --html=integration_report/report.html \
                        --self-contained-html
                '''
            }
            post {
                always {
                    publishHTML(target: [
                        reportName           : 'Integration Test Report',
                        reportDir            : 'integration_report',
                        reportFiles          : 'report.html',
                        keepAll              : true,
                        alwaysLinkToLastBuild: true,
                        allowMissing         : true
                    ])
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
