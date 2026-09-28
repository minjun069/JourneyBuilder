# 1. 빌드 및 배포 매뉴얼

작성일 2026-09-28 / 기준 커밋 `1b83057` (master) / 저장소 `https://lab.ssafy.com/s15-fintech-finance-sub1/S15P21B109.git`

이 문서는 GitLab 에서 소스를 클론한 뒤 로컬과 운영 서버(EC2)에서 빌드·배포하는 전 과정을 정리한다. 이 프로젝트는 Java 가 아니라 Python·Node 기반이므로 "JVM·WAS" 항목은 Python 런타임과 ASGI 서버(uvicorn), 웹서버(nginx)로 대응해 적었다.

---

## 1. 프로젝트 개요와 아키텍처

서비스명: **Journey Builder** (코드·DB 명칭 `RetireWise`). AI 재무상담·재무진단 웹 서비스다. PB 대시보드 사이드바에는 `RetireWise` 가 표시된다.

### 1-1. 구성 요소 (모노레포)

| 경로 | 역할 | 기술 | 컨테이너 포트 |
|---|---|---|---|
| `front-pjt/` | 사용자·PB 웹 화면 (SPA) | Vue 3, Vite, Pinia, ECharts | 로컬 Vite 5173 / 운영 nginx 80 |
| `backend-pjt/` | 업무 API, 인증(JWT), AI 요청 상태, 리포트·시뮬레이션 계산 | Django 5.2, DRF, uvicorn(ASGI) | 8000 |
| `ai-pjt/` | AI 상담 오케스트레이션, RAG 검색 | FastAPI, LangGraph, LlamaIndex | 8001 |
| `db/postgres/init.sql` | PostgreSQL 최초 초기화 (pgvector, RAG 테이블) | PostgreSQL 16 | 5432 |
| `deploy/` | 배포 스크립트, nginx 설정, env 생성 스크립트 | bash, nginx | — |
| `contract-fixtures/` | 서비스 간 계약·계산 정책 JSON (이미지에 복사됨) | JSON | — |
| `ai-docs/` | AI 요구사항·계약·보안 문서 | Markdown | — |

### 1-2. Compose 서비스

| 서비스 | 이미지 / 빌드 | 역할 | 실행 형태 |
|---|---|---|---|
| `postgres` | `pgvector/pgvector:pg16` | 업무 DB + AI 체크포인트 + RAG 벡터 인덱스 | 상시 |
| `redis` | `redis:7.4-bookworm` (`--appendonly yes`) | Backend 의 AI 요청 상태·멱등키·시뮬레이션 잠금 | 상시 |
| `migrate` | `backend-pjt/Dockerfile` | `python manage.py migrate --noinput` | one-shot |
| `rag-bootstrap` | `ai-pjt/Dockerfile` | 공식 RAG artifact import 후 활성화 (`python -m rag.bootstrap`) | one-shot |
| `backend` | `backend-pjt/Dockerfile` | Django API | 상시 |
| `gateway` | `nginx:1.29.1-alpine` | `/api/` 리버스 프록시 | 상시 |
| `ai-pjt` | `ai-pjt/Dockerfile` | AI 서비스 | 상시 |
| `web` (운영 override 만) | `front-pjt/Dockerfile` | SPA 정적 서빙 + `/api` 위임 | 상시 |

### 1-3. 기동 순서 (Compose `depends_on`)

```text
PostgreSQL healthy ─┬─→ migrate (one-shot, exit 0) ──┬─→ backend ─→ gateway
                    │                                 │      └─(healthy)─→ web  (운영 override)
                    └─→ rag-bootstrap (one-shot, exit 0) ─┴─→ ai-pjt  (migrate·rag-bootstrap 둘 다 완료 필요)
Redis healthy ────────────────────────────────────→ backend
```

### 1-4. 요청 흐름

```text
[로컬 개발]  브라우저 → :5173 Vite dev ─/api 프록시→ 127.0.0.1:8081 gateway → backend:8000 → ai-pjt:8001
[로컬 운영재현] 브라우저 → :8080 web(nginx) ─/api/→ gateway:80 → backend:8000 → ai-pjt:8001
[운영]       브라우저 ─HTTPS 443→ 호스트 nginx(certbot)
                         ├─ /      → 127.0.0.1:8080 web
                         └─ /api/  → 127.0.0.1:8081 gateway → backend:8000 → ai-pjt:8001
                                                                 └→ postgres:5432, redis:6379
```

브라우저는 AI 서비스를 직접 호출하지 않는다. Backend 가 `AI_SERVICE_BEARER_TOKEN` 으로 AI 서비스(`POST /api/ai/v2/chat/stream`)를 호출하고 결과를 SSE 로 중계한다.

### 1-5. 포트 표

| 서비스 | 컨테이너 포트 | 로컬 (기본 compose) | 로컬 (운영 재현) | 운영 EC2 |
|---|---|---|---|---|
| postgres | 5432 (`expose`) | 미공개 | 미공개 | 미공개 |
| redis | 6379 (`expose`) | 미공개 | 미공개 | 미공개 |
| backend | 8000 (`expose`) | 미공개 | 미공개 | 미공개 |
| ai-pjt | 8001 (`expose`) | 미공개 | 미공개 | 미공개 |
| gateway | 80 | `${API_GATEWAY_PORT}` → `.env` 기준 `127.0.0.1:8081` | 동일 | `127.0.0.1:8081` |
| web | 80 | 서비스 없음 | `127.0.0.1:${WEB_HOST_PORT:-8080}` | `127.0.0.1:8080` |
| Vite dev | 5173 | `localhost:5173` | — | — |
| 호스트 nginx | — | — | — | 80(→443 리다이렉트), 443 |
| SSH | — | — | — | 22 |

`API_GATEWAY_PORT` 를 비우면 Compose 기본값 `8080`(0.0.0.0 바인딩)이 열린다. `.env.example` 에는 `127.0.0.1:8081` 이 들어 있다.

---

## 2. 사용 기술·제품 버전

### 2-1. 런타임·서버·인프라

| 구분 | 제품 | 버전 | 설정값·비고 | 근거 |
|---|---|---|---|---|
| 언어 런타임 (JVM 대응) | Python (CPython) | **3.13.14** | 이미지 `python:3.13.14-slim-bookworm`, `PYTHONDONTWRITEBYTECODE=1`, `PYTHONUNBUFFERED=1` | `backend-pjt/Dockerfile`, `ai-pjt/Dockerfile` |
| WAS (ASGI 서버) | uvicorn | **0.52.4** | backend: `uvicorn config.asgi:application --host 0.0.0.0 --port 8000 --workers 2` / ai: `uvicorn main:app --host 0.0.0.0 --port 8001` | 각 Dockerfile `CMD` |
| 웹 프레임워크 | Django / FastAPI | 5.2.17 / 0.115.14 | Django `DEBUG=false`, `TIME_ZONE=Asia/Seoul`, `USE_TZ=True` | `requirements.lock`, `settings.py` |
| 웹서버 (API 게이트웨이) | nginx | **1.29.1-alpine** | upstream `backend:8000 resolve`, resolver `127.0.0.11 valid=10s`, keepalive 32 | `deploy/nginx/default.conf` |
| 웹서버 (SPA 정적) | nginx | **1.27-alpine** | gzip on, `/assets/` 30일 immutable 캐시, `index.html` `Cache-Control: no-cache`, history 모드 fallback | `front-pjt/nginx/default.conf` |
| 웹서버 (호스트, TLS) | nginx (apt) | **1.24** | `/api/` → 127.0.0.1:8081 (`proxy_read_timeout 140s`, buffering off), `/` → 127.0.0.1:8080 | `deploy/nginx-host.conf` |
| 프론트 빌드 런타임 | Node.js | **22** (`node:22-bookworm-slim`) | `package.json` engines `^20.19.0 \|\| >=22.12.0` | `front-pjt/Dockerfile` |
| 번들러 | Vite | 8.0.16 | dev 서버 5173, `/api` 프록시 | `package-lock.json` |
| DB | PostgreSQL + pgvector | **16.15** / vector **0.8.6** | 이미지 `pgvector/pgvector:pg16` | `docker-compose.yml`, 실측 |
| 캐시·상태 저장소 | Redis | **7.4** | `redis:7.4-bookworm`, AOF(`--appendonly yes`), 인증 없음(내부망 전용) | `docker-compose.yml` |
| 컨테이너 | Docker / Docker Compose | 로컬 29.7.2 / v5.4.0, 서버 docker-ce 29.8 / v5.5 | BuildKit 사용 | 실측, `deploy/README.md` |
| OS (운영) | Ubuntu | 24.04.4 LTS | 4 vCPU, 15 GiB RAM, 309 GB | `deploy/README.md` |
| CI/CD | GitLab CI + GitLab Runner | Runner 19.3.1 | shell executor, 태그 `ssafy-ec2` | `.gitlab-ci.yml` |
| TLS | certbot (Let's Encrypt) | apt 패키지 | `certbot --nginx`, `certbot.timer` 자동 갱신 | `deploy/README.md` |
| IDE | Visual Studio Code | **1.138.0** | 확장: Vue (Official) — Volar | 실측, `front-pjt/README.md` |
| 형상관리 | Git | 2.55.0 | `.gitattributes` 로 `*.sh` LF 강제 | 실측 |

### 2-2. 헬스체크 설정값

| 대상 | 검사 명령 | interval / timeout / retries / start |
|---|---|---|
| postgres | `pg_isready -U $POSTGRES_USER -d $POSTGRES_DB` | 5s / 5s / 10 / 5s |
| redis | `redis-cli ping` | 5s / 3s / 10 / 5s |
| backend | `python readiness.py` (TCP 8000 연결, DB `SELECT 1`, 미적용 migration 0건, Redis `PING`) | 10s / 5s / 3 / 20s |
| ai-pjt | `python readiness.py` (`/api/system/v1/health` 200, DB `SELECT 1`, RAG active 인덱스·chunk·임베딩 모델/차원 일치) | 10s / 5s / 3 / 20s |
| web | `wget -q -O /dev/null http://127.0.0.1/` | 10s / 3s / 3 / 5s |

LLM·임베딩 실호출은 어떤 헬스체크에도 포함되지 않는다. 키가 틀려도 `healthy` 가 될 수 있으므로 9장의 실호출 검증 명령으로 확인한다.

### 2-3. Backend 주요 라이브러리 (`backend-pjt/requirements.lock`, 해시 고정)

| 패키지 | 버전 | 패키지 | 버전 |
|---|---|---|---|
| django | 5.2.17 | djangorestframework | 3.17.2 |
| djangorestframework-simplejwt | 5.5.1 | django-cors-headers | 4.9.0 |
| uvicorn | 0.52.4 | httpx | 0.28.1 |
| redis (py) | 5.2.1 | psycopg2-binary | 2.9.12 |
| setuptools (빌드 전용) | 84.0.0 | asgiref / pyjwt / sqlparse | 3.12.1 / 2.13.0 / 0.6.0 |

사내 패키지 `backend-pjt/packages/retirement_simulation` 은 멀티스테이지 빌드의 1단계에서 wheel 로 만들어 런타임 이미지에 설치한다.

### 2-4. AI 주요 라이브러리 (`ai-pjt/requirements.lock`, 해시 고정)

| 패키지 | 버전 | 패키지 | 버전 |
|---|---|---|---|
| fastapi | 0.115.14 | uvicorn[standard] | 0.52.4 |
| pydantic | 2.13.5 | python-dotenv | 1.2.3 |
| langgraph | 0.6.11 | langgraph-checkpoint-postgres | 2.0.25 |
| psycopg[binary] | 3.3.5 | langchain-core | 0.3.86 |
| langchain-google-genai | 2.1.12 | langchain-openai | 0.3.35 |
| llama-index-core | 0.14.24 | llama-index-embeddings-google-genai | 0.6.0 |
| llama-index-retrievers-bm25 | 0.8.0 | langfuse | 4.15.1 |
| google-genai | 2.22.0 | openai | 2.54.0 |

### 2-5. Frontend 주요 라이브러리 (`front-pjt/package-lock.json`)

| 패키지 | 버전 | 패키지 | 버전 |
|---|---|---|---|
| vue | 3.5.35 | vue-router | 5.1.0 |
| pinia | 3.0.4 | axios | 1.17.0 |
| echarts | 6.1.0 | vue-echarts | 8.0.1 |
| vite | 8.0.16 | @vitejs/plugin-vue | 6.0.7 |
| vite-plugin-vue-devtools | 8.1.2 | eslint / oxlint | 10.4.1 / 1.60.0 |
| prettier | 3.8.3 | @playwright/test (E2E) | 1.63.0 |

### 2-6. AI 모델 (외부 서비스 경유, 상세는 [02-external-services.md](02-external-services.md))

| 용도 | 모델 | 설정 |
|---|---|---|
| LLM | `gemini-2.5-flash-lite` (기본값) | `LLM_PROVIDER=gemini`, `LLM_MODEL`, `LLM_TEMPERATURE=0` |
| 임베딩 | `gemini-embedding-2`, 3072차원 | Compose 고정 |

---

## 3. 환경 변수

### 3-1. 파일 구조

| 파일 | 용도 | Git 추적 |
|---|---|---|
| 루트 `.env` | Compose 치환값. **실제 비밀값 포함** | 추적 안 함 (`.gitignore`) |
| 루트 `.env.example` | 루트 `.env` 의 서식. `# env-contract: required / optional` 섹션으로 CI 가 계약 검사 | 추적 |
| `backend-pjt/.env.example` | Backend 가 인식하는 전체 변수 명세 (컨테이너 밖 직접 실행 시에만 사용) | 추적 |
| `ai-pjt/.env.example` | AI 가 인식하는 전체 변수 명세 (직접 실행 시에만 사용) | 추적 |
| `front-pjt/.env` / `.env.example` | Vite dev 서버 전용 | `.env` 추적 안 함 |
| 서버 `/srv/s15p21b109/secrets/app.env` | 운영 루트 env (`root:gitlab-runner 640`) | 저장소 밖 |

### 3-2. 루트 `.env` — 필수 변수 (빈 값이면 `docker compose config` 단계에서 실패)

| 변수 | 용도 | 생성 규칙 |
|---|---|---|
| `DJANGO_SECRET_KEY` | Django 서명 키 (JWT 서명 포함) | `openssl rand -hex 32` |
| `POSTGRES_PASSWORD` | `retirewise` DB 계정 비밀번호 | `openssl rand -hex 24`. **볼륨 최초 생성 시 DB 에 고정됨** |
| `AI_SERVICE_BEARER_TOKEN` | Backend → AI 서비스 인증 토큰 (양쪽 동일 값) | `openssl rand -hex 32` |
| `AI_MASKING_PSEUDONYM_KEY` | AI 로그·트레이스 가명화 HMAC 키 | `openssl rand -hex 32`. 32바이트 이상, `AI_SERVICE_BEARER_TOKEN` 과 달라야 함 |
| `DATABASE_URL` | AI 서비스·rag-bootstrap 용 PostgreSQL DSN | `postgresql://retirewise:<POSTGRES_PASSWORD>@postgres:5432/retirewise_db` |
| `GOOGLE_API_KEY` | **SSAFY GMS 키** (Gemini LLM·임베딩) | 사람이 직접 입력. 개인 Google 키 아님 |

### 3-3. 루트 `.env` — 선택 변수 (Compose 에 기본값 있음)

| 변수 | 기본값 | 용도 |
|---|---|---|
| `DJANGO_ALLOWED_HOSTS` | `localhost,127.0.0.1,backend` | Django 허용 Host. **운영은 `j15b109.p.ssafy.io` 를 반드시 추가** |
| `POSTGRES_DB` / `POSTGRES_USER` | `retirewise_db` / `retirewise` | 바꾸면 `DATABASE_URL` 도 함께 변경 |
| `AI_SERVICE_TIMEOUT_SECONDS` | `5` | Backend → AI 연결 타임아웃 |
| `AI_STREAM_READ_TIMEOUT_SECONDS` | `120` | Backend → AI SSE 읽기 타임아웃 |
| `READINESS_TIMEOUT_SECONDS` | `2` | readiness 검사 타임아웃 (0 초과 10 이하) |
| `AI_CONTEXT_EXECUTION_TTL_SECONDS` | `600` | 요청별 임시 체크포인트 보존 |
| `AI_CONTEXT_RECEIPT_TTL_SECONDS` | `86400` | 완료 영수증 보존 |
| `AI_CONTEXT_CLOSED_RETENTION_SECONDS` | `86400` | 종료된 대화 컨텍스트 보존 |
| `AI_CONTEXT_CLEANUP_INTERVAL_SECONDS` | `300` | 정리 루프 주기 |
| `AI_CONTEXT_MEMORY_INTERVAL_SECONDS` | `5` | 대화 요약 워커 주기 |
| `AI_CONTEXT_MEMORY_TOP_K` | `5` | 대화 기억 검색 개수 |
| `AI_CONTEXT_EMBEDDING_BATCH_SIZE` | `8` | 대화 기억 임베딩 배치 |
| `AI_PLAN_VALIDATION_ENABLED` | `false` | AI plan 구조·단계 허용목록 검증. 현재 임시로 꺼 둠. `true` 로 바꾸고 AI 컨테이너를 재생성하면 복구 |
| `LLM_PROVIDER` | `gemini` | `gemini` / `openai` / `opensource` |
| `LLM_MODEL` | `gemini-2.5-flash-lite` | LLM 모델명 |
| `LLM_TEMPERATURE` | `0` | 0~1 |
| `OPENAI_API_KEY` | (빈 값) | `LLM_PROVIDER=openai` 일 때만 |
| `LLM_BASE_URL` | `https://gms.ssafy.io/gmsapi/api.openai.com/v1` | OpenAI 호환 엔드포인트 |
| `RAG_TOP_K` / `RAG_FETCH_K` | `5` / `10` | 최종 반환 수 / 후보 수 (`FETCH_K ≥ TOP_K`) |
| `RAG_MIN_DENSE_SCORE` | `0.54` | dense 검색 최소 유사도 |
| `RAG_INDEX_RETENTION_DAYS` | `30` | retired 인덱스 보관 기간 |
| `AI_COUNSELING_RAG_ENABLED` | `false` | 상담 후보 RAG. **운영 override 에서 `true` 고정** |
| `AI_COUNSELING_RAG_TIMEOUT_SECONDS` | `12` | 상담 보충 전체 한도 (30 이하) |
| `API_GATEWAY_PORT` | `8080` (예제 `127.0.0.1:8081`) | gateway 호스트 바인딩 |
| `WEB_HOST_PORT` | `8080` | 운영 override 의 web 포트 (`127.0.0.1` 에 바인딩) |
| `LANGFUSE_ENABLED` | `false` | 마스킹 관측 export. `true` 면 아래 3개 필수 |
| `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` / `LANGFUSE_BASE_URL` | (빈 값) | Langfuse 연결값 |
| `LANGFUSE_TRACING_ENVIRONMENT` | `default` | 환경 구분 |
| `APP_RELEASE` | `local` | AI 이미지 빌드 arg. `deploy.sh` 가 Git SHA 로 덮어씀 |

### 3-4. Compose 에 고정된 값 (env 로 바꿀 수 없음)

| 서비스 | 변수 | 값 |
|---|---|---|
| backend·migrate | `DJANGO_DEBUG` | `false` |
| backend·migrate | `POSTGRES_HOST` / `POSTGRES_PORT` | `postgres` / `5432` |
| backend·migrate | `REDIS_URL` | `redis://redis:6379/0` |
| backend·migrate | `AI_SERVICE_URL` | `http://ai-pjt:8001` |
| backend·migrate | `READINESS_HOST` / `READINESS_PORT` | `127.0.0.1` / `8000` |
| ai-pjt·rag-bootstrap | `EMBEDDING_PROVIDER` / `EMBEDDING_MODEL` / `EMBED_DIM` | `gemini` / `gemini-embedding-2` / `3072` |
| ai-pjt·rag-bootstrap | `GOOGLE_GEMINI_BASE_URL` | `https://gms.ssafy.io/gmsapi/generativelanguage.googleapis.com` |
| ai-pjt | `RAG_FINANCIAL_COLLECTION` / `RAG_INTERFACE_COLLECTION` / `RAG_COUNSELING_COLLECTION` | `financial-knowledge` / `interface-guides` / `counseling-candidate` |
| ai-pjt | `RAG_COUNSELING_MIN_DENSE_SCORE` | `0.54` |
| ai-pjt | `READINESS_HTTP_URL` | `http://127.0.0.1:8001/api/system/v1/health` |
| ai-pjt | `LANGCHAIN_TRACING_V2` | `false` (raw tracing 차단) |

### 3-5. 프론트 `front-pjt/.env` (Vite dev 서버 전용)

```dotenv
VITE_API_BASE_URL=/api
VITE_API_PROXY_TARGET=http://localhost:8081
```

- `VITE_API_PROXY_TARGET` 이 있어야 Vite 가 `/api` 요청을 gateway 로 프록시한다. 없으면 `/api/...` 가 5173 으로 가서 404 가 난다. `cp front-pjt/.env.example front-pjt/.env` 로 만든다.
- 프론트 클라이언트는 `VITE_API_BASE_URL` 뒤에 `/{domain}/v{version}` 을 붙인다 (예: `/api/auth/v1/login/`).

### 3-6. 빌드 시 사용되는 변수

| 변수 | 위치 | 값 | 설명 |
|---|---|---|---|
| `VITE_API_BASE_URL` | `front-pjt/Dockerfile` `ARG` | `/api` | Vite 가 번들에 박는다. **Compose 는 넘기지 않으며 ARG 기본값이 유일한 출처**다 |
| `APP_RELEASE` | `ai-pjt/Dockerfile` `ARG` | `local` 또는 Git SHA | `LANGFUSE_RELEASE` 와 OCI 라벨 `org.opencontainers.image.revision` 에 기록 |
| `DOCKER_BUILDKIT` | CI 변수 | `1` | BuildKit 사용 |
| pip 해시 검증 | 각 Dockerfile | `--require-hashes` | lock 파일의 해시와 다르면 빌드 실패 |

### 3-7. CI 전용 더미 변수 (`.gitlab-ci.yml`, 운영 값 아님)

`CI_DJANGO_SECRET_KEY`, `CI_POSTGRES_PASSWORD`, `CI_AI_SERVICE_BEARER_TOKEN`, `CI_AI_MASKING_PSEUDONYM_KEY`, `CI_GOOGLE_API_KEY`, `CI_DATABASE_URL`. 테스트 컨테이너에 `-e` 로 주입한다. 실제 비밀값은 CI Variables 에 두지 않는다.

### 3-8. 생성 방법

```bash
bash deploy/make-env.sh                 # 로컬: 루트 .env 생성 (권한 600)
GOOGLE_API_KEY=<GMS키> bash deploy/make-env.sh   # 키를 함께 넣어 생성 (셸 히스토리에 남음)
sudo bash deploy/make-env.sh --output /srv/s15p21b109/secrets/app.env   # 서버
```

- `.env.example` 을 복사하고 네 비밀값은 `openssl rand -hex`, `DATABASE_URL` 은 조립해서 채운다.
- `GOOGLE_API_KEY` 는 비어 있으면 편집기로 직접 넣는다.
- 파일이 이미 있으면 **덮어쓰지 않고 exit 2** 로 끝난다. `POSTGRES_PASSWORD` 가 볼륨과 짝이기 때문이다.

### 3-9. 거부되는 값

| 서비스 | 거부 값 | 추가 검증 |
|---|---|---|
| backend (`config/runtime_env.py`) | `change-me`, `changeme`, `password`, `postgres`, `secret`, `ssafy` | bool·숫자·host 목록 파싱 실패 시 기동 거부 |
| ai (`core/config.py`) | `change-me`, `changeme`, `password`, `postgres`, `secret` | 가명화 키 32바이트 이상·토큰과 상이, `RAG_FETCH_K ≥ RAG_TOP_K`, `AI_COUNSELING_RAG_TIMEOUT_SECONDS ≤ 30`, RAG collection 이름 고정 |

---

## 4. 로컬 빌드·실행 절차

### 4-1. 사전 준비

| 항목 | 버전 | 비고 |
|---|---|---|
| Docker Desktop (Windows) 또는 Docker Engine | Docker 29.x, Compose v5.x | Windows 는 WSL2 백엔드 |
| Git | 2.x | Windows 는 Git Bash 필요 (`deploy/*.sh` 실행) |
| Node.js / npm | 22.12 이상 (또는 20.19 이상) | 프론트 dev 서버용 |
| openssl | — | `make-env.sh` 가 사용 (Git Bash 에 포함) |
| SSAFY GMS 키 | — | [02-external-services.md](02-external-services.md) 참고 |

### 4-2. 클론과 환경 파일 생성

```bash
git clone https://lab.ssafy.com/s15-fintech-finance-sub1/S15P21B109.git
cd S15P21B109
bash deploy/make-env.sh
# 편집기로 .env 를 열어 GOOGLE_API_KEY= 에 GMS 키 입력
cp front-pjt/.env.example front-pjt/.env
docker compose config --quiet && echo OK     # 필수 변수 누락 검사
```

### 4-3. 전체 스택 빌드·기동

```bash
docker compose up --build -d
```

첫 기동 시 일어나는 일은 다음과 같다.

1. backend·ai 이미지를 빌드하고 외부 이미지(`pgvector/pgvector:pg16`, `redis:7.4-bookworm`, `nginx:1.29.1-alpine`)를 받는다. `migrate` 는 backend 이미지를, `rag-bootstrap` 은 ai 이미지를 공유한다.
2. 네트워크 `s15p21b109_application`, 볼륨 `s15p21b109_postgres_data`, `s15p21b109_redis_data` 를 만든다.
3. postgres 가 처음 뜰 때 `POSTGRES_DB/USER/PASSWORD` 로 DB 와 계정을 만들고 `db/postgres/init.sql` 을 1회 실행한다 (pgvector 확장, `rag_*` 테이블).
4. `migrate` 가 Django 마이그레이션을 적용하고 `Exited (0)` 로 끝난다.
5. `rag-bootstrap` 이 이미지 안의 `ai-pjt/rag/artifacts/official__*.tar.gz` 를 검증한 뒤 최고 버전(현재 2.2.0)을 import 하고 두 인덱스 포인터를 활성화한다. 로컬 기본값(`AI_COUNSELING_RAG_ENABLED=false`)에서는 외부 호출이 없다.
6. `backend` 가 기동하고 약 20~30초 뒤 `healthy` 가 된다. `gateway` 가 이어서 뜬다.
7. `ai-pjt` 가 기동하면서 LangGraph 체크포인트 테이블과 `ai_*` 컨텍스트 테이블을 만든다.

### 4-4. 상태 확인

```bash
docker compose ps                              # backend, ai-pjt 가 healthy
docker compose ps -a migrate rag-bootstrap     # 둘 다 Exited (0)
docker compose logs --no-log-prefix rag-bootstrap   # "catalog_version": "2.2.0"
docker compose exec backend python readiness.py     # ready
docker compose exec ai-pjt python readiness.py      # ready
curl -i http://localhost:8081/api/consulting/v1/consultations/   # 401 이면 정상 (인증 필요 경로)
```

### 4-5. 시연 데이터 생성

```bash
docker compose exec backend python manage.py bootstrap_demo
```

- PB 계정 `admin1234@naver.com` / `admin1234`, 시드 고객 40명 `demo001~040@retirewise.demo` / `demo-pass-2026!`, 커뮤니티 글 10건·댓글 5건을 만든다.
- 데모가 아닌 회원이 이미 있으면 중단된다. 로컬 DB 가 맞으면 `--yes` 를 붙인다.
- **운영 서버에서는 사용하지 않는다.** 운영 PB 계정은 7장의 `create_pb` 로 만든다.

### 4-6. 프론트엔드 실행

```bash
cd front-pjt
npm ci
npm run dev          # http://localhost:5173
```

- 브라우저는 `localhost` 로 접속한다. CORS 허용 origin 이 `http://localhost:5173`, `http://localhost:3000` 으로 하드코딩돼 있다 (Vite 프록시 사용 시에는 same-origin 이라 무관).
- dev 모드에서는 라우터의 로그인 가드가 꺼진다. 역할 가드(`/pb`)는 항상 동작한다.

### 4-7. 운영과 동일한 경로로 로컬 재현

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
# http://localhost:8080  (web 컨테이너가 SPA 를 서빙하고 /api 를 gateway 로 위임)
```

운영 override 는 `AI_COUNSELING_RAG_ENABLED=true` 로 고정돼 있어 `rag-bootstrap` 이 GMS 임베딩을 실제로 호출한다. 유효한 `GOOGLE_API_KEY` 가 필요하다.

### 4-8. 개별 이미지 빌드

```bash
# backend / migrate (빌드 컨텍스트 = 저장소 루트)
docker build -f backend-pjt/Dockerfile -t s15p21b109-backend:latest .
# ai-pjt / rag-bootstrap (빌드 컨텍스트 = 저장소 루트)
docker build --build-arg APP_RELEASE="$(git rev-parse HEAD)" -f ai-pjt/Dockerfile -t s15p21b109-ai-pjt:latest .
# web (빌드 컨텍스트 = front-pjt, VITE_API_BASE_URL 은 넘기지 않는다)
docker build -t s15p21b109-web:latest front-pjt
# 프론트 정적 산출물만 (front-pjt/dist)
cd front-pjt && npm ci --no-audit --no-fund && npm run build
```

- backend·ai 는 `contract-fixtures/` 를 복사하므로 컨텍스트가 **저장소 루트**여야 한다. `backend-pjt/README.md` 의 `docker build ... backend-pjt` 는 옛 명령이다.
- 루트 컨텍스트이므로 루트 `.dockerignore` 만 적용된다. 테스트 디렉터리와 `front-pjt` 는 제외된다.

### 4-9. 재기동·코드 반영·로그·종료

```bash
docker compose up --build -d                     # 코드 변경 반영 (이미지 재빌드 후 교체)
docker compose build backend && docker compose up -d --no-deps backend   # 한 서비스만
docker compose up -d --force-recreate --no-build migrate backend         # backend 재생성 시 migrate 동반
docker compose up -d --force-recreate --no-build rag-bootstrap ai-pjt    # ai 재생성 시 rag-bootstrap 동반
docker compose logs -f backend                   # 실시간 로그
docker compose logs --tail 100 ai-pjt
docker compose down                              # 종료 (볼륨 유지)
docker compose down -v                           # 로컬 한정: 볼륨까지 삭제 → 다음 up 에서 DB 초기화
```

로컬 전용 `docker-compose.override.yml` (Git 미추적)을 두면 `./backend-pjt:/app` 바인드 마운트와 `uvicorn --reload` 로 재빌드 없이 코드가 반영된다. 이때 `./contract-fixtures:/app/contract-fixtures:ro` 마운트도 함께 있어야 migrate 가 `policy is missing` 으로 실패하지 않는다.

### 4-10. 테스트 실행

| CI 잡 | 내용 | 로컬 명령 |
|---|---|---|
| `test:env-contract` | `.env.example`·Compose·코드가 읽는 변수 목록 일치 검사 | `make env-contract-check` |
| `test:foundation` (allow_failure) | 산출물 정합성 게이트 `g1_gate.py` | `make g1-foundation` (**Windows 불가**) |
| `test:backend` | 시뮬레이션 패키지·Backend·계약 테스트 | `make g1-backend` |
| `test:ai` | `rag.bootstrap --check-only` + AI·guardrails·rag·tools·evals·security | `make g1-ai g1-rag g1-tools g1-observability g1-security` |
| `test:front` | `npm test` + P07 E2E 준비 테스트 | `cd front-pjt && npm test` |
| `test:f14-regression` | 격리 Compose 에서 Django 전체 suite | `bash deploy/f14-regression.sh` |

Windows 에는 `python3` 명령이 없으므로 `make PYTHON=python <target>` 으로 실행한다. 브라우저 E2E 는 `bash front-pjt/tests/e2e/f05/run.sh --smoke` 로 실행한다 (Docker, Git Bash 필요).

---

## 5. 운영 서버 배포 절차

### 5-1. 서버 정보

| 항목 | 값 |
|---|---|
| 도메인 | `j15b109.p.ssafy.io` (https://j15b109.p.ssafy.io) |
| 접속 | `ssh -i J15B109T.pem ubuntu@j15b109.p.ssafy.io` |
| 사양 | Ubuntu 24.04.4, 4 vCPU, 15 GiB, 309 GB |
| 열린 포트 (ufw) | 22, 80, 443 |
| 비밀값 | `/srv/s15p21b109/secrets/app.env` (`root:gitlab-runner 640`, 디렉터리 750) |
| 배포 상태 기록 | `/srv/s15p21b109/state/last_good_images` |
| 러너 체크아웃 | `/home/gitlab-runner/builds/<id>/0/s15-fintech-finance-sub1/S15P21B109` |

### 5-2. 서버 초기 세팅 순서

1. **Docker 설치.** 공식 apt 저장소에서 docker-ce 와 compose plugin 을 설치한다.
   ```bash
   sudo usermod -aG docker ubuntu
   sudo usermod -aG docker gitlab-runner
   ```
2. **GitLab Runner 설치·등록.** 공식 apt 저장소에서 설치하고 프로젝트 러너로 등록한다. executor 는 `shell`, 태그는 `ssafy-ec2`, untagged 잡은 받지 않는다.
   ```bash
   sudo gitlab-runner verify
   systemctl status gitlab-runner
   ```
3. **비밀값 파일 생성.** 저장소를 한 번 받은 위치에서 실행한다.
   ```bash
   sudo mkdir -p /srv/s15p21b109/secrets /srv/s15p21b109/state
   sudo bash deploy/make-env.sh --output /srv/s15p21b109/secrets/app.env
   sudo chown root:gitlab-runner /srv/s15p21b109/secrets /srv/s15p21b109/secrets/app.env
   sudo chmod 750 /srv/s15p21b109/secrets && sudo chmod 640 /srv/s15p21b109/secrets/app.env
   sudo chown gitlab-runner /srv/s15p21b109/state
   sudoedit /srv/s15p21b109/secrets/app.env
   ```
   편집기에서 다음 값을 채운다. 값은 `printf`·`sed -i` 로 넣지 않는다 (셸 히스토리에 남음).
   ```dotenv
   GOOGLE_API_KEY=<GMS 키>
   DJANGO_ALLOWED_HOSTS=j15b109.p.ssafy.io,localhost,127.0.0.1,backend
   API_GATEWAY_PORT=127.0.0.1:8081
   WEB_HOST_PORT=8080
   ```
   ```bash
   sudo docker compose --env-file /srv/s15p21b109/secrets/app.env -f docker-compose.yml -f docker-compose.prod.yml config --quiet && echo OK
   ```
4. **호스트 nginx.**
   ```bash
   sudo apt install nginx
   sudo cp deploy/nginx-host.conf /etc/nginx/sites-available/s15p21b109
   sudo ln -s /etc/nginx/sites-available/s15p21b109 /etc/nginx/sites-enabled/
   sudo rm /etc/nginx/sites-enabled/default
   sudo nginx -t && sudo systemctl reload nginx
   ```
5. **방화벽.** SSH 세션을 2개 열어 둔 상태에서 진행한다.
   ```bash
   sudo ufw allow 80/tcp && sudo ufw allow 443/tcp
   ```
6. **HTTPS 인증서.**
   ```bash
   sudo certbot --nginx -d j15b109.p.ssafy.io --register-unsafely-without-email --agree-tos --redirect
   sudo certbot renew --dry-run
   ```
7. **첫 배포.** master 파이프라인을 실행하거나 러너 체크아웃에서 `bash deploy/deploy.sh` 를 실행한다.
8. **운영 PB 계정.**
   ```bash
   sudo docker compose --project-name s15p21b109 --env-file /srv/s15p21b109/secrets/app.env \
     -f docker-compose.yml -f docker-compose.prod.yml \
     exec backend python manage.py create_pb pb@example.com --name "김PB" --password "<강한 비밀번호>"
   ```
9. **GitLab 설정.** `master` 보호 브랜치(Maintainer 만 push·merge, force push 금지), "파이프라인 성공 시에만 머지", "skipped 파이프라인 머지 허용", Mattermost 알림을 켠다.

### 5-3. CI/CD 파이프라인 (`.gitlab-ci.yml`)

```text
push / MR ─→ test 스테이지: env-contract · foundation(allow_failure) · backend · ai · front · f14-regression
master 머지 ─→ test 통과 ─→ deploy:production (자동, resource_group=production, AI_REQUIRED=1)
                                └─ bash deploy/deploy.sh
```

- 러너가 곧 배포 서버라서 SSH·이미지 레지스트리 없이 체크아웃 디렉터리에서 바로 Compose 를 올린다.
- MR 이 열린 브랜치는 MR 파이프라인만 돈다. `deploy:production` 은 MR 파이프라인에 생성되지 않는다.

### 5-4. `deploy/deploy.sh` 동작

| 단계 | 내용 |
|---|---|
| 1. 사전 점검 | env 파일 읽기, `compose config --quiet`. 실패하면 값이 빈 필수 변수 이름을 출력하고 중단 (컨테이너 변경 없음) |
| 2. 롤백 대상 확인 | `s15p21b109-<서비스>:last-good` 태그 확인 (web·backend·ai-pjt·migrate·rag-bootstrap). 없으면 현재 기동 중 이미지를 승격 |
| 3. 빌드·기동 | `APP_RELEASE=<Git SHA>` → `build --pull` → `up -d --remove-orphans` → `restart gateway` |
| 4. 헬스체크 (최대 150초, 5초 간격) | rag-bootstrap `exited:0` + web·backend `healthy` + `GET http://127.0.0.1:8080/` 200 + `GET http://127.0.0.1:8080/api/consulting/v1/consultations/` 가 5xx 아님 (401 정상) + (`AI_REQUIRED=1` 이면) ai-pjt `healthy` |
| 5-성공 | 현재 이미지에 `:last-good` 을 옮겨 붙이고 `docker image prune -f` |
| 5-실패 | ai-pjt 상태·healthcheck 기록·로그 100줄과 backend 로그 20줄을 남긴 뒤 `:last-good` → `:latest` 재태그, `up -d --no-build`, exit 1 |

실행 명령 형태:

```bash
bash deploy/deploy.sh                    # 기본: app.env 사용, 빌드 포함
bash deploy/deploy.sh --no-build         # 재빌드 없이 재기동
AI_REQUIRED=0 bash deploy/deploy.sh      # AI 가 깨진 상태에서 서빙만 급히 배포
ENV_FILE=.env bash deploy/deploy.sh      # 로컬 재현
```

스크립트 기본값은 `AI_REQUIRED=0` 이고 CI 잡만 `1` 로 실행한다.

### 5-5. 수동 재배포·특정 서비스 재기동

```bash
# env 만 바뀐 경우: GitLab 에서 master 최신 파이프라인의 deploy:production 을 Retry
# 또는 서버에서 (러너 체크아웃 디렉터리)
sudo bash -c 'cd /home/gitlab-runner/builds/<id>/0/s15-fintech-finance-sub1/S15P21B109 && \
  docker compose --project-name s15p21b109 --env-file /srv/s15p21b109/secrets/app.env \
  -f docker-compose.yml -f docker-compose.prod.yml \
  up -d --force-recreate --no-build rag-bootstrap ai-pjt'
```

### 5-6. 수동 롤백

```bash
for s in web backend ai-pjt migrate rag-bootstrap; do
  docker tag s15p21b109-$s:last-good s15p21b109-$s:latest
done
sudo AI_REQUIRED=1 bash deploy/deploy.sh --no-build
```

이미지 롤백만 한다. DB 스키마는 되돌리지 않는다 (마이그레이션은 전진만).

---

## 6. 배포 시 특이사항

1. **`POSTGRES_PASSWORD` 는 볼륨과 짝이다.** 볼륨이 처음 만들어질 때 DB 에 고정된다. env 만 바꾸면 접속이 끊긴다. 바꾸려면 DB 에서 `ALTER USER retirewise PASSWORD '...'` 를 먼저 실행하고 파일을 고친 뒤 재기동한다.
2. **서버에서 `docker compose down -v` 를 쓰지 않는다.** 회원·상담·리포트·커뮤니티 데이터가 모두 지워진다. `deploy.sh` 도 볼륨을 지우지 않는다.
3. **`make-env.sh` 는 기존 파일을 덮어쓰지 않는다.** 서버 `app.env` 는 한 번 만든 뒤 사람이 관리한다.
4. **필수 변수(`${VAR:?}`)를 새로 추가하는 MR 은 머지 전에 서버 `app.env` 에 줄을 추가해야 한다.** 그렇지 않으면 다음 배포가 사전 점검에서 멈춘다. MR 체크리스트는 `.env.example`, 서비스 `.env.example`, `make-env.sh`, `.gitlab-ci.yml` 더미, `deploy/README.md` 표 갱신과 `make env-contract-check` 통과다.
5. **`app.env` 를 편집하기 전에 immutable snapshot 을 만든다.** `sudo bash deploy/f14-config-snapshot.sh create ...` 로 root 전용 0500/0400 + `chattr +i` 사본을 남긴다. 복구도 이 스크립트의 `restore` 로만 한다.
6. **`GOOGLE_API_KEY` 는 GMS 키다.** Google 로 직접 보내면 `API key not valid` 로 거부된다. 그래서 `GOOGLE_GEMINI_BASE_URL` 을 GMS 주소로 Compose 에 고정했다.
7. **`langchain-google-genai` 2.x 는 base URL 환경변수를 스스로 읽지 않는다.** `ai-pjt/core/llm.py` 가 `transport="rest"` 와 `client_options.api_endpoint` 를 넘겨 GMS 로 보낸다. 라이브러리를 3.x 이상으로 올리면 `langchain-core` 1.x 충돌이 난다.
8. **헬스체크는 LLM 정상을 증명하지 않는다.** 키가 틀려도 ai-pjt 는 `healthy` 가 된다. 운영에서는 9장의 임베딩 실호출(`3072` 출력)로 확인한다.
9. **운영 `rag-bootstrap` 은 GMS 를 실제로 호출한다.** 운영 override 가 `AI_COUNSELING_RAG_ENABLED=true` 로 고정돼 있어 상담 후보 37개 카드를 Gemini 로 색인한다. 키가 무효면 `rag-bootstrap` 이 실패하고 ai-pjt 가 뜨지 않아 배포가 롤백된다.
10. **ai-pjt 를 재생성할 때는 `rag-bootstrap` 을, backend·web 을 재생성할 때는 `migrate` 를 함께 지정한다.** 의존 one-shot 컨테이너의 이미지가 사라진 상태면 `No such image` 로 전체 명령이 실패한다.
11. **Compose 프로젝트명을 `s15p21b109` 로 고정한다.** 수동 명령에서 `--project-name s15p21b109` 를 빼고 다른 디렉터리에서 실행하면 별개 스택을 만들려 해 `127.0.0.1:8080` 포트 충돌이 난다. 롤백 태그 이름(`s15p21b109-<서비스>`)도 이 이름에 의존한다.
12. **운영 `DJANGO_ALLOWED_HOSTS` 에 도메인을 넣는다.** nginx 가 `Host` 헤더를 그대로 넘기므로 도메인이 없으면 Django 가 400 을 낸다. `deploy.sh` 헬스체크는 `127.0.0.1` 로 호출해 이 누락을 잡지 못한다.
13. **프론트 API 주소는 빌드 시점에 고정된다.** `front-pjt/Dockerfile` 의 `ARG VITE_API_BASE_URL=/api` 가 유일한 출처이며 Compose 로 넘기지 않는다. env 로 받던 시절 옛 값이 덮어써 전 API 가 404 가 된 사고가 있었다.
14. **web nginx 는 `index.html` 을 `no-cache` 로 내보낸다.** 이 헤더가 없으면 배포 후에도 옛 화면이 보이거나 사라진 해시 파일을 404 로 받는다.
15. **gateway 의 upstream 은 `resolve` 방식이다.** backend 가 재생성돼 IP 가 바뀌어도 10초 안에 따라간다. `deploy.sh` 는 즉시 반영을 위해 배포 후 gateway 를 재시작한다.
16. **gateway 의 SSE 전용 location 정규식이 현재 경로와 맞지 않는다.** `deploy/nginx/default.conf` 는 `^/api/v(1|2)/consultations/...` 인데 실제 경로는 `/api/consulting/v2/consultations/<id>/messages/stream/` 이다. 현재 SSE 는 일반 `/api/` 블록을 탄다. Backend 가 `X-Accel-Buffering: no` 를 보내 버퍼링은 꺼지지만 무응답 60초 초과 시 끊길 수 있다. 정규식을 `^/api/consulting/v2/consultations/[0-9]+/messages/stream/$` 로 고치는 후속 작업이 필요하다.
17. **호스트에 DB·캐시 포트를 열지 않는다.** Compose 는 `expose` 만 쓴다. 호스트에 바인딩되는 것은 `127.0.0.1:8080`(web)과 `127.0.0.1:8081`(gateway)뿐이다.
18. **SSAFY 서버 금지 사항.** ufw 에서 22 를 막거나 `authorized_keys` 를 지우거나 `/home`·시스템 디렉터리 권한을 바꾸면 초기화 외 복구가 불가하다.
19. **shell 러너는 같은 체크아웃을 재사용한다.** 컨테이너가 root 로 파일을 만들면 다음 잡의 `git clean` 이 실패한다. 복구는 `sudo chown -R gitlab-runner:gitlab-runner /home/gitlab-runner/builds` 다.
20. **인증서 만료일은 2026-12-06 이다.** 이메일을 등록하지 않아 만료 경고 메일이 없다. `certbot.timer` 가 하루 2회 자동 갱신하며 `sudo certbot certificates` 로 점검한다.
21. **Windows 체크아웃 주의.** `*.sh` 와 `ai-pjt/rag/data/**/*.md` 는 `.gitattributes` 로 LF 가 강제된다. CRLF 가 되면 서버 스크립트가 `\r: command not found` 로, RAG 해시 검증이 불일치로 실패한다. `g1_gate.py` 는 Windows 경로 구분자 때문에 실행되지 않는다.
22. **`AI_PLAN_VALIDATION_ENABLED` 는 현재 `false` 가 기본값이다.** AI plan 검증을 임시로 끈 상태이며 `true` 로 바꾸고 AI 컨테이너를 재생성하면 복구된다.

---

## 7. DB 접속 정보 및 주요 계정

### 7-1. PostgreSQL

| 항목 | 값 |
|---|---|
| 엔진 | PostgreSQL 16.15 + pgvector 0.8.6 (`pgvector/pgvector:pg16`) |
| DB 이름 | `retirewise_db` (`POSTGRES_DB`) |
| 계정 | `retirewise` (`POSTGRES_USER`), DB 소유자. Django·AI·rag-bootstrap 이 모두 이 계정 사용 |
| 비밀번호 | `POSTGRES_PASSWORD` (루트 `.env` / 서버 `app.env`) |
| 호스트:포트 | `postgres:5432` — Compose 내부망 전용, 호스트 미공개 |
| Django 접속 | `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_HOST`, `POSTGRES_PORT` (+ E2E 전용 `POSTGRES_SCHEMA`) |
| AI 접속 | `DATABASE_URL=postgresql://retirewise:<비밀번호>@postgres:5432/retirewise_db` |
| 볼륨 | `s15p21b109_postgres_data` → `/var/lib/postgresql/data` |
| 초기화 | `db/postgres/init.sql` (볼륨 최초 생성 시 1회) |

DB 콘솔 접속:

```bash
docker compose exec postgres psql -U retirewise -d retirewise_db
```

### 7-2. Redis

| 항목 | 값 |
|---|---|
| 호스트:포트 | `redis:6379` (내부망 전용, 인증 없음) |
| Backend 접속 | `REDIS_URL=redis://redis:6379/0` (Compose 고정) |
| 용도 | AI 요청 상태(TTL 600초·86400초), `Idempotency-Key` 재사용, 토큰 금고, 시뮬레이션 중복 실행 잠금(60초) |

### 7-3. 애플리케이션 계정

| 계정 | 비밀번호 | 역할 | 생성 방법 |
|---|---|---|---|
| `admin1234@naver.com` | `admin1234` | PB (is_staff), 로그인 시 `/pb` 이동 | `bootstrap_demo` (로컬 시연 전용) |
| `demo001@retirewise.demo` ~ `demo040@retirewise.demo` | `demo-pass-2026!` | 시드 고객 | `bootstrap_demo` |
| 운영 PB | 운영자가 지정 | PB | `python manage.py create_pb <email> --name <이름> --password <비밀번호>` (또는 `PB_PASSWORD`) |
| Django 슈퍼유저 | 운영자가 지정 | Django admin | `python manage.py createsuperuser` |

PB 계정은 회원가입 화면으로 만들 수 없다. 운영 라우팅에는 Django `/admin/` 경로가 없으며 관리자 화면은 `/pb` 다.

### 7-4. 인증·서비스 간 토큰

| 항목 | 값 |
|---|---|
| 사용자 인증 | simplejwt `Bearer`. access 1시간, refresh 1일(라이브러리 기본값). 로그아웃 시 refresh 블랙리스트 등록 |
| Backend → AI | `Authorization: Bearer <AI_SERVICE_BEARER_TOKEN>` |
| JWT 서명 키 | `DJANGO_SECRET_KEY` (바꾸면 전원 재로그인) |

---

## 8. 프로퍼티·설정 파일 목록

| 경로 | 역할 | Git 추적 |
|---|---|---|
| `.env` | 루트 Compose 치환값 (비밀값) | 추적 안 함 |
| `.env.example` | 루트 env 서식, CI 계약 검사 대상 | 추적 |
| `backend-pjt/.env.example` | Backend 전체 변수 명세 | 추적 |
| `ai-pjt/.env.example` | AI 전체 변수 명세 | 추적 |
| `front-pjt/.env.example` | Vite dev 변수 | 추적 |
| `ai-pjt/rag/ops.env.example` | RAG 생산자(artifact 제작) 변수 | 추적 |
| `backend-pjt/config/settings.py` | Django 설정 (앱, DATABASES, JWT, CORS, 시간대) | 추적 |
| `backend-pjt/config/runtime_env.py` | Backend env 엄격 파서, placeholder 거부 | 추적 |
| `ai-pjt/core/config.py` | AI 설정·검증 (`validate_runtime`) | 추적 |
| `ai-pjt/core/llm.py` | LLM·임베딩 클라이언트 생성 (GMS 경유 설정) | 추적 |
| `docker-compose.yml` | 기본 스택 | 추적 |
| `docker-compose.prod.yml` | 운영 override (web, restart, 로그 상한) | 추적 |
| `docker-compose.e2e.yml` | F05 브라우저 E2E override | 추적 |
| `docker-compose.override.yml` | 로컬 전용 바인드 마운트 | 추적 안 함 |
| `backend-pjt/Dockerfile`, `ai-pjt/Dockerfile`, `front-pjt/Dockerfile` | 이미지 정의 | 추적 |
| `backend-pjt/requirements.lock`, `requirements-build.lock`, `ai-pjt/requirements.lock`, `front-pjt/package-lock.json` | 의존성 고정 | 추적 |
| `deploy/nginx/default.conf` | gateway nginx | 추적 |
| `front-pjt/nginx/default.conf` | web nginx | 추적 |
| `deploy/nginx-host.conf` | 호스트 nginx 사본 (실제 `/etc/nginx/sites-available/s15p21b109`) | 추적 |
| `db/postgres/init.sql` | pgvector + RAG 테이블 초기화 | 추적 |
| `db/postgres/schema.sql`, `report_tables.sql` | 폐기된 뉴스 파이프라인 잔재 (미사용) | 추적 |
| `contract-fixtures/financial-calculation/v1/policy.json` | 재무 계산 정책 | 추적 |
| `contract-fixtures/simulation/v1/policy.json` | 은퇴 시뮬레이션 정책 | 추적 |
| `contract-fixtures/ai-lifecycle/v2/*.json` | Backend↔AI 계약 (step-registry 등) | 추적 |
| `ai-pjt/rag/data/catalog.json` | RAG 승인 코퍼스 카탈로그 | 추적 |
| `ai-pjt/rag/artifacts/official__official-catalog-2-2-0.tar.gz` | 공식 RAG 인덱스 백업 (SHA-256 `3e6e5e20…`) | 추적 |
| `.gitlab-ci.yml` | CI/CD 파이프라인 | 추적 |
| `deploy/make-env.sh`, `deploy/deploy.sh`, `deploy/check_env_contract.py` | env 생성, 배포, env 계약 검사 | 추적 |
| `deploy/f14-config-snapshot.sh`, `f14-rollback-rehearsal.sh`, `f14-regression.sh` | 설정 스냅샷, 롤백 리허설, 회귀 테스트 | 추적 |
| `.gitattributes`, `.dockerignore`, `.gitignore` | 줄바꿈 강제, 빌드 제외 목록, 추적 제외 | 추적 |
| 서버 `/srv/s15p21b109/secrets/app.env` | 운영 env | 저장소 밖 |

---

## 9. 트러블슈팅

| 증상 | 원인 | 확인·조치 |
|---|---|---|
| `docker compose up` 즉시 실패 `... is required` | 루트 `.env` 없음 또는 필수값 누락 | `bash deploy/make-env.sh`, `docker compose config --quiet` |
| migrate 가 `password authentication failed` | 기존 볼륨과 새 `.env` 의 비밀번호 불일치 | `ALTER USER retirewise PASSWORD '...'` 또는 (로컬) `docker compose down -v` |
| migrate 가 `FinancialPolicyError: policy is missing` | 로컬 override 바인드 마운트가 이미지의 `contract-fixtures` 를 가림 | override 에 `./contract-fixtures:/app/contract-fixtures:ro` 추가 |
| ai-pjt 재시작 반복 `GOOGLE_API_KEY is required` | GMS 키 미입력 | `.env` 에 입력 후 `up -d --force-recreate rag-bootstrap ai-pjt` |
| AI 응답만 실패, 컨테이너는 healthy | GMS 키 만료·오류, 모델명 오류 | 아래 실호출 명령. `3072` 가 나와야 정상 |
| 프론트 dev 에서 모든 API 404 | `front-pjt/.env` 없음 (프록시 미설정) | `cp front-pjt/.env.example front-pjt/.env` 후 Vite 재시작 |
| 운영에서 API 400 Bad Request | `DJANGO_ALLOWED_HOSTS` 에 도메인 없음 | `app.env` 수정 후 `deploy.sh --no-build` |
| 배포 후 `/api` 502 | gateway 가 옛 backend IP 사용 | `docker compose restart gateway` (10초 내 자동 회복) |
| CI `git clean` Permission denied | 컨테이너가 root 소유 파일 생성 | `sudo chown -R gitlab-runner:gitlab-runner /home/gitlab-runner/builds` |

```bash
# GMS 임베딩 실호출 확인 (크레딧 소모, 최소 횟수로)
docker compose exec -T ai-pjt python -c "from core.llm import embed_model; print(len(embed_model.get_text_embedding('연금')))"

# 운영 상태 점검 (러너 체크아웃 디렉터리)
C='sudo docker compose --project-name s15p21b109 --env-file /srv/s15p21b109/secrets/app.env -f docker-compose.yml -f docker-compose.prod.yml'
$C ps; $C ps -a migrate rag-bootstrap
$C logs --no-log-prefix --tail 100 rag-bootstrap
$C logs --tail 100 ai-pjt
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8080/                                  # 200
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8080/api/consulting/v1/consultations/   # 401
docker image inspect s15p21b109-ai-pjt:latest --format '{{index .Config.Labels "org.opencontainers.image.revision"}}'
```
