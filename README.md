# RetireWise

사용자의 자산·현금 흐름·재무 목표를 정리·분석해 금융 건강과 은퇴 준비 상태를 진단·설명하는 AI 재무진단 서비스다.

> 현재 채택된 AI-001 3주 MVP의 사용자 기능은 재무 정보 입력·검토와 재무 진단까지다. 개인별 자산배분안·상품 구성·매매·리밸런싱 계획을 생성하는 재무 설계 기능은 포함하지 않는다.

## 현재 애플리케이션 구성

| 경로 | 역할 | 로컬 실행 |
|---|---|---|
| `front-pjt/` | Vue 사용자 화면 | 별도 Vite 개발 서버 |
| `backend-pjt/` | Django 업무 API와 AI 요청 상태 | 루트 Compose의 `backend` |
| `ai-pjt/` | FastAPI·LangGraph 상담 서비스 | 루트 Compose의 `ai-pjt` |
| `ai-docs/` | AI 요구사항·아키텍처·계약·평가·보안 공유 문서 | [문서 읽기 시작점](ai-docs/README.md) |
| `db/` | 애플리케이션 PostgreSQL 초기화 | 루트 Compose의 `postgres` |

루트 `docker-compose.yml`은 Backend·AI·PostgreSQL·Redis의 통합 실행 진입점이다. 기존 뉴스 분석과 Kafka·Flink·배치 데이터 파이프라인은 이번 프로젝트에서 폐기됐으며, `data-pjt/`, `docker-compose-kafka.yml`, `docker-compose-flink.yml`, `db/docker-compose.yml` 등 남아 있는 산출물은 현재 실행 경로에서 사용하지 않는다. 이 비활성 산출물의 삭제는 별도 정리 작업에서 수행한다.

## Compose 실행 계약

Compose는 다음 순서로 준비 상태를 확인한다.

```text
PostgreSQL healthy ─┬─→ AI
                    └─→ Django migration 완료 ─→ Backend ─→ Gateway(nginx)
Redis healthy ─────────────────────────────────→ Backend
```

- 서비스 간 연결은 `postgres`, `redis`, `backend`, `ai-pjt`라는 내부 DNS를 사용한다.
- host 포트는 API 진입점인 `gateway`(nginx)만 공개한다 — `API_GATEWAY_PORT`(기본 `8080`, 0.0.0.0 바인딩).
  로컬 개발은 `.env`에서 `API_GATEWAY_PORT=127.0.0.1:8081` 처럼 loopback으로 제한하고,
  프론트 `front-pjt/.env`의 `VITE_API_BASE_URL`을 같은 주소(`http://localhost:8081/api/v1`)로 맞춘다.
  그 외 서비스(postgres·redis·backend·ai)는 host 포트를 공개하지 않는다.
- PostgreSQL과 Redis 데이터는 Compose project별 named volume에 저장된다.
- PostgreSQL 초기화 SQL은 새 `postgres_data` volume을 만들 때만 실행된다.
- secret에는 동작 가능한 기본값이 없다. 실제 값은 커밋하지 않은 루트 `.env` 또는 실행 환경에서 주입한다.

필수 환경변수:

| 변수 | 용도 |
|---|---|
| `POSTGRES_PASSWORD` | `retirewise` PostgreSQL 계정 비밀번호 |
| `DATABASE_URL` | AI checkpoint용 PostgreSQL DSN. host는 `postgres`, DB는 `retirewise_db`를 사용 |
| `DJANGO_SECRET_KEY` | Django secret |
| `AI_SERVICE_BEARER_TOKEN` | Backend·AI 서비스 인증용 공유 token |

기본 DB 이름과 사용자는 각각 `retirewise_db`, `retirewise`다. 다른 값을 사용할 때는 `POSTGRES_DB`, `POSTGRES_USER`, `DATABASE_URL`을 함께 바꿔야 한다. LLM provider에 따라 `GOOGLE_API_KEY` 또는 `OPENAI_API_KEY`도 주입한다. 전체 AI·Backend 변수 목록은 `ai-pjt/.env.example`과 `backend-pjt/.env.example`을 따른다.

secret 누락 여부와 Compose 구조는 컨테이너를 만들기 전에 확인할 수 있다.

```bash
docker compose config --quiet
```

인프라만 기동하고 준비 상태를 확인하는 명령은 다음과 같다.

```bash
docker compose up -d postgres redis
docker compose ps postgres redis
docker compose down
```

전체 이미지와 서비스를 기동하는 최종 명령은 다음과 같다.

```bash
docker compose up --build -d
docker compose ps
docker compose exec ai-pjt python readiness.py
docker compose exec backend python readiness.py
docker compose down
```

`backend-pjt/config/settings.py`는 PostgreSQL과 필수 secret을 환경변수에서 읽으며,
G1은 빈 volume·clean image에서 migration, 두 애플리케이션 readiness, PostgreSQL·Redis
장애와 복구, 종료 후 자원 정리까지 검증했다. `make g1-check`는 D1/G0·P01~P14의
결정적 검사와 산출물 정합성을, `make g1-runtime`은 이 실행환경을 다시 검사한다.

이 G1 결과는 동결된 D1 v2 HTTP·AI lifecycle의 제품 구현 완료를 뜻하지 않는다.
현재 `/chat`, legacy callback과 메시지 단위 승인 흐름을 v2 request·callback·polling·change별
결정으로 전환하는 작업은 F04와 F12에 남아 있다. 정확한 현재/목표 차이는 공유 계약 문서의
`ai-docs/contracts/current-state.md`, 입력별 활성 상태는
`contract-fixtures/g1/artifact-consistency.json`을 따른다.

## PB 관리자 대시보드

PB(프라이빗 뱅커)가 전체 고객의 재무 데이터를 통합 조회하는 화면이다. 프론트 라우트는
`/pb`(개요·고객 리스트·고객 상세·세그먼트), API 는 `backend-pjt/adminapi/` 의
`/api/v1/admin/*` 전체가 `IsAdminUser`(is_staff) 로 보호되며 v1 은 조회 전용(GET)이다.
로그인 후 `GET /api/v1/auth/me/` 의 `is_staff` 로 역할을 판별한다.
집계는 요청 시 계산이라 PB 화면의 60초 폴링만으로 최신 DB 상태가 반영된다.

로컬 시연 환경은 명령 한 번으로 세팅한다 — 팀 공용 PB 계정과 시드 고객 40명이 생성된다.

```bash
docker compose exec backend python manage.py bootstrap_demo
```

| 구분 | 값 |
|---|---|
| PB 계정 | `admin1234@naver.com` / `admin1234` |
| 시드 고객 | `demo001~040@retirewise.demo` / 공통 `demo-pass-2026!` |

- 계정·시드는 DB(볼륨)에 저장되므로 환경(팀원)마다 한 번씩 실행한다. 재실행해도 안전하다
  (데모 계정만 재생성, 실제 가입 고객 데이터는 건드리지 않음).
- 데모가 아닌 실제 고객이 있는 DB에서는 오염 방지를 위해 `--yes` 를 붙여야 실행된다.
- **운영 서버에서는 사용 금지** — PB 계정은 `create_pb` 로 강한 비밀번호를 지정해 개별 생성한다.

```bash
# 운영용 PB 계정 생성 (회원가입 화면으로는 만들 수 없다)
docker compose exec backend python manage.py create_pb pb@example.com --name "김PB" --password "..."
```

## 개발 원칙

- AI는 Django 업무 테이블을 직접 수정하지 않는다.
- 사용자가 승인하기 전 AI의 변경 제안은 저장된 업무 데이터가 아니다.
- 외부 HTTP·AI 계약은 서비스 schema·serializer와 contract test로 검증하고, 공용 실행 fixture는 `contract-fixtures/`에 둔다.
- 실제 secret, 로컬 volume 데이터, 자동 생성물은 커밋하지 않는다.
