# AI runtime

이 문서는 P01(D2 AI 실행환경)의 설치·컨테이너·환경변수·readiness와 후속 담당자 인수 정보를 기록한다. 외부 HTTP·AI 계약은 runtime schema와 contract test로 검증하며, 이 문서는 해당 계약을 변경하지 않는다.

## 실행 기준

- Python: 3.13.14
- Container port: 8001
- Application command: `uvicorn main:app --host 0.0.0.0 --port 8001`
- Required runtime dependency: PostgreSQL
- Optional external dependencies: LLM provider, Backend callback

Langfuse·LangSmith의 model callback tracing은 typed masking 경계를 우회하므로
활성화하지 않는다. `LANGFUSE_ENABLED`나 자동 LangSmith tracing 환경변수가 켜져
있으면 서비스는 요청을 받기 전에 startup을 실패시킨다.

`requirements.txt`는 직접 의존성의 호환 범위를 사람이 관리한다. `requirements.lock`은 Python 3.13/Linux에서 해석한 직접·전이 의존성 전체와 배포 파일 hash를 고정하며 Docker와 CI가 설치한다.

FastAPI는 현재 transport 구현의 기준선인 0.115 minor 범위에 유지한다. F04-T01이
request·callback 계약을 변경하면서 framework 범위를 올릴 경우 `/health`, request
validation, background callback 회귀검사를 먼저 통과시킨 뒤 lock을 함께 갱신한다.
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
| startup 필수 | `AI_SERVICE_BEARER_TOKEN` | 누락·빈 값·단순 placeholder를 거부하고 `/v2/chat[/stream]` request와 Backend callback에 같은 bearer로 사용 |
| legacy 설정 호환 | `AI_CALLBACK_SECRET` | 읽기만 하며 endpoint에서는 사용하지 않음. 구형 `/chat[/stream]`은 body·header와 무관하게 항상 410 반환 |
| 선택 | `RAG_FINANCIAL_COLLECTION`, `RAG_INTERFACE_COLLECTION` | 승인 catalog의 논리 collection. 기본값은 각각 `financial-knowledge`, `interface-guides` |
| 선택 | `RAG_TOP_K`, `RAG_FETCH_K`, `RAG_MIN_DENSE_SCORE` | 최종 5건, dense·BM25 후보 10건, dense 최소 0.30 |
| 선택 | `RAG_INDEX_RETENTION_DAYS`, `EMBED_DIM`, `LANGFUSE_*` | retired 인덱스 30일 보관, embedding 차원, 관측 조정값 |
| readiness | `READINESS_HTTP_URL`, `READINESS_TIMEOUT_SECONDS` | 기본값은 컨테이너 내부 `/health`, 2초 |
| 미래 선택 | `REDIS_URL` | 현재 AI runtime과 readiness는 사용하지 않음 |

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

`GET /health`는 HTTP process가 응답하고 예상 payload `{"status":"ok"}`를 반환하는지 확인하는 liveness다. `readiness.py`는 컨테이너가 새 요청을 받을 조건을 다음 순서로 확인한다.

1. `DATABASE_URL`과 probe 설정이 유효하다.
2. 컨테이너 내부 `GET /health`가 200과 예상 payload를 반환한다.
3. 현재 LangGraph checkpointer가 사용하는 PostgreSQL에서 `SELECT 1`이 성공한다.

성공하면 `ready`를 출력하고 exit 0, 실패하면 예외 종류만 출력하고 exit 1을 반환한다. DSN·credential·원격 URL이 오류 출력에 포함되지 않도록 예외 메시지는 출력하지 않는다. Dockerfile의 `HEALTHCHECK`가 이 exit code를 `healthy` 또는 `unhealthy`로 변환한다.

실제 LLM 호출, Backend callback, Redis, Langfuse는 readiness에서 제외한다. 이들을 포함하면 외부 장애·rate limit·비용 때문에 정상 AI process까지 연쇄적으로 traffic에서 제외될 수 있다. provider와 callback 상태는 별도 smoke test와 운영 지표로 확인한다.

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
- `/health`의 비정상 status 또는 payload
- 0 이하·10초 초과·숫자가 아닌 readiness timeout

## 후속 AI transport 인수 사항

G1은 `DATABASE_URL`, provider credential과 `AI_SERVICE_BEARER_TOKEN`을 startup에서
실패 폐쇄형으로 검증한다. 후속 transport 구현은 이 readiness 경계를 유지하고 다음
항목을 자동 검사해야 한다.

1. `AI_SERVICE_BEARER_TOKEN`은 request와 callback 양방향 인증에 같은 rotation 가능한 secret으로 연결돼 있다.
2. legacy `/chat[/stream]`은 410으로 종료됐다. 남은 v1 내부 helper를 제거할 때 `AI_CALLBACK_SECRET` 환경 예시도 함께 제거한다.
3. startup/config 오류와 callback 로그에 API key, bearer token, 전체 DSN을 남기지 않는다.
4. `/ready` endpoint를 application에 추가한다면 `readiness.py`와 PostgreSQL 판정이 서로 달라지지 않게 하나를 공통 경계로 만들고 Docker healthcheck도 그 경계를 사용한다.
5. 실제 LLM·Backend·Redis·Langfuse 호출을 readiness에 추가하지 않는다. 별도 provider/callback smoke test와 운영 관측으로 검증한다.

후속 검증에는 인증 없는 request·callback 거부, DSN/token 마스킹, PostgreSQL 정상·장애,
liveness 정상인데 PostgreSQL이 실패하는 경우를 포함한다. readiness 구현 변경 시
`tests/test_readiness.py`도 같은 변경에서 갱신한다.

## P01 Docker 검증 결과

2026-09-03에 Docker Desktop 4.87.0, Engine 29.7.2에서 다음 범위를 직접 검증했다.

- `docker build --no-cache -t retirewise-ai:d2 ai-pjt`: Python 3.13.14 base image와 `pip --require-hashes` 설치를 포함한 clean build 성공
- 격리 network와 새 `postgres:16-bookworm` container에서 PostgreSQL health 확인 후 AI 기동 성공
- AI container: Python `3.13.14`, user `app`/effective UID `999`, host publish 없이 `8001/tcp`만 노출
- 정상 상태: `GET /health` 200, `python readiness.py`의 출력 `ready`·exit 0, Docker 상태 `healthy`
- `DATABASE_URL` 누락: `not ready: ReadinessConfigurationError`·exit 1이며 credential 출력 없음
- PostgreSQL 중단: `/health`는 계속 200이지만 readiness는 `not ready: OperationalError`·exit 1, healthcheck 실패 3회 뒤 Docker 상태 `unhealthy`
- 검증용 container와 network는 결과 확인 뒤 제거

이 P01 결과는 AI image와 PostgreSQL에 한정된 역사적 기록이다. G1에서 실제 Compose
service name·credential·dependency condition을 사용한 AI·Backend·Redis·PostgreSQL
전체 clean start와 장애 복구·종료를 다시 검증했다. 실제 LLM provider 호출과 Backend
callback은 후속 transport 구현 뒤 별도 smoke test가 필요하다.
