# Django HTTP API 계약

상태: **Browser↔Django AI 상담 SSE와 Frontend 로컬 제안 결정 계약 동결 — 나머지 HTTP API 미정**

이 문서는 Browser↔Django 메시지 SSE와 Frontend 로컬 change 결정 경계를 소유한다. Django↔AI 서비스 경계는 [AI 서비스 API](ai-api.md), canonical 필드 의미와 데이터 소유권은 [데이터 스키마](../data/schema.md), 동결 계약과 아직 다른 실제 구현은 [현재 계약 불일치 조사](current-state.md)를 따른다.

일반 AI 상담, Elicit 전체 대화와 Error Review의 응답 채널은 ADR-0002~ADR-0005에
따라 v2 SSE로 통일한다. 스트리밍 endpoint와 이벤트·멱등성 계약은
[AI 상담 SSE v2 계약](sse-v2.md)이 소유한다. 비스트리밍 메시지 POST와 AI 결과
polling endpoint는 제공하지 않는다.

## 공통 규칙

- Browser는 `/api/v2` 아래 Django API만 호출한다. AI 서버와 MCP 서버는 직접 호출하지 않는다.
- Function Calling 도구는 AI 프로세스 내부에서만 실행하며 별도 HTTP endpoint로 공개하지 않는다.
- 사용자 API는 JWT 인증과 상담 세션 소유권을 검증한다. 인증되지 않은 요청은 `401`, 인증됐지만 소유하지 않은 세션·request는 존재 여부를 숨기기 위해 `404`로 응답한다.
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

## SSE 메시지 접수

`POST /api/v2/consultations/{session_id}/messages/stream/`

필수 header:

```text
Authorization: Bearer <user-jwt>
Idempotency-Key: <client-generated-uuid>
Accept: text/event-stream
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

Elicit의 UI 동의 `start`, QA 뒤 `resume`, 활성 질문 continuation과 Error Review의
`save`·`user_request`도 이 endpoint를 사용한다.
Elicit `start`·`resume`은 `elicit_action`, Error Review는
`request_mode=error_review`와 `review_trigger`를 같은 SSE 요청 body에 포함한다.

같은 사용자·세션에서 같은 `Idempotency-Key`와 같은 payload를 다시 보내면 완료된
terminal receipt를 사용해 `start -> done | error`를 재생한다. 같은 키의 payload가
다르면 `409 IDEMPOTENCY_CONFLICT`, 아직 실행 중이면 `409 AI_STREAM_IN_PROGRESS`다.
receipt와 payload digest는 24시간 보존한다.

## draft 변경과 Frontend 로컬 결정

v2의 AI 폼 변경 target은 `draft_form`뿐이다. Django는 검증된 모든 form action을 `proposal`로 정규화하고 표시용 `action_id`와 change별 `change_id`, 초기 `pending` 상태를 부여한다. AI가 표시한 `user_requested`와 confidence는 설명 메타데이터일 뿐 승인이나 적용 권한이 아니다.

하나의 action에 여러 change가 있어도 사용자는 각 change를 독립적으로 승인하거나 거절한다. 이 결정은 Frontend 메모리에서만 처리하며 v1·v2 어느 경로에도 Change Decision 요청을 보내지 않는다.

- 승인: SSE 완료 메시지에 이미 포함된 해당 change를 공통 resolver로 현재 draft에 즉시 적용하고 control을 종료한다.
- 거절: draft를 변경하지 않고 해당 proposal control만 종료한다.
- `pending → approved | rejected`와 action의 `pending | partially_resolved | resolved`는 화면 표시용 로컬 상태다. 서버에 전송·저장·재생하지 않으므로 결정용 멱등성 키나 충돌 응답도 없다.
- 승인·거절은 전체 폼의 저장 유효성이나 DB 저장 완료를 뜻하지 않는다. 사용자가 작성 완료를 누를 때 기존 폼 저장 API와 Django serializer가 현재 draft 전체를 다시 검증하고 성공한 경우에만 업무 DB에 저장한다.
- v1 일괄 action 결정 route와 v2 change 결정 route는 제공하지 않으며 요청 시 `404`다.

## 상태·오류 코드

| HTTP | code | 의미·재시도 |
|---:|---|---|
| 400 | `REQUEST_INVALID` | 필드 누락·형식 오류, 자동 재시도 금지 |
| 401 | `AUTH_REQUIRED` | 사용자 인증 필요 |
| 404 | `SESSION_NOT_FOUND` | 없거나 소유하지 않은 상담 세션 |
| 409 | `SESSION_CLOSED` | 종료된 상담에 메시지 접수 불가 |
| 409 | `IDEMPOTENCY_CONFLICT` | 같은 키에 다른 payload |
| 422 | `CONTRACT_VERSION_UNSUPPORTED` | `2.0`이 아닌 SSE payload |
| 429 | `RATE_LIMITED` | `Retry-After` 이후 재시도 가능 |
| 502 | `AI_DISPATCH_FAILED` | AI SSE 연결 실패, 사용자 새 요청 가능 |

SSE terminal 오류는 `error` event의 `data.error`에 들어가며 응답 header 이전 HTTP 실패
envelope와 구분한다. 정상·누락·잘못된 자료형·권한·timeout·중복 응답은 저장소의
`contract-fixtures/ai-lifecycle/v2/lifecycle.json`, draft 적용 형태는
`contract-fixtures/ai-lifecycle/v2/form-actions.json`을 따른다.

## v2 전환 정책

- v2 배포 시 Frontend·Django·AI를 조정 배포하고 v1 route와 payload는 동시에 종료한다. v1/v2 병행 기간은 두지 않는다.
- 선택 필드 추가만 v2 호환 변경이다. 필수 여부, 이름, 자료형, 단위, 상태 의미, 인증 방식 변경은 다음 major 계약이다.
- breaking change는 모든 consumer 준비와 fixture 검증 후 다음 major로 한 번에 전환한다.
- rollback은 이전 계약을 병행하는 방식이 아니라 Frontend·Django·AI 전체 release를 함께 되돌리는 방식으로 수행한다.
- 계약 문서, schema, adapter, 공용 fixture, provider·consumer test를 같은 변경 단위에서 갱신한다.

## 남은 범위

전체 제품 API의 날짜·timezone 정책 적용 결과와 AI 상담 외 endpoint의 v2 전환은
별도 확인이 필요하다. 일반 상담, Elicit 전체 대화와 Error Review는 v2 SSE
endpoint만 사용하며 비스트리밍 접수·polling·HTTP callback은 제거됐다.
