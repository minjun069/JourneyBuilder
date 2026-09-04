# Django HTTP API 계약

상태: **AI 상담 HTTP·승인 생명주기 v2 동결 — 나머지 HTTP API 미정**

## 공통 규칙

- Browser는 `/api/v2` 아래 Django API만 호출한다. AI 서버와 MCP 서버는 직접 호출하지 않는다.
- 사용자 API는 JWT 인증과 상담 세션 소유권을 검증한다. 인증되지 않은 요청은 `401`, 인증됐지만 소유하지 않은 세션·request·action은 존재 여부를 숨기기 위해 `404`로 응답한다.
- AI 비동기 요청·응답에는 `contract_version: "2.0"`을 포함한다.
- 금액은 저장·AI canonical payload에서 KRW 원 단위 정수를 사용하며 화면 단위 변환은 Frontend adapter가 담당한다. canonical JSON number는 크기와 무관하게 원 단위이고, `만`·`억` 해석은 원시 화면 문자열에만 적용한다.
- form field의 `unit: percent`는 퍼센트포인트라서 `5`가 5%를 뜻한다. chart assumption의 `unit: decimal`은 `0.05`가 5%를 뜻하며 두 표현을 암묵적으로 섞지 않는다.
- 날짜는 `YYYY-MM-DD`, 시각은 UTC ISO 8601 문자열로 교환하고 Frontend가 시각만 표시 timezone으로 변환한다.
- 오류는 다음 envelope를 사용하며 내부 stack, credential, 원본 금융정보를 포함하지 않는다.

```json
{
  "success": false,
  "error": {
    "code": "REQUEST_INVALID",
    "message": "요청 내용을 확인해주세요.",
    "retryable": false
  }
}
```

## 메시지 접수

`POST /api/v2/consultations/{session_id}/messages/`

필수 header:

```text
Authorization: Bearer <user-jwt>
Idempotency-Key: <client-generated-uuid>
```

```json
{
  "contract_version": "2.0",
  "content": "월급은 세후 300만 원이에요.",
  "step_id": "basic-info",
  "form_data": {}
}
```

- `content`: 필수, 1~2000자
- `step_id`: 실제 화면 단계 ID. 폼 맥락이 없으면 `general`
- `form_data`: 현재 화면의 미저장 폼. 단계별 canonical adapter가 생성한다.
- `form` 별칭은 v2에서 지원하지 않는다.

접수 성공은 `202 Accepted`다.

```json
{
  "contract_version": "2.0",
  "success": true,
  "data": {
    "user_message_id": 10,
    "request_id": "ai_req_...",
    "status": "processing",
    "processing_deadline_at": "2026-09-02T06:01:30Z"
  }
}
```

같은 사용자·세션에서 같은 `Idempotency-Key`와 같은 payload를 다시 보내면 기존 응답을 반환한다. 같은 키의 payload가 다르면 `409 IDEMPOTENCY_CONFLICT`다. 접수 결과와 payload digest는 24시간 보존한다. AI 접수 자체가 실패하면 `502 AI_DISPATCH_FAILED`와 이미 생성된 `user_message_id`·`request_id`를 반환하며, 사용자가 다시 시도할 때는 새 키를 사용한다.

## AI 결과 polling

`GET /api/v2/consultations/{session_id}/ai-responses/{request_id}/`

Frontend는 3초 간격으로 최대 90초 동안 polling한다. 알려진 작업의 `processing`, `completed`, `failed`, `expired`는 모두 `200`이며 transport·인증·소유권·상태 저장소 오류만 non-2xx다.

| 상태 | 응답 데이터와 의미 |
|---|---|
| `processing` | `request_id`, `status`, `processing_deadline_at` |
| `completed` | 저장된 assistant `message`, citation, 검증된 draft action |
| `failed` | `error.code`, 사용자 메시지, `retryable` |
| `expired` | `AI_REQUEST_EXPIRED`; 이전 작업을 되살리지 않고 새 메시지 접수를 안내 |

AI 실행 deadline은 60초, callback 전달 여유를 포함한 Django processing deadline은 90초다. 활성 상태 안전 TTL은 10분이고 terminal 상태와 만료 tombstone은 24시간 보존한다. 90초가 지나도 terminal callback이 없으면 Django는 polling 또는 만료 작업에서 `expired`로 전이한다.

## draft 변경과 개별 결정

v2의 AI 폼 변경 target은 `draft_form`뿐이다. Django는 검증된 모든 form action을 `proposal`로 정규화하고 `action_id`와 change별 `change_id`를 부여한다. 모든 change는 `pending`으로 시작하며 사용자가 별도 결정 endpoint로 승인하기 전에는 draft에도 적용하지 않는다. AI가 표시한 `user_requested`와 confidence는 설명·감사 메타데이터일 뿐 승인이나 적용 권한이 아니다.

하나의 action에 여러 change가 있어도 전체 승인·전체 거절하지 않는다. 각 change는 독립적으로 다음 endpoint를 사용한다.

```text
POST /api/v2/consultations/{session_id}/ai-actions/{action_id}/changes/{change_id}/approve/
POST /api/v2/consultations/{session_id}/ai-actions/{action_id}/changes/{change_id}/reject/
```

두 요청 모두 사용자 JWT와 `Idempotency-Key`가 필요하며 body는 비어 있다. 승인 성공 예시는 다음과 같다.

```json
{
  "contract_version": "2.0",
  "success": true,
  "data": {
    "action_id": "act_...",
    "change_id": "chg_...",
    "decision_status": "approved",
    "action_status": "partially_resolved",
    "change": {
      "section": "basic_info",
      "path": "monthly_income",
      "operation": "set",
      "value": 3000000
    }
  }
}
```

- change는 `pending → approved | rejected` 단방향이다.
- action 상태는 모든 change가 pending이면 `pending`, 일부만 결정됐으면 `partially_resolved`, pending이 없으면 `resolved`다.
- Frontend는 승인 응답에 포함된 단일 change만 공통 resolver로 draft에 적용한다. 거절은 draft를 바꾸지 않는다.
- 같은 결정을 재전송하면 최초 결과와 `200`을 반환한다. 반대 결정을 보내면 `409 ACTION_DECISION_CONFLICT`다.
- 개별 승인은 전체 폼의 저장 유효성을 보장하지 않는다. 사용자가 작성 완료를 누를 때 Django serializer가 현재 draft 전체를 다시 검증하고 성공한 경우에만 업무 DB에 저장한다.
- v2에는 AI action이 확정 재무정보를 직접 변경하는 endpoint가 없다.

## 상태·오류 코드

| HTTP | code | 의미·재시도 |
|---:|---|---|
| 400 | `REQUEST_INVALID` | 필드 누락·형식 오류, 자동 재시도 금지 |
| 401 | `AUTH_REQUIRED` | 사용자 인증 필요 |
| 404 | `SESSION_NOT_FOUND` | 없거나 소유하지 않은 상담 세션 |
| 404 | `AI_REQUEST_NOT_FOUND` | 없거나 소유하지 않은 AI 작업 |
| 404 | `ACTION_NOT_FOUND` | 없거나 소유하지 않은 action/change |
| 409 | `SESSION_CLOSED` | 종료된 상담에 메시지 접수 불가 |
| 409 | `IDEMPOTENCY_CONFLICT` | 같은 키에 다른 payload |
| 409 | `ACTION_DECISION_CONFLICT` | 이미 반대 결정으로 종결된 change |
| 422 | `CONTRACT_VERSION_UNSUPPORTED` | `2.0`이 아닌 비동기 payload |
| 429 | `RATE_LIMITED` | `Retry-After` 이후 재시도 가능 |
| 502 | `AI_DISPATCH_FAILED` | AI 접수 실패, 사용자 새 요청 가능 |
| 503 | `AI_STATE_UNAVAILABLE` | 작업 상태 저장소 장애, 같은 polling 재시도 가능 |

`failed`·`expired` 작업의 도메인 오류는 polling의 `data.error`에 들어가며 HTTP 실패 envelope와 구분한다. 정상·누락·잘못된 자료형·권한·timeout·중복 응답은 [AI lifecycle v2 fixture](fixtures/ai-lifecycle/v2/lifecycle.json), draft 적용 형태는 [form action fixture](fixtures/ai-lifecycle/v2/form-actions.json)를 따른다.

## v2 전환 정책

- v2 배포 시 Frontend·Django·AI를 조정 배포하고 v1 route와 payload는 동시에 종료한다. v1/v2 병행 기간은 두지 않는다.
- 선택 필드 추가만 v2 호환 변경이다. 필수 여부, 이름, 자료형, 단위, 상태 의미, 인증 방식 변경은 다음 major 계약이다.
- breaking change는 모든 consumer 준비와 fixture 검증 후 다음 major로 한 번에 전환한다.
- rollback은 이전 계약을 병행하는 방식이 아니라 Frontend·Django·AI 전체 release를 함께 되돌리는 방식으로 수행한다.
- 계약 문서, schema, adapter, 공용 fixture, provider·consumer test를 같은 변경 단위에서 갱신한다.

## 남은 범위

전체 제품 API의 날짜·timezone 정책 적용 결과와 AI 상담 외 endpoint의 v2 전환은 별도 확인이 필요하다. AI 상담 상태 갱신은 v2에서 polling으로 동결하며 SSE 전환은 후속 breaking 또는 별도 endpoint 결정으로 다룬다.
