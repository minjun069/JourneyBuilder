# RetireWise

사용자의 자산·현금 흐름·재무 목표를 바탕으로 은퇴 설계와 금융 건강 진단을 지원하는 AI 재무설계 서비스다.

## 현재 애플리케이션 구성

| 경로 | 역할 | 로컬 실행 |
|---|---|---|
| `front-pjt/` | Vue 사용자 화면 | 별도 Vite 개발 서버 |
| `backend-pjt/` | Django 업무 API와 AI 요청 상태 | 루트 Compose의 `backend` |
| `ai-pjt/` | FastAPI·LangGraph 상담 서비스 | 루트 Compose의 `ai-pjt` |
| `db/` | 애플리케이션 PostgreSQL 초기화 | 루트 Compose의 `postgres` |

루트 `docker-compose.yml`은 Backend·AI·PostgreSQL·Redis의 통합 실행 진입점이다. 기존 뉴스 분석과 Kafka·Flink·배치 데이터 파이프라인은 이번 프로젝트에서 폐기됐으며, `data-pjt/`, `docker-compose-kafka.yml`, `docker-compose-flink.yml`, `db/docker-compose.yml` 등 남아 있는 산출물은 현재 실행 경로에서 사용하지 않는다. P03 범위를 넘어서는 파일 삭제는 별도 정리 작업에서 수행한다.

## Compose 실행 계약

Compose는 다음 순서로 준비 상태를 확인한다.

```text
PostgreSQL healthy ─┬─→ AI
                    └─→ Django migration 완료 ─→ Backend
Redis healthy ─────────────────────────────────→ Backend
```

- 서비스 간 연결은 `postgres`, `redis`, `backend`, `ai-pjt`라는 내부 DNS를 사용한다.
- host 포트는 기본 공개하지 않는다. 필요한 개발 포트는 별도 override에서 loopback에만 공개한다.
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

현재 `backend-pjt/config/settings.py`는 아직 PostgreSQL 연결을 환경변수에서 읽지 않는다. I06 Backend lifecycle 반영 전에는 migration과 Backend readiness가 실패하므로 위 전체 기동을 G1 통과로 판정하지 않는다. P04가 I06 반영 뒤 빈 volume에서 전체 clean start와 종료를 다시 검증한다.

## 개발 원칙

- AI는 Django 업무 테이블을 직접 수정하지 않는다.
- 사용자가 승인하기 전 AI의 변경 제안은 저장된 업무 데이터가 아니다.
- 외부 HTTP·AI 계약은 `docs/contracts/`, 데이터 소유권은 `docs/data/schema.md`를 따른다.
- 실제 secret, 로컬 volume 데이터, 자동 생성물은 커밋하지 않는다.
