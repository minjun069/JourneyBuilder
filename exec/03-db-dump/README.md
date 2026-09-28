# 3. DB 덤프 파일

작성일 2026-09-28 / 기준 커밋 `1b83057` (master) / 원본 로컬 개발 DB (PostgreSQL 16.15, pgvector 0.8.6)

## 1. 파일 목록

| 파일 | 내용 | 크기 |
|---|---|---|
| `retirewise_db_schema.sql` | `retirewise_db` 전체 스키마 (테이블 40개, 인덱스, 제약조건, `vector` 확장). 데이터 없음 | 약 62 KB |
| `retirewise_db_data.sql` | 스키마 + 데이터. 재생성 가능한 데이터는 제외 (아래 표) | 약 413 KB |

두 파일 모두 plain SQL 형식이며 `--no-owner --no-acl` 로 만들어 계정 이름에 의존하지 않는다. Django 마이그레이션은 최신(`consulting.0010_consultingsession_confirmed_empty_sections`)까지 적용된 상태다.

### 데이터에서 제외한 테이블

| 테이블 | 제외 이유 | 복원 방법 |
|---|---|---|
| `rag_index_manifests`, `rag_index_chunks`, `rag_active_indexes` | 벡터 인덱스. 부분 복원 시 오히려 `rag-bootstrap` 의 무결성 검사에 걸린다 | `rag-bootstrap` 이 `ai-pjt/rag/artifacts/official__*.tar.gz` 에서 자동 복원 |
| `checkpoints`, `checkpoint_blobs`, `checkpoint_writes`, `checkpoint_migrations` | LangGraph 요청 단위 임시 체크포인트 | AI 서비스 기동 시 자동 생성 |
| `ai_*` | AI 대화 컨텍스트·임시 실행 상태 (TTL 로 정리됨) | AI 서비스 기동 시 자동 생성 |
| `django_session`, `django_admin_log` | 세션·관리 로그 | 불필요 |
| `token_blacklist_*` | JWT 로그아웃 블랙리스트 | 불필요 (재로그인만 필요) |

### 데이터에 포함된 주요 내용

| 테이블 | 행 수 | 내용 |
|---|---|---|
| `accounts_users` | 47 | PB 1명, 시드 고객 40명(`@retirewise.demo`), 팀 개발용 테스트 계정 6명 |
| `consulting_sessions` | 50 | 상담 세션 |
| `consulting_messages` | 156 | 상담 메시지 |
| `consulting_*` (재무 정보 13개 테이블) | 각 30~160 | 기본정보·자산·부채·소득·지출·재무목표 |
| `reports` | 45 | 재무 진단 리포트 |
| `community_posts` / `comments` / `likes` | 12 / 3 / 2 | 커뮤니티 |
| `django_migrations`, `django_content_type`, `auth_permission` | — | 마이그레이션 이력과 권한 (앱 데이터 정합용) |

비밀번호는 Django PBKDF2 해시로만 저장돼 있다.

## 2. 덤프 생성 방법

postgres 포트는 호스트에 공개되지 않으므로 컨테이너 안에서 `pg_dump` 를 실행한다. 저장소 루트에서 실행한다.

```bash
# 스키마 전용
docker compose exec -T postgres pg_dump -U retirewise -d retirewise_db \
  --no-owner --no-acl --schema-only > exec/03-db-dump/retirewise_db_schema.sql

# 스키마 + 데이터 (재생성 가능한 데이터 제외)
docker compose exec -T postgres pg_dump -U retirewise -d retirewise_db --no-owner --no-acl \
  --exclude-table-data='rag_*' --exclude-table-data='checkpoint*' --exclude-table-data='ai_*' \
  --exclude-table-data='django_session' --exclude-table-data='django_admin_log' \
  --exclude-table-data='token_blacklist_*' > exec/03-db-dump/retirewise_db_data.sql
```

- `--exclude-table-data` 는 테이블 정의는 남기고 행만 뺀다.
- Windows 는 Git Bash 에서 실행한다. PowerShell 의 `>` 리다이렉트는 인코딩을 바꿀 수 있다.
- 운영 서버는 앞에 `sudo docker compose --project-name s15p21b109 --env-file /srv/s15p21b109/secrets/app.env -f docker-compose.yml -f docker-compose.prod.yml` 를 붙인다. 운영 덤프는 실사용자 개인정보가 들어 있으므로 저장소에 올리지 않는다.

## 3. 복원 방법

새 환경(빈 볼륨)에 복원하는 순서다.

```bash
# 1) postgres 만 띄운다 (init.sql 이 vector 확장과 rag_* 테이블을 만든다)
docker compose up -d postgres

# 2) init.sql 이 만든 rag_* 테이블과 충돌하지 않도록 public 스키마를 비운 뒤 덤프를 넣는다
docker compose exec -T postgres psql -U retirewise -d retirewise_db \
  -c 'DROP SCHEMA public CASCADE; CREATE SCHEMA public;'
docker compose exec -T postgres psql -U retirewise -d retirewise_db -v ON_ERROR_STOP=1 \
  < exec/03-db-dump/retirewise_db_data.sql

# 3) 나머지 서비스를 올린다
docker compose up --build -d
```

- 3단계에서 `migrate` 는 이미 적용된 마이그레이션을 건너뛰고, `rag-bootstrap` 이 RAG 인덱스를 채우고, AI 서비스가 체크포인트·컨텍스트 테이블을 확인한다.
- 스키마만 필요하면 2단계에서 `retirewise_db_schema.sql` 을 넣고 `docker compose exec backend python manage.py bootstrap_demo` 로 시연 데이터를 만든다.
- 덤프 첫 줄의 `\restrict` 는 PostgreSQL 16.10 이상의 `psql` 이 인식하는 보안 메타 명령이다. 다른 클라이언트로 넣을 때 오류가 나면 첫 `\restrict` 줄과 마지막 `\unrestrict` 줄을 지운다.
- 이미 데이터가 있는 볼륨에 복원하면 2단계의 `DROP SCHEMA` 가 기존 데이터를 모두 지운다. 운영에서는 실행하지 않는다.

## 4. 스키마 개요

| 소유 | 테이블 | 설명 | 주요 관계 |
|---|---|---|---|
| accounts | `accounts_users` | 회원. 이메일 로그인, `is_staff=true` 가 PB | — |
| accounts | `accounts_users_groups`, `accounts_users_user_permissions` | Django 권한 M2M | → `accounts_users` |
| consulting | `consulting_sessions` | 상담 세션. 상태 `onboarding / in_progress / completed / abandoned`, 빈 섹션 확인 목록 `confirmed_empty_sections` | → `accounts_users` |
| consulting | `consulting_messages` | 상담 메시지 (`user / assistant`), AI 추출 데이터 jsonb | → `consulting_sessions` |
| consulting | `consulting_ai_request_executions` | AI 요청 실행 기록 (세션당 처리 중 1건 부분 unique, 만료 시각) | → sessions, messages |
| consulting | `consulting_ai_terminal_receipts` | AI 완료 영수증 (`request_id` unique) | → sessions, messages |
| consulting | `consulting_basic_info` | 기본 정보 (나이, 직업, 혼인, 부양가족, 투자성향, 소득, 은퇴·기대수명) | 세션 1:1 |
| consulting | `consulting_financial_mindset` | 경제 관념 서술 6항목 | 세션 1:1 |
| consulting | `consulting_financial_satisfaction` | 재무 만족도 5항목 (1~5점) | 세션 1:1 |
| consulting | `consulting_financial_assets` | 금융자산 | 세션 1:N |
| consulting | `consulting_pension_assets` | 연금자산 | 세션 1:N |
| consulting | `consulting_real_estate_assets` | 부동산자산 | 세션 1:N |
| consulting | `consulting_liabilities` | 부채 | 세션 1:N |
| consulting | `consulting_incomes` | 소득 | 세션 1:N |
| consulting | `consulting_expenses` | 지출 (주기 jsonb) | 세션 1:N |
| consulting | `consulting_goal_periods` / `consulting_goal_period_items` | 은퇴 전·후 기간별 목표와 항목 | 세션 1:N, 기간 1:N |
| consulting | `consulting_goal_milestones` | 나이별 마일스톤 | 세션 1:N |
| reports | `reports` | 재무 진단 리포트 9종 (버전, `is_latest` 부분 unique) | → sessions |
| community | `community_posts` | 게시글 (카테고리, 재무 정보 첨부 sections, soft delete) | → users |
| community | `community_comments` | 댓글 (soft delete) | → posts, users |
| community | `community_likes` | 좋아요 (`user, post` unique) | → posts, users |
| Django 기본 | `django_migrations`, `django_content_type`, `auth_*`, `django_session`, `django_admin_log`, `token_blacklist_*` | 프레임워크 테이블 | — |
| AI (RAG) | `rag_index_manifests`, `rag_index_chunks`, `rag_active_indexes` | RAG 인덱스 manifest, 벡터 청크(`vector`), 논리→물리 포인터 | — |
| AI (LangGraph) | `checkpoints`, `checkpoint_blobs`, `checkpoint_writes`, `checkpoint_migrations` | 대화 그래프 체크포인트 | — |
| 레거시 | `ai_request_checkpoints` | 이전 AI 전송 방식의 잔재. 현재 코드에서 사용하지 않음 | — |

AI 서비스는 기동할 때 `ai_committed_contexts`, `ai_context_terminal_receipts`, `ai_temporary_executions`, `ai_conversation_turn_archive`(vector 3072), `ai_context_summary_jobs` 를 `CREATE TABLE IF NOT EXISTS` 로 만든다. 이 덤프를 뜬 로컬 DB 에는 최신 AI 서비스가 아직 기동되지 않아 이 5개 테이블이 없다.

## 5. 시연 계정

| 계정 | 비밀번호 | 역할 |
|---|---|---|
| `admin1234@naver.com` | `admin1234` | PB |
| `demo001@retirewise.demo` ~ `demo040@retirewise.demo` | `demo-pass-2026!` | 시드 고객 |

덤프 없이도 `docker compose exec backend python manage.py bootstrap_demo` 로 같은 계정과 시드 데이터를 다시 만들 수 있다.

## 6. 주의사항

- 이 덤프는 로컬 개발 DB 에서 만들었으며 실사용자 데이터는 없다. 팀원 테스트 계정 6개가 포함돼 있다.
- 운영 DB 덤프는 실사용자 개인정보가 들어 있으므로 저장소·채팅에 올리지 않는다.
- 서버에서 `docker compose down -v` 를 실행하면 볼륨이 지워진다. 복구는 덤프로만 가능하다.
- `POSTGRES_PASSWORD` 는 볼륨을 처음 만들 때 DB 에 고정된다. 새 볼륨에 복원할 때는 루트 `.env` 의 값이 그대로 쓰인다.
