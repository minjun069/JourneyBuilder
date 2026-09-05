# P04 — G1 clean start·readiness 검증 준비

상태: **검증 절차 준비 — 이 문서는 G1 통과 결과를 기록하지 않음**

## 1. 목적과 경계

이 문서는 P01~P03이 만든 AI·Backend·PostgreSQL·Redis 실행환경을 통합한 뒤,
G1 담당자가 빈 환경에서 같은 순서로 실행하고 같은 기준으로 판정할 수 있도록
명령, 기대 결과, 실패 반환 대상, 증거 양식을 정의한다.

P04의 완료 조건은 **G1을 통과하는 것**이 아니라 다음 준비가 끝나는 것이다.

- 설치·빌드 → 기동 → dependency readiness → 종료·정리 순서가 명령으로 재현된다.
- process up과 readiness가 구분된다.
- 필수 환경변수와 secret의 기본 거부를 확인할 수 있다.
- 정상·실패 조건을 같은 판정표로 기록할 수 있다.
- 실패를 P01·P02·P03 또는 후속 application 작업에 반환할 수 있다.

P04에서는 production code, 공통 `Makefile`, CI 설정을 수정하지 않는다. 아래 명령의
실제 전체 실행과 최종 판정은 G1에서 수행한다. P04에서는 문법·구성처럼 컨테이너를
기동하지 않는 범위만 확인할 수 있다.

## 2. G1 실행 전 입력

G1 담당자는 실행 전에 다음 값을 기록한다.

```text
G1 기준 commit SHA:
P04 절차 commit SHA:
실행자:
실행 시각과 timezone:
OS·architecture:
Docker Engine:
Docker Compose:
환경파일 절대 경로(값은 기록하지 않음):
Compose project 이름:
```

기준 commit에는 P01~P03 결과와 G1에 필요한 후속 application 설정이 포함돼야 한다.
현재 P01~P03 기준선만으로는 다음 항목이 남아 있으므로 이를 해결하지 않은 실행을
G1 통과로 기록하지 않는다.

- Backend가 Compose의 Django·PostgreSQL 환경변수를 읽는 설정
- AI·Backend 애플리케이션의 필수 bearer token 검증
- migration과 두 application readiness가 같은 통합 구성에서 성공하는 상태

## 3. 환경변수 준비

실제 운영 secret을 사용하지 않는다. 저장소 밖의 임시 환경파일에 이번 실행에서만
사용할 폐기 가능한 값을 넣고, 파일 경로만 증거에 남긴다. 값과 전체 DSN은 로그·문서에
복사하지 않는다.

| 분류 | 변수 | 판정 |
|---|---|---|
| 필수 secret | `POSTGRES_PASSWORD` | 누락·빈 값이면 Compose 구성 단계에서 거부 |
| 필수 연결 | `DATABASE_URL` | PostgreSQL user·password·DB·service DNS와 일치 |
| 필수 secret | `DJANGO_SECRET_KEY` | 누락·빈 값이면 Compose 구성 단계에서 거부 |
| 필수 secret | `AI_SERVICE_BEARER_TOKEN` | 누락·빈 값이면 Compose 구성 단계에서 거부 |
| 선택 기본값 | `POSTGRES_DB`, `POSTGRES_USER` | 기본값은 `retirewise_db`, `retirewise` |
| 선택 설정 | `DJANGO_DEBUG`, `DJANGO_ALLOWED_HOSTS` | 기본값을 명시적으로 검토 |
| readiness | `READINESS_TIMEOUT_SECONDS` | 0초 초과 10초 이하 |
| provider | `GOOGLE_API_KEY`, `OPENAI_API_KEY` | 실제 provider smoke test가 아니면 사용하지 않음 |
| legacy | `AI_CALLBACK_SECRET` | G1 readiness의 필수값으로 판정하지 않음 |

환경파일에는 최소한 다음 이름이 있어야 한다. 꺾쇠 값은 실제 운영 credential이 아닌
이번 실행 전용 값으로 교체한다.

```dotenv
POSTGRES_DB=retirewise_db
POSTGRES_USER=retirewise
POSTGRES_PASSWORD=<DISPOSABLE_DB_PASSWORD>
DATABASE_URL=postgresql://retirewise:<DISPOSABLE_DB_PASSWORD>@postgres:5432/retirewise_db
DJANGO_SECRET_KEY=<DISPOSABLE_DJANGO_SECRET>
AI_SERVICE_BEARER_TOKEN=<DISPOSABLE_SERVICE_TOKEN>
DJANGO_DEBUG=false
DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1,backend
READINESS_TIMEOUT_SECONDS=2
```

## 4. 공통 실행 변수와 안전한 정리

모든 명령은 저장소 루트에서 실행한다. project 이름은 다른 작업과 겹치지 않는
소문자·숫자·하이픈 조합으로 직접 정한다.

```bash
export P04_COMPOSE_PROJECT=retirewise-g1-20260905-01
export P04_ENV_FILE=/absolute/path/to/disposable-g1.env

test -n "$P04_COMPOSE_PROJECT"
test -f "$P04_ENV_FILE"
git status --short --branch
git rev-parse HEAD
docker version --format 'client={{.Client.Version}} server={{.Server.Version}}'
docker compose version
```

검증 성공·실패와 관계없이 같은 project가 만든 자원만 정리한다. 기존 project의
container·network·volume에는 `down`이나 `stop`을 실행하지 않는다.

```bash
cleanup_p04() {
  docker compose \
    --project-name "$P04_COMPOSE_PROJECT" \
    --env-file "$P04_ENV_FILE" \
    down --volumes --remove-orphans --timeout 30
}
trap cleanup_p04 EXIT INT TERM
```

이전에 중단된 같은 P04 실행이 있을 때만 최초 상태를 정리한다.

```bash
cleanup_p04
```

## 5. 구성과 clean build 명령

필수값을 전혀 제공하지 않은 구성은 실패해야 한다. `/dev/null`을 환경파일로 지정하고
관련 shell 환경도 제거해 저장소의 `.env`나 실행자 환경에 따른 오판을 막는다.

```bash
env \
  -u POSTGRES_PASSWORD \
  -u DATABASE_URL \
  -u DJANGO_SECRET_KEY \
  -u AI_SERVICE_BEARER_TOKEN \
  docker compose \
    --project-name "$P04_COMPOSE_PROJECT" \
    --env-file /dev/null \
    config --quiet
```

기대 결과는 0이 아닌 종료 코드와 누락된 변수 이름이다. secret 값이나 전체 DSN이
출력되면 안 된다.

준비한 환경파일로는 구성이 성공해야 한다.

```bash
docker compose \
  --project-name "$P04_COMPOSE_PROJECT" \
  --env-file "$P04_ENV_FILE" \
  config --quiet
```

기존 application image layer에 의존하지 않고 lock 설치를 다시 확인한다.

```bash
docker compose \
  --project-name "$P04_COMPOSE_PROJECT" \
  --env-file "$P04_ENV_FILE" \
  build --pull --no-cache migrate backend ai-pjt
```

## 6. 기동과 readiness 명령

먼저 저장 의존성만 기동하고 healthcheck가 끝날 때까지 기다린다.

```bash
docker compose \
  --project-name "$P04_COMPOSE_PROJECT" \
  --env-file "$P04_ENV_FILE" \
  up --detach --wait --wait-timeout 120 postgres redis
```

그다음 migration과 application을 포함한 전체 구성을 기동한다. `--wait`가 실패하거나
timeout이면 process가 일부 실행 중이어도 준비 완료로 판정하지 않는다.

```bash
docker compose \
  --project-name "$P04_COMPOSE_PROJECT" \
  --env-file "$P04_ENV_FILE" \
  up --detach --wait --wait-timeout 180
```

각 dependency와 application probe를 별도로 확인한다.

```bash
docker compose \
  --project-name "$P04_COMPOSE_PROJECT" \
  --env-file "$P04_ENV_FILE" \
  ps --all

docker compose \
  --project-name "$P04_COMPOSE_PROJECT" \
  --env-file "$P04_ENV_FILE" \
  exec -T postgres sh -c 'pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB"'

docker compose \
  --project-name "$P04_COMPOSE_PROJECT" \
  --env-file "$P04_ENV_FILE" \
  exec -T redis redis-cli ping

docker compose \
  --project-name "$P04_COMPOSE_PROJECT" \
  --env-file "$P04_ENV_FILE" \
  exec -T ai-pjt python readiness.py

docker compose \
  --project-name "$P04_COMPOSE_PROJECT" \
  --env-file "$P04_ENV_FILE" \
  exec -T backend python readiness.py
```

`migrate`는 실행 중 상태가 아니라 종료 코드 0이어야 한다.

```bash
test "$(
  docker inspect --format '{{.State.ExitCode}}' "$(
    docker compose \
      --project-name "$P04_COMPOSE_PROJECT" \
      --env-file "$P04_ENV_FILE" \
      ps --quiet --all migrate
  )"
)" = 0
```

정상 상태에서 PostgreSQL·Redis·AI·Backend는 `healthy`, `migrate`는 `exited (0)`이어야
한다. `running`, 열린 TCP port, HTTP 200 중 하나만으로 readiness를 대신하지 않는다.

## 7. 격리된 실패 확인 명령

다음 명령은 P04 project 내부에서만 실행한다. 하나의 실패를 확인한 뒤 dependency를
복구하고 정상 readiness를 다시 확인한 다음 다음 사례로 넘어간다.

### 7.1 잘못된 probe 설정

```bash
docker compose \
  --project-name "$P04_COMPOSE_PROJECT" \
  --env-file "$P04_ENV_FILE" \
  exec -T -e READINESS_TIMEOUT_SECONDS=0 ai-pjt python readiness.py

docker compose \
  --project-name "$P04_COMPOSE_PROJECT" \
  --env-file "$P04_ENV_FILE" \
  exec -T -e READINESS_PORT=0 backend python readiness.py
```

두 명령은 모두 종료 코드 1과 `not ready: ReadinessConfigurationError`를 반환해야 하며
credential이나 URL 값을 출력하면 안 된다.

### 7.2 PostgreSQL 장애

```bash
docker compose \
  --project-name "$P04_COMPOSE_PROJECT" \
  --env-file "$P04_ENV_FILE" \
  stop postgres

docker compose \
  --project-name "$P04_COMPOSE_PROJECT" \
  --env-file "$P04_ENV_FILE" \
  exec -T ai-pjt python readiness.py

docker compose \
  --project-name "$P04_COMPOSE_PROJECT" \
  --env-file "$P04_ENV_FILE" \
  exec -T backend python readiness.py
```

AI·Backend readiness는 모두 실패해야 한다. AI process의 `/health`가 계속 응답하더라도
PostgreSQL probe가 실패하면 ready로 판정하지 않는다.

복구 후 두 readiness를 다시 확인한다.

```bash
docker compose \
  --project-name "$P04_COMPOSE_PROJECT" \
  --env-file "$P04_ENV_FILE" \
  up --detach --wait --wait-timeout 120 postgres

docker compose \
  --project-name "$P04_COMPOSE_PROJECT" \
  --env-file "$P04_ENV_FILE" \
  exec -T ai-pjt python readiness.py

docker compose \
  --project-name "$P04_COMPOSE_PROJECT" \
  --env-file "$P04_ENV_FILE" \
  exec -T backend python readiness.py
```

### 7.3 Redis 장애

```bash
docker compose \
  --project-name "$P04_COMPOSE_PROJECT" \
  --env-file "$P04_ENV_FILE" \
  stop redis

docker compose \
  --project-name "$P04_COMPOSE_PROJECT" \
  --env-file "$P04_ENV_FILE" \
  exec -T backend python readiness.py
```

Backend readiness는 실패해야 한다. AI는 Redis를 현재 필수 dependency로 사용하지
않으므로 Redis 장애만으로 AI readiness 실패를 기대하지 않는다.

```bash
docker compose \
  --project-name "$P04_COMPOSE_PROJECT" \
  --env-file "$P04_ENV_FILE" \
  exec -T ai-pjt python readiness.py
```

복구 후 Backend readiness를 다시 확인한다.

```bash
docker compose \
  --project-name "$P04_COMPOSE_PROJECT" \
  --env-file "$P04_ENV_FILE" \
  up --detach --wait --wait-timeout 120 redis

docker compose \
  --project-name "$P04_COMPOSE_PROJECT" \
  --env-file "$P04_ENV_FILE" \
  exec -T backend python readiness.py
```

## 8. 종료와 잔여 자원 확인

```bash
cleanup_p04
trap - EXIT INT TERM

test -z "$(
  docker compose \
    --project-name "$P04_COMPOSE_PROJECT" \
    --env-file "$P04_ENV_FILE" \
    ps --quiet --all
)"

test -z "$(
  docker volume ls \
    --filter "label=com.docker.compose.project=$P04_COMPOSE_PROJECT" \
    --quiet
)"

test -z "$(
  docker network ls \
    --filter "label=com.docker.compose.project=$P04_COMPOSE_PROJECT" \
    --quiet
)"
```

세 확인이 모두 종료 코드 0이어야 한다. 실패하면 잔여 자원 이름을 기록하되 다른
Compose project의 자원은 삭제하지 않는다.

## 9. G1 판정표

P04에서는 `실제 결과·증거`와 `G1 판정`을 채우지 않는다. G1 실행자가 동일한 기준
commit에서 위 명령을 실행한 뒤 채운다.

| ID | 검사 대상 | 통과 신호 | 실패·반환 대상 | 실제 결과·증거 | G1 판정 |
|---|---|---|---|---|---|
| G1-PRE-01 | 기준 상태 | SHA·Docker 버전·환경파일 경로·project 이름 기록 | 기준 불명확 → 통합 담당 |  |  |
| G1-CFG-01 | secret 기본 거부 | 값 미제공 `config`가 non-zero | Compose 계약 → P03 |  |  |
| G1-CFG-02 | 유효 구성 | 임시값 제공 `config --quiet`가 0 | 변수 mapping → P01~P03 |  |  |
| G1-BLD-01 | clean image build | `--pull --no-cache` build가 0 | AI image → P01, Backend image → P02 |  |  |
| G1-INF-01 | PostgreSQL | healthcheck와 `pg_isready` 성공 | PostgreSQL·Compose → P03 |  |  |
| G1-INF-02 | Redis | healthcheck와 `PONG` | Redis·Compose → P03 |  |  |
| G1-MIG-01 | migration | one-shot 종료 코드 0 | Backend 설정 → I06/P02, 순서 → P03 |  |  |
| G1-AI-01 | AI readiness | HTTP liveness와 PostgreSQL probe, exit 0 | probe/image → P01, app 설정 → I05 |  |  |
| G1-BE-01 | Backend readiness | process·DB·migration·Redis, exit 0 | probe/image → P02, app 설정 → I06 |  |  |
| G1-NEG-01 | probe 설정 오류 | 잘못된 timeout·port가 exit 1 | 해당 probe → P01/P02 |  |  |
| G1-NEG-02 | PostgreSQL 장애 | AI·Backend not ready, 복구 후 ready | P01/P02/P03 또는 application 담당 |  |  |
| G1-NEG-03 | Redis 장애 | Backend만 not ready, 복구 후 ready | P02/P03 |  |  |
| G1-SEC-01 | 오류 마스킹 | 오류에 secret·전체 DSN 미노출 | P01/P02 또는 application 담당 |  |  |
| G1-END-01 | 종료·정리 | P04 project container·volume·network 0개 | Compose cleanup → P03 |  |  |

다음 중 하나라도 해당하면 G1을 통과로 기록하지 않는다.

- 필수 행이 실패하거나 실행되지 않았다.
- `running` 또는 process up만 확인하고 dependency readiness를 확인하지 않았다.
- 기존 volume이나 기존 image만 사용해 clean 설치·빌드를 생략했다.
- secret 기본 거부 또는 오류 마스킹을 확인하지 않았다.
- 종료 뒤 P04 project 자원이 남았다.
- 실행 명령, 핵심 출력, 남은 미검증이 기록되지 않았다.

## 10. G1 증거 양식

```text
작업 게이트: G1 — D2 clean start·readiness
기준 commit SHA:
P04 절차 commit SHA:
실행 환경:
사용한 Compose project:
사용한 환경파일 경로(값 제외):

실행 명령:
-

핵심 결과:
-

판정표:
- G1-PRE-01:
- G1-CFG-01:
- G1-CFG-02:
- G1-BLD-01:
- G1-INF-01:
- G1-INF-02:
- G1-MIG-01:
- G1-AI-01:
- G1-BE-01:
- G1-NEG-01:
- G1-NEG-02:
- G1-NEG-03:
- G1-SEC-01:
- G1-END-01:

남은 미검증:
-

실패 반환 대상:
-

정리 결과:
-

G1 판정: 통과 / 미통과 / 선행 작업 대기
판정자:
```

“기동 성공”, “연동 완료”, “테스트 통과”만으로 판정하지 않는다. 각 결과에는 실행
명령, 종료 코드, secret을 제거한 핵심 출력 또는 보관 위치, 미검증 항목을 연결한다.
