# RabbitMQ + SSE 도입 및 구현 계획

## 1. 문서 목적

이 문서는 은퇴 설계 특화 재무설계 AI Agent 프로젝트에 다음 두 기능을 도입하기 위한 기준과 작업 계획을 정의한다.

- 실시간 AI 상담 응답을 위한 HTTP/SSE 스트리밍
- 금융건강진단, 은퇴자금 시뮬레이션, 재무계획 및 PDF 생성과 같은 장기 작업을 위한 RabbitMQ 기반 비동기 처리

현재의 Django 모듈러 모놀리스와 FastAPI + LangGraph 분리 구조는 유지한다. Kafka, Kubernetes, Argo CD, Airflow, Spark, Hadoop, MongoDB는 이 도입 범위에 포함하지 않는다. 뉴스 데이터 파이프라인은 핵심 MVP와 분리된 선택 기능으로 취급한다.

---

## 2. 목표 아키텍처

```text
Vue Browser
    |
    | REST: 인증, 상담 정보, 대시보드, 커뮤니티, 작업 조회
    | SSE: 일반 AI 상담 답변 스트리밍
    v
Django Core
    |-- PostgreSQL
    |     - 사용자/동의
    |     - 상담/재무정보/메시지
    |     - 비동기 작업 상태
    |     - 보고서/산출물 메타데이터
    |     - Transactional Outbox
    |
    |-- HTTP/SSE ----------------------> FastAPI AI
    |                                    - LangGraph
    |                                    - 일반 QA/RAG/input assist
    |                                    - 토큰/이벤트 스트리밍
    |
    `-- Outbox Publisher --> RabbitMQ --> Worker
                              |           - 금융건강진단
                              |           - 은퇴자금 시뮬레이션
                              |           - 재무계획 생성
                              |           - PDF 생성
                              |
                              `--> retry queue / DLQ

Redis (선택)
    - 짧은 TTL 캐시
    - rate limit
    - 일시적인 스트리밍 상태
    - 영속 작업 상태의 원본으로는 사용하지 않음
```

### 역할 분리 원칙

| 구분 | 처리 방식 | 이유 |
|---|---|---|
| 일반 AI 질문·입력 보조 | HTTP/SSE | 사용자가 답변 생성을 실시간으로 보고 즉시 상호작용해야 함 |
| 금융건강진단 | RabbitMQ | 계산·AI 생성 실패 시 재시도와 상태 추적 필요 |
| 은퇴자금 및 생애현금흐름 시뮬레이션 | RabbitMQ | 실행 시간이 길어질 수 있고 요청 연결과 생명주기를 분리해야 함 |
| 재무계획 생성 | RabbitMQ | 여러 계산과 AI 호출을 묶은 신뢰성 있는 작업 명령에 해당 |
| PDF 생성 | RabbitMQ | 렌더링 실패, 파일 저장 실패 및 재시도 처리 필요 |
| 비동기 작업 결과 조회 | REST polling 우선 | 구현이 단순하고 연결 복구가 쉬움. 필요하면 향후 알림 SSE 추가 |

SSE와 RabbitMQ는 서로 대체하지 않는다. SSE는 사용자에게 실시간 응답을 전달하고, RabbitMQ는 서버 내부의 장기 작업을 신뢰성 있게 수행한다.

---

## 3. 도입 전 확정할 의사결정

구현 시작 전에 아래 항목을 팀에서 확정한다.

### 필수 결정

1. RabbitMQ 클라이언트/작업 프레임워크
   - MVP 권장안: Celery + RabbitMQ
   - 이유: Django 통합, worker 운영, 재시도와 지수 백오프, 작업 라우팅을 짧은 기간에 구현하기 유리함
   - ACK/NACK 동작은 Celery 설정과 RabbitMQ 관리 화면·로그로 검증함
   - RabbitMQ 프로토콜을 직접 학습하는 것이 최우선이면 `aio-pika` 기반 consumer를 별도 구현할 수 있으나 구현·테스트 범위가 커짐

2. Worker 코드 위치
   - 권장안: `backend-pjt` 내부에 task 모듈을 두고 별도 Celery worker 프로세스로 실행
   - 이유: 현재 재무 계산기와 Django ORM 모델이 `backend-pjt`에 있으므로 중복 없이 재사용 가능
   - FastAPI/LangGraph가 필요한 단계는 worker가 내부 HTTP API 또는 명확한 Python 서비스 경계를 통해 AI 서비스에 요청

3. SSE API 형태
   - 권장안: `POST /api/v1/consultations/{session_id}/messages/stream/`
   - 브라우저는 JWT가 포함된 `fetch()` 요청을 보내고 `ReadableStream`으로 SSE를 읽음
   - 브라우저 기본 `EventSource`는 POST body와 Authorization header 사용에 제약이 있어 사용하지 않음

4. 연결 종료 정책
   - 권장안: 브라우저 연결이 끊겨도 이미 시작된 AI 생성은 가능한 한 완료하고 최종 메시지를 DB에 저장
   - 사용자는 재접속 후 메시지 목록 API로 결과를 복구

5. 파일 저장소
   - 로컬 개발: 로컬 media 디렉터리
   - 운영: S3 호환 Object Storage 권장
   - DB에는 PDF 바이너리가 아니라 object key, URL, checksum, 생성 시각을 저장

6. 뉴스 데이터 파이프라인
   - MVP 기본 실행 구성에서 제외하고 선택 profile 또는 별도 compose로 격리
   - 뉴스 리포트 기능을 MVP에 포함한다면 RabbitMQ 장기 작업과 뉴스 배치 파이프라인의 책임을 별도 문서로 구분

---

## 4. 사용자가 준비하거나 결정해야 할 것

### 서비스 범위

- 비동기 생성 대상 보고서 목록과 우선순위 확정
- “상담 완료 시 전체 보고서 자동 생성”과 “사용자가 보고서별 생성 요청” 중 UX 결정
- PDF 보고서에 포함할 페이지와 템플릿 확정
- 비동기 작업의 허용 최대 실행 시간 확정
- 실패한 작업을 사용자가 직접 재실행할 수 있게 할지 결정

### 인프라 및 비밀정보

- 개발·배포 환경의 RabbitMQ 접속 정보
- 운영 Object Storage를 사용할 경우 bucket과 인증정보
- AI API key 및 모델 설정
- 내부 Django↔FastAPI 인증 secret
- 운영 도메인, HTTPS 종료 위치 및 reverse proxy 종류

### 금융 도메인 기준

- 금융건강진단 지표와 산식 승인
- 은퇴 시뮬레이션의 기본 가정 확정
  - 기대수익률
  - 물가상승률
  - 기대수명
  - 연금 수령 시점
  - 세금·수수료 반영 범위
- 계산 결과에 표시할 면책 문구 확정
- 계산 로직의 버전 변경 시 기존 보고서를 재생성할지 결정

### 개인정보 및 동의

- 저장할 동의 종류와 약관 버전 확정
- AI 처리 및 보고서 생성을 위한 개인정보 제공 범위 검토
- 보관 기간, 회원탈퇴 시 삭제·익명화 정책 결정
- 향후 마이데이터 연동을 고려한 데이터 출처 표시 기준 결정

---

## 5. SSE 구현 설계

### 5.1 요청 흐름

1. Vue가 Django 스트리밍 API에 JWT, 메시지, `step_id`, `form_data`를 전송한다.
2. Django가 사용자 인증과 상담 세션 소유권을 검증한다.
3. Django가 사용자 메시지를 PostgreSQL에 저장하고 `request_id`를 생성한다.
4. Django가 FastAPI `/chat/stream`에 내부 요청을 보낸다.
5. FastAPI가 LangGraph/LLM 이벤트를 SSE 형식으로 반환한다.
6. Django가 이벤트를 버퍼링하지 않고 Vue에 중계한다.
7. 답변 완료 후 assistant 메시지를 PostgreSQL에 한 번 저장한다.
8. Vue는 `done` 이벤트를 받은 뒤 화면 상태를 완료 처리한다.

### 5.2 SSE 이벤트 계약

```text
event: start
data: {"request_id":"ai_req_123","user_message_id":10}

event: token
data: {"content":"은퇴 준비를"}

event: metadata
data: {"intent":"qa","requires_approval":false}

event: done
data: {"request_id":"ai_req_123","message_id":11}

event: error
data: {"request_id":"ai_req_123","code":"AI_TIMEOUT","message":"AI 응답 생성 시간이 초과되었습니다."}

```

이벤트 사이에는 빈 줄을 넣고 모든 payload는 JSON으로 직렬화한다. `token` 이벤트에는 완성되지 않은 텍스트 조각만 넣고 DB에는 토큰마다 저장하지 않는다.

### 5.3 오류 및 복구 정책

- FastAPI 연결 실패: Django가 `error` 이벤트를 보내고 요청 상태를 실패로 저장
- 스트리밍 도중 실패: 생성된 부분 답변을 UI에 남기되 완료 메시지로 취급하지 않음
- 브라우저 연결 종료: 서버는 가능한 한 생성과 최종 DB 저장을 완료
- 프록시 idle timeout 방지: 15~30초마다 comment heartbeat 전송
- 재접속: 자동 토큰 이어받기보다 기존 메시지 목록 API로 최종 상태 복구
- 동일 `request_id` 중복 완료: 한 개의 assistant 메시지만 생성되도록 DB unique constraint 또는 멱등 처리 적용

### 5.4 실행 환경 요구사항

- Django를 ASGI로 실행
- reverse proxy 응답 버퍼링 비활성화
- `Content-Type: text/event-stream`
- `Cache-Control: no-cache`
- `X-Accel-Buffering: no`
- 스트리밍 구간의 읽기 timeout을 일반 API보다 길게 설정
- Django와 FastAPI 모두 연결 종료와 timeout을 구조화 로그로 기록

### 5.5 기존 구현에서 정리할 항목

- 정의되지 않은 `_stream_ai_response`를 실제 FastAPI 중계 구현으로 교체
- FastAPI `BackgroundTasks + callback`을 일반 채팅 기본 경로에서 제거
- 기존 polling API는 초기 안정화 기간에 fallback으로 유지
- Django와 FastAPI 요청 스키마의 `request_id`, `session_id`, `callback_url`, `status`, 내부 secret 불일치 해소
- 프론트의 `form` 필드와 Django의 `form_data` 필드명을 통일

---

## 6. RabbitMQ 비동기 작업 설계

### 6.1 영속 작업 모델

Redis가 아니라 PostgreSQL을 작업 상태의 원본으로 사용한다.

예시 모델: `AsyncJob`

| 필드 | 설명 |
|---|---|
| `id` | 내부 PK |
| `job_id` | 외부 노출 UUID, unique |
| `user_id` | 요청 사용자 |
| `session_id` | 상담 세션 |
| `job_type` | `financial_health`, `retirement_simulation`, `financial_plan`, `pdf_report` |
| `status` | `PENDING`, `PROCESSING`, `COMPLETED`, `FAILED`, `CANCELLED` |
| `progress` | 0~100 선택 값 |
| `input_snapshot` | 실행 시점 입력 데이터 또는 snapshot 참조 |
| `result` | 결과 JSON 또는 보고서 ID |
| `error_code` | 사용자 노출 가능한 안정적인 오류 코드 |
| `error_message` | 내부 진단 메시지 |
| `attempt_count` | 현재 시도 횟수 |
| `correlation_id` | API·메시지·worker 로그 연결 ID |
| `idempotency_key` | 중복 생성 방지 키 |
| `started_at` | 처리 시작 시각 |
| `finished_at` | 완료/실패 시각 |
| `created_at`, `updated_at` | 감사·운영 정보 |

권장 unique key 예시:

```text
(session_id, job_type, input_version, idempotency_key)
```

### 6.2 작업 명령 메시지

메시지에는 필요한 금융정보 전체를 무제한 복사하지 않고 식별자와 버전을 중심으로 넣는다.

```json
{
  "message_version": 1,
  "job_id": "0ba7...",
  "job_type": "retirement_simulation",
  "session_id": 12,
  "user_id": 3,
  "input_version": 4,
  "correlation_id": "corr_...",
  "requested_at": "2026-08-25T10:00:00Z"
}
```

Worker는 `job_id`로 작업을 조회하고 처리 직전에 필요한 snapshot을 읽는다. 메시지에는 비밀번호, 토큰, 불필요한 개인정보를 넣지 않는다.

### 6.3 Queue 구성

초기에는 작업 유형별 queue를 과도하게 나누지 않는다.

```text
retirewise.jobs       기본 장기 작업 queue
retirewise.jobs.retry 지연 재시도 queue
retirewise.jobs.dlq   최대 재시도 초과 또는 복구 불가능 메시지
```

PDF 렌더링의 자원 사용량이 다른 작업을 방해하면 다음 단계에서 `retirewise.pdf` queue와 worker concurrency를 분리한다.

### 6.4 ACK/NACK 및 재시도

- 메시지는 작업 성공과 DB commit 완료 후 ACK
- 일시적 오류는 retry 대상으로 처리
  - AI timeout
  - 일시적인 네트워크 오류
  - Object Storage 일시 장애
- 영구 오류는 즉시 실패 처리하거나 DLQ로 이동
  - 유효하지 않은 `job_type`
  - 삭제된 상담 세션
  - 지원하지 않는 메시지 버전
  - 입력 snapshot 검증 실패
- worker 비정상 종료 시 unacked 메시지는 재전달
- 지수 백오프 예시: 5초 → 30초 → 2분 → 10분
- 최대 시도 횟수 권장: 4회
- 재시도 횟수를 초과하면 `FAILED`로 저장하고 DLQ로 이동

Celery 사용 시 다음 동작을 명시적으로 설정하고 테스트한다.

- late ACK
- worker lost 시 task reject/requeue
- retry backoff와 jitter
- task time limit
- prefetch 제한
- 동일 작업의 중복 실행에 대비한 멱등성

### 6.5 멱등성

RabbitMQ는 중복 전달이 가능하다고 가정한다.

- worker는 시작 전에 `job_id` 상태 확인
- 이미 `COMPLETED`인 작업이면 결과를 다시 만들지 않고 ACK
- 동일 report version을 unique constraint로 보호
- PDF object key는 `session/job/version` 기반의 결정적 이름 사용
- 외부 AI 호출 전후의 상태를 구분하여 중복 비용을 최소화
- `PROCESSING` 상태가 일정 시간 이상 멈춘 작업을 탐지하는 복구 정책 마련

### 6.6 Transactional Outbox

API에서 `AsyncJob`만 저장하고 RabbitMQ publish에 실패하면 작업이 영원히 대기할 수 있다. 이를 방지하기 위해 동일 DB transaction에서 `AsyncJob`과 `OutboxEvent`를 함께 저장한다.

```text
API transaction
    1. AsyncJob(PENDING) 저장
    2. OutboxEvent(UNPUBLISHED) 저장
    3. commit

Outbox Publisher
    4. 미발행 이벤트 조회
    5. RabbitMQ publish + publisher confirm
    6. published_at 기록
```

Outbox publisher도 중복 publish가 가능하므로 consumer 멱등성은 반드시 필요하다.

### 6.7 작업 API

```text
POST /api/v1/consultations/{session_id}/jobs/
GET  /api/v1/consultations/{session_id}/jobs/{job_id}/
POST /api/v1/consultations/{session_id}/jobs/{job_id}/retry/
GET  /api/v1/consultations/{session_id}/reports/
GET  /api/v1/consultations/{session_id}/reports/{report_type}/
```

생성 요청 응답 예시:

```json
{
  "success": true,
  "data": {
    "job_id": "0ba7...",
    "status": "PENDING",
    "correlation_id": "corr_..."
  }
}
```

프론트는 2~3초 간격 polling을 사용하되 진행 중인 화면에서만 조회한다. `COMPLETED` 또는 `FAILED`가 되면 polling을 종료한다.

---

## 7. 구현 작업 계획

### Phase 0. 계약 및 범위 확정

목표: 구현 중 구조가 다시 흔들리지 않도록 API와 작업 단위를 먼저 고정한다.

- SSE 이벤트와 오류 코드 정의
- 비동기 `job_type` 목록 확정
- 보고서 생성 UX 결정
- 재시도 대상 오류 분류
- PDF 저장 위치 결정
- 뉴스 파이프라인을 MVP 기본 경로에서 분리

산출물:

- API 계약서
- 이벤트/메시지 JSON 예시
- 상태 전이표
- 환경변수 목록

완료 기준:

- 프론트, Django, AI 담당자가 동일 계약에 합의
- `PENDING → PROCESSING → COMPLETED/FAILED` 전이가 명확함

### Phase 1. 현재 계약 오류 정리

목표: 새로운 기능을 얹기 전에 기존 Django↔FastAPI 경계를 정상화한다.

- `form`/`form_data` 명칭 통일
- Django↔FastAPI 요청 schema 통일
- 내부 secret 전달과 검증 구현
- callback/polling 기존 경로에 대한 최소 통합 테스트 작성
- 환경변수와 기본 포트 정리

완료 기준:

- 사용자 메시지, 현재 폼, 세션 ID가 FastAPI까지 정확히 전달됨
- AI 성공·실패 결과가 중복 없이 DB에 저장됨

### Phase 2. SSE 기본 경로 구현

목표: 일반 AI 상담에서 토큰 또는 의미 단위 응답을 실시간 표시한다.

- FastAPI `/chat/stream` 구현
- LangGraph streaming 이벤트 변환
- Django ASGI 스트리밍 proxy 구현
- Vue `fetch + ReadableStream` parser 구현
- `start/token/metadata/done/error` 이벤트 처리
- 완료 시 assistant 메시지 단일 저장
- heartbeat, timeout, 연결 종료 처리
- 기존 polling fallback 유지

완료 기준:

- 첫 응답 조각이 전체 답변 완료 전에 표시됨
- 한글 JSON과 여러 줄 데이터가 깨지지 않음
- 새로고침 후 최종 메시지가 복구됨
- 중간 연결 종료가 서버 오류로 확산되지 않음

### Phase 3. RabbitMQ 개발 환경 구축

목표: 로컬에서 broker, worker, Django를 한 번에 실행하고 관찰할 수 있게 한다.

- RabbitMQ management 이미지 추가
- 기본 user/vhost/queue/DLX 정책 설정
- Celery app과 worker 프로세스 추가
- healthcheck와 startup dependency 추가
- 환경변수 예제 작성
- RabbitMQ 관리 화면에서 queue, unacked, retry, DLQ 확인

완료 기준:

- 테스트 작업 발행과 소비 성공
- worker 강제 종료 후 메시지가 유실되지 않고 재처리됨

### Phase 4. 영속 작업 및 Outbox 구현

목표: broker 장애에도 작업 요청이 사라지지 않게 한다.

- `AsyncJob`, `OutboxEvent` 모델과 migration
- 작업 생성·조회 API
- idempotency key 처리
- outbox publisher 프로세스
- publisher confirm과 발행 상태 기록
- correlation ID 전파

완료 기준:

- RabbitMQ 중단 중 생성한 작업이 DB에 남음
- RabbitMQ 복구 후 자동 발행됨
- 같은 idempotency key의 요청이 작업을 중복 생성하지 않음

### Phase 5. 장기 작업 이관

목표: 기존 동기 보고서 생성을 worker로 이동한다.

권장 이관 순서:

1. 은퇴자금 시뮬레이션
2. 금융건강진단
3. 재무계획 생성
4. PDF 생성

작업별 공통 구현:

- 입력 snapshot 검증
- `PROCESSING` 전환
- 계산/AI 호출
- 결과와 보고서 version 저장
- 성공 commit 후 ACK
- 재시도 가능 오류 분류
- 최대 재시도 초과 시 `FAILED`와 DLQ 기록

완료 기준:

- API 응답이 계산 완료를 기다리지 않고 `202 Accepted` 반환
- 동일 메시지를 두 번 전달해도 보고서가 중복 생성되지 않음
- 실패 원인과 시도 횟수를 작업 API에서 확인 가능

### Phase 6. PDF 및 파일 저장

목표: 생성된 재무 결과를 검증 가능한 PDF로 제공한다.

- HTML 또는 문서 템플릿 확정
- PDF worker task 구현
- Object Storage 업로드
- checksum과 object key 저장
- 다운로드 권한 검증 API
- 임시 파일 정리

완료 기준:

- 사용자 본인의 PDF만 다운로드 가능
- 같은 작업 재처리 시 불필요한 중복 파일이 생기지 않음
- 실패 시 불완전 파일과 DB 상태가 남지 않음

### Phase 7. 운영 안정화와 관찰성

목표: 장애 원인을 빠르게 추적하고 안전하게 복구한다.

- 모든 API·메시지·worker 로그에 `correlation_id`, `job_id`, `session_id` 포함
- queue depth, 처리 시간, 실패율, retry 횟수 모니터링
- DLQ 조회와 수동 재처리 절차 작성
- 장시간 `PROCESSING` 작업 탐지
- graceful shutdown 검증
- 개인정보가 로그와 메시지에 노출되지 않는지 점검

완료 기준:

- 하나의 correlation ID로 Django→RabbitMQ→worker 실행을 추적 가능
- DLQ 메시지의 원인 확인과 안전한 재처리 가능

---

## 8. 테스트 계획

### SSE

- 정상 token 스트림 및 `done` 이벤트
- FastAPI 연결 실패
- LLM timeout
- 브라우저 중간 연결 종료
- 한글, 줄바꿈, 긴 JSON payload
- 동일 요청 완료 이벤트 중복 수신
- reverse proxy buffering 비활성화 확인
- 새로고침 후 메시지 복구

### RabbitMQ/Worker

- 정상 작업 완료와 ACK
- worker 처리 중 강제 종료 후 재전달
- 일시 오류의 지수 백오프 재시도
- 영구 오류의 즉시 실패/DLQ 이동
- 최대 재시도 초과
- 동일 `job_id` 중복 전달
- broker 중단 중 Outbox 적재 및 복구 후 발행
- publisher confirm 이전 publisher 종료
- 장시간 `PROCESSING` 복구

### 데이터 정합성

- 동일 report type의 최신 version 하나만 유지
- 계산 입력 snapshot과 결과 version 연결
- 작업 완료와 보고서 저장의 transaction 경계
- 사용자가 다른 사용자의 job/report를 조회하지 못함
- 회원탈퇴·세션 삭제 시 작업과 파일 처리 정책

### 부하 및 운영

- 동시 SSE 연결 수 목표치 테스트
- worker concurrency별 DB connection 수 확인
- 긴 PDF 작업이 다른 계산 작업을 막지 않는지 확인
- RabbitMQ queue 적체 시 API가 계속 빠르게 응답하는지 확인

---

## 9. 예상 일정

1~2명이 기존 코드를 이해하고 구현한다는 전제의 대략적인 범위다.

| 단계 | 예상 기간 |
|---|---:|
| Phase 0 계약 및 범위 확정 | 0.5~1일 |
| Phase 1 현재 계약 오류 정리 | 1~2일 |
| Phase 2 SSE 구현 및 안정화 | 3~5일 |
| Phase 3 RabbitMQ/Celery 개발 환경 | 1~2일 |
| Phase 4 영속 작업과 Outbox | 3~5일 |
| Phase 5 장기 작업 이관 | 4~7일 |
| Phase 6 PDF와 파일 저장 | 2~4일 |
| Phase 7 관찰성·장애 테스트 | 2~4일 |

전체 예상은 약 3~5주다. 시연 가능한 최소 범위는 SSE, RabbitMQ 기본 worker, 은퇴 시뮬레이션 작업 하나까지 약 1.5~2주로 잡을 수 있다. 일정은 PDF 템플릿 완성도와 AI 호출 복잡도에 따라 달라진다.

---

## 10. 우선 적용할 저장소 영역

```text
backend-pjt/
  config/                 RabbitMQ/Celery/ASGI/환경 설정
  consulting/             SSE proxy, 메시지 저장, 상담 입력 snapshot
  reports/                AsyncJob 연계, 보고서 version 및 결과 저장
  jobs/ 또는 core/jobs/   AsyncJob, OutboxEvent, publisher, 공통 상태 전이

ai-pjt/
  main.py                 /chat/stream
  schemas/                Django↔FastAPI 계약
  graph/                  LangGraph streaming

front-pjt/
  src/api/                SSE fetch client, 작업 API
  src/stores/             스트림 및 작업 상태
  src/views/ai/            token 표시, 오류·복구 UI
  src/views/dashboard/     장기 작업 진행/실패/재시도 UI

docker-compose.yml         Django, FastAPI, PostgreSQL, Redis(선택), RabbitMQ, worker
docs/                      API·메시지 계약, 운영 및 DLQ runbook
```

---

## 11. 구현 완료 정의

RabbitMQ + SSE 도입은 다음 조건을 모두 충족하면 완료로 본다.

- 일반 AI 상담에서 실제 생성 중인 답변이 SSE로 점진적으로 표시된다.
- SSE 연결이 끊겨도 최종 메시지를 다시 조회할 수 있다.
- 장기 작업 API는 즉시 `202 Accepted`를 반환한다.
- 작업 상태가 PostgreSQL에 영속적으로 기록된다.
- broker 또는 worker 장애 시 작업 명령이 유실되지 않는다.
- 중복 메시지가 중복 보고서나 중복 PDF를 만들지 않는다.
- 일시 오류는 지수 백오프로 재시도된다.
- 최대 재시도 초과 메시지는 DLQ와 실패 상태에 남는다.
- 하나의 correlation ID로 요청 전체를 추적할 수 있다.
- PDF와 보고서는 사용자 소유권 검증 후에만 조회된다.
- 핵심 경로의 자동화 테스트와 장애 시나리오 테스트가 통과한다.

---

## 12. 권장 첫 번째 구현 단위

전체를 한 번에 구현하지 않고 다음 vertical slice를 먼저 완성한다.

1. 일반 QA 한 종류를 SSE로 스트리밍
2. `AsyncJob`과 `OutboxEvent` 모델 추가
3. 은퇴자금 시뮬레이션 한 종류를 RabbitMQ worker로 실행
4. 작업 상태 polling 화면 제공
5. worker 강제 종료, broker 중단, 중복 전달 테스트

이 단위가 통과하면 동일 패턴으로 금융건강진단, 재무계획, PDF 생성을 확장한다. 이렇게 하면 SSE와 RabbitMQ의 핵심 위험을 가장 작은 제품 흐름에서 먼저 검증할 수 있다.
