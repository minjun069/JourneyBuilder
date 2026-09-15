# AI runtime

이 문서는 P01(D2 AI 실행환경)의 설치·컨테이너·환경변수·readiness와 후속 담당자 인수 정보를 기록한다. 외부 HTTP·AI 계약은 runtime schema와 contract test로 검증하며, 이 문서는 해당 계약을 변경하지 않는다.

## 실행 기준

- Python: 3.13.14
- Container port: 8001
- Application command: `uvicorn main:app --host 0.0.0.0 --port 8001`
- Required runtime dependency: PostgreSQL
- Optional external dependencies: LLM provider, Langfuse

Langfuse·LangSmith의 raw model callback tracing은 typed masking 경계를 우회하므로
활성화하지 않는다. `LANGFUSE_ENABLED=true`는 callback을 붙이지 않고, 기존
`MaskedInstrumentationSink`가 직렬화·마스킹한 typed event만 Langfuse v4 exporter로
보낸다. 자동 LangChain·LangSmith tracing 환경변수는 서비스가 요청을 받기 전에
startup을 실패시킨다.

`requirements.txt`는 직접 의존성의 호환 범위를 사람이 관리한다. `requirements.lock`은 Python 3.13/Linux에서 해석한 직접·전이 의존성 전체와 배포 파일 hash를 고정하며 Docker와 CI가 설치한다.

FastAPI는 현재 transport 구현의 기준선인 0.115 minor 범위에 유지한다. framework
범위를 올릴 경우 `/api/system/v1/health`, SSE request validation과 terminal 회귀검사를 먼저
통과시킨 뒤 lock을 함께 갱신한다.
`--exclude-newer` 날짜는 의도적으로 의존성 기준 시점을 올릴 때만 변경한다.

lock을 갱신할 때는 저장소 루트에서 다음 명령을 실행하고 unit test와 clean image build를 다시 수행한다.

```bash
uv pip compile \
  --python-version 3.13 \
  --python-platform x86_64-unknown-linux-gnu \
  --generate-hashes \
  --exclude-newer 2026-09-03T00:00:00Z \
  --output-file ai-pjt/requirements.lock \
  ai-pjt/requirements.txt
```

## 환경변수

직접 실행할 때는 `.env.example`을 `.env`로 복사한 뒤 실제 값은 secret store나 로컬 비추적 파일에서 주입한다. `.env.example`의 `change-me`와 빈 key는 실행 가능한 기본 credential이 아니다.

| 분류 | 변수 | 현재 판정 |
|---|---|---|
| 필수 | `DATABASE_URL` | AI startup과 readiness가 사용하는 PostgreSQL DSN |
| 조건부 필수 | `GOOGLE_API_KEY` | `LLM_PROVIDER=gemini`일 때 필요 |
| 조건부 필수 | `OPENAI_API_KEY`, `LLM_BASE_URL` | OpenAI 또는 OpenAI 호환 LLM provider에 맞춰 필요. 현재 설치된 embedding adapter는 Gemini |
| startup 필수 | `AI_SERVICE_BEARER_TOKEN` | 누락·빈 값·단순 placeholder를 거부하고 `/api/ai/v2/chat/stream` request bearer로 사용 |
| 선택 | `RAG_FINANCIAL_COLLECTION`, `RAG_INTERFACE_COLLECTION` | 승인 catalog의 논리 collection. 기본값은 각각 `financial-knowledge`, `interface-guides` |
| 선택 | `RAG_TOP_K`, `RAG_FETCH_K`, `RAG_MIN_DENSE_SCORE` | 최종 5건, dense·BM25 후보 10건, dense 최소 0.30 |
| 선택 | `RAG_INDEX_RETENTION_DAYS`, `EMBED_DIM` | retired 인덱스 30일 보관, embedding 차원 |
| Langfuse 조건부 필수 | `LANGFUSE_ENABLED`, `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_BASE_URL` | 활성화할 때 세 연결값을 모두 명시하며 URL credential을 금지 |
| Langfuse 선택 | `LANGFUSE_TRACING_ENVIRONMENT`, `LANGFUSE_RELEASE` | 환경 분리와 배포 SHA·버전 표시 |
| readiness | `READINESS_HTTP_URL`, `READINESS_TIMEOUT_SECONDS` | 기본값은 컨테이너 내부 `/api/system/v1/health`, 2초 |
| 미래 선택 | `REDIS_URL` | 현재 AI runtime과 readiness는 사용하지 않음 |

## Langfuse v4 사용

가장 빠른 시작 방법은 Langfuse Cloud에서 project와 API key를 만드는 것이다. 자체
호스팅은 한 container짜리 구형 v2 snippet을 사용하지 않고
[공식 v4 Docker Compose](https://langfuse.com/self-hosting/deployment/docker-compose)를
별도 stack으로 기동한다. 애플리케이션 `.env`에는 실제 배포 위치에 맞는 API base URL을
명시하며 credential을 Git에 커밋하지 않는다.

```dotenv
LANGFUSE_ENABLED=true
LANGFUSE_PUBLIC_KEY=pk-lf-...
LANGFUSE_SECRET_KEY=sk-lf-...
LANGFUSE_BASE_URL=https://cloud.langfuse.com
LANGFUSE_TRACING_ENVIRONMENT=development
LANGFUSE_RELEASE=<git-sha-or-release>
LANGCHAIN_TRACING_V2=false
```

Cloud region이나 자체 호스팅 주소가 다르면 `LANGFUSE_BASE_URL`을 해당 API 주소로
바꾼다. 설정 확인은 실제 고객 요청 전에 검증 전용 project credential을 사용하는 별도
연결 smoke로 실행한다.

```bash
docker compose run --rm --no-deps ai-pjt \
  python -c 'from langfuse import get_client; raise SystemExit(0 if get_client().auth_check() else 1)'
```

연결 확인 뒤 AI 서비스를 기동하면 `retirewise.<event_family>.<stage>` observation에서
가명 trace correlation, 단계·상태·지연, 모델 token·비용 metadata를 확인할 수 있다.
prompt·response·고객 snapshot·도구 원문은 보내지 않는다. SDK는 이 exporter가 직접
만든 span만 허용하므로 `langfuse.langchain.CallbackHandler`, `@observe`, 자동
LangChain tracing을 production 경로에 추가하지 않는다.

Langfuse 전송은 비동기다. FastAPI lifespan 종료 시 SDK `shutdown()`으로 대기 event를
flush하며, emit 또는 shutdown 장애는 AI 업무 응답을 실패시키지 않는다. Langfuse는
readiness dependency가 아니므로 장애 여부는 exporter 지표와 별도 smoke에서 확인한다.

## Container build와 실행

공용 step registry를 image에 포함하기 위해 저장소 루트를 build context로 사용한다.

```bash
docker build -f ai-pjt/Dockerfile -t retirewise-ai:g1 .
```

기본 배포에서는 AI 포트를 host에 공개하지 않고 Backend가 Compose 내부 주소 `http://ai-pjt:8001`로 호출한다. 로컬에서 직접 smoke test할 때만 loopback에 임시 공개한다.

```bash
docker run --rm \
  --env-file ai-pjt/.env \
  --network de_net \
  -p 127.0.0.1:8001:8001 \
  retirewise-ai:g1
```

## Liveness와 readiness

`GET /api/system/v1/health`는 HTTP process가 응답하고 예상 payload `{"status":"ok"}`를 반환하는지 확인하는 liveness다. `readiness.py`는 컨테이너가 새 요청을 받을 조건을 다음 순서로 확인한다.

1. `DATABASE_URL`과 probe 설정이 유효하다.
2. 컨테이너 내부 `GET /api/system/v1/health`가 200과 예상 payload를 반환한다.
3. 현재 LangGraph checkpointer가 사용하는 PostgreSQL에서 `SELECT 1`이 성공한다.

성공하면 `ready`를 출력하고 exit 0, 실패하면 예외 종류만 출력하고 exit 1을 반환한다. DSN·credential·원격 URL이 오류 출력에 포함되지 않도록 예외 메시지는 출력하지 않는다. Dockerfile의 `HEALTHCHECK`가 이 exit code를 `healthy` 또는 `unhealthy`로 변환한다.

실제 LLM 호출, Redis, Langfuse는 readiness에서 제외한다. 이들을 포함하면 외부 장애·rate limit·비용 때문에 정상 AI process까지 연쇄적으로 traffic에서 제외될 수 있다. provider와 SSE 상태는 별도 smoke test와 운영 지표로 확인한다.

readiness unit test:

```bash
python3 -m unittest discover -s ai-pjt/tests -p 'test_*.py'
```

## Compose·G1 인수 사항

P03 Compose 담당자는 다음 조건을 사용한다.

- build context: 저장소 루트, Dockerfile `ai-pjt/Dockerfile`
- container port: 8001, 기본 host publish 없음
- service DNS 예시: `ai-pjt`
- PostgreSQL의 실제 user/password/database/host를 `DATABASE_URL`로 주입
- AI가 사용할 PostgreSQL service가 먼저 healthy가 되도록 dependency condition 설정
- AI Dockerfile의 healthcheck를 덮어쓴다면 동일한 `python readiness.py` 조건 유지
- 현재 AI runtime은 Redis를 사용하지 않으므로 Redis를 AI readiness dependency로 연결하지 않음

G1은 빈 환경에서 lock 설치, PostgreSQL 기동, AI `healthy`, 종료 순서와 다음 실패를 재현했다.

- `DATABASE_URL` 누락
- PostgreSQL 미기동 또는 연결 끊김
- `/api/system/v1/health`의 비정상 status 또는 payload
- 0 이하·10초 초과·숫자가 아닌 readiness timeout

## 후속 AI transport 인수 사항

G1은 `DATABASE_URL`, provider credential과 `AI_SERVICE_BEARER_TOKEN`을 startup에서
실패 폐쇄형으로 검증한다. 후속 transport 구현은 이 readiness 경계를 유지하고 다음
항목을 자동 검사해야 한다.

1. `AI_SERVICE_BEARER_TOKEN`은 `/api/ai/v2/chat/stream` 인증에 연결된 rotation 가능한 secret이다.
2. 비스트리밍 `/v2/chat`, 구형 `/chat[/stream]`, HTTP callback 전송과 관련 환경변수는 제거됐다.
3. startup/config 오류와 SSE 로그에 API key, bearer token, 전체 DSN을 남기지 않는다.
4. `/ready` endpoint를 application에 추가한다면 `readiness.py`와 PostgreSQL 판정이 서로 달라지지 않게 하나를 공통 경계로 만들고 Docker healthcheck도 그 경계를 사용한다.
5. 실제 LLM·Redis·Langfuse 호출을 readiness에 추가하지 않는다. 별도 provider/SSE smoke test와 운영 관측으로 검증한다.

후속 검증에는 인증 없는 SSE request 거부, 제거된 route의 404, DSN/token 마스킹, PostgreSQL 정상·장애,
liveness 정상인데 PostgreSQL이 실패하는 경우를 포함한다. readiness 구현 변경 시
`tests/test_readiness.py`도 같은 변경에서 갱신한다.

## P01 Docker 검증 결과

2026-09-03에 Docker Desktop 4.87.0, Engine 29.7.2에서 다음 범위를 직접 검증했다.

- `docker build --no-cache -t retirewise-ai:d2 ai-pjt`: Python 3.13.14 base image와 `pip --require-hashes` 설치를 포함한 clean build 성공
- 격리 network와 새 `postgres:16-bookworm` container에서 PostgreSQL health 확인 후 AI 기동 성공
- AI container: Python `3.13.14`, user `app`/effective UID `999`, host publish 없이 `8001/tcp`만 노출
- 정상 상태: `GET /api/system/v1/health` 200, `python readiness.py`의 출력 `ready`·exit 0, Docker 상태 `healthy`
- `DATABASE_URL` 누락: `not ready: ReadinessConfigurationError`·exit 1이며 credential 출력 없음
- PostgreSQL 중단: `/api/system/v1/health`는 계속 200이지만 readiness는 `not ready: OperationalError`·exit 1, healthcheck 실패 3회 뒤 Docker 상태 `unhealthy`
- 검증용 container와 network는 결과 확인 뒤 제거

이 P01 결과는 AI image와 PostgreSQL에 한정된 역사적 기록이다. G1에서 실제 Compose
service name·credential·dependency condition을 사용한 AI·Backend·Redis·PostgreSQL
전체 clean start와 장애 복구·종료를 다시 검증했다. 실제 LLM provider 호출과 Backend
SSE 연결은 별도 smoke test가 필요하다.
