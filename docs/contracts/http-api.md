# Django HTTP API 계약

상태: **AI 상담 관련 목표 계약 작성 — 나머지 HTTP API 미정**

## 공통 규칙

- Browser는 `/api/v1` 아래 Django API만 호출한다.
- 사용자 API는 JWT 인증과 상담 세션 소유권을 검증한다.
- AI 서버와 MCP 서버를 Browser에 직접 노출하지 않는다.
- 금액은 저장·AI canonical payload에서 원 단위 정수를 사용한다. 화면 단위 변환은 Frontend adapter가 담당한다.
- 날짜·시간의 최종 형식과 timezone 정책은 제품 전체 계약에서 추가 확정한다.
- 오류 응답은 최소 `success: false`, 안정적인 `code`, 사용자용 `message`를 가진다. 내부 stack과 비밀은 포함하지 않는다.

## 엔드포인트

### 사용자 메시지 전송

`POST /api/v1/consultations/{session_id}/messages/`

```json
{
  "content": "월급은 세후 300만 원이에요.",
  "step_id": "basic-info",
  "form_data": {}
}
```

- `content`: 필수, 1~2000자
- `step_id`: 실제 화면 단계 ID. 폼 맥락이 없으면 `general`
- `form_data`: 현재 화면의 미저장 폼. 필드 구조는 단계 계약을 따른다.
- 필드명은 `form_data`로 고정한다. `form` 별칭을 계약으로 유지하지 않는다.

접수 성공은 `202 Accepted`와 다음 데이터를 반환한다.

```json
{
  "success": true,
  "data": {
    "user_message_id": 10,
    "request_id": "ai_req_...",
    "status": "processing"
  }
}
```

### AI 결과 polling

`GET /api/v1/consultations/{session_id}/ai-responses/{request_id}/`

- `processing`: request ID와 상태를 반환한다.
- `completed`: 저장된 assistant message와 구조화된 인용·변경 제안을 반환한다.
- `failed`: 안정적인 오류 코드와 사용자 메시지를 반환한다.
- session·user가 request와 일치하지 않으면 `403` 또는 `404`로 거부한다.

### 미저장 draft action

`target=draft_form`인 action은 Frontend의 현재 폼에만 적용한다. `direct`는 즉시 적용하고 `proposal`은 사용자가 승인한 뒤 같은 resolver로 적용한다. 둘 다 DB 저장이 아니므로 Backend 승인 API를 호출하지 않으며, 이후 사용자가 폼 저장 API를 호출할 때 serializer 검증을 통과해야 영속화된다.

응답 `step_id`가 현재 화면과 다르거나 안정 ID 대상을 찾을 수 없으면 direct를 proposal로 낮춘다.

### 영속 변경 제안 승인·거절

```text
POST /api/v1/consultations/{session_id}/ai-actions/approve/
POST /api/v1/consultations/{session_id}/ai-actions/reject/
```

```json
{
  "message_id": 11
}
```

- Django는 저장된 제안을 현재 schema와 세션 상태로 재검증한다.
- 승인 시 transaction 안에서 한 번만 적용하고 완료 상태를 기록한다.
- 거절 시 데이터는 변경하지 않고 거절 상태만 기록한다.
- 이미 승인·거절된 제안의 중복 요청은 `409`로 거부한다.
- 저장된 고객 데이터를 바꾸는 action은 Frontend 로컬 변경으로 대체하거나 Backend 승인 API를 생략하지 않는다.

## 호환성 정책

- AI 상담 계약 변경은 [`서비스 계약 변경·인수인계`](../engineering/contract-handoff.md)를 따른다.
- `form`→`form_data`처럼 현재 구현과 목표 계약이 다른 항목은 양쪽 담당자가 같은 fixture와 E2E로 전환을 확인한다.
- 필드 추가는 기본값·미지원 consumer 동작을 정의해야 한다.
- 필드 삭제·이름·자료형·단위 변경은 breaking change로 취급하고 migration·rollback을 포함한다.
- 계약 문서, serializer/schema, Frontend adapter, provider·consumer test를 같은 변경 단위에서 갱신한다.

## 열린 결정

- 전체 API의 공통 오류 envelope와 code 목록
- polling 유지 기간과 향후 SSE 전환 여부
- 제안이 여러 개인 메시지의 action 식별 방식
- API 버전 상승과 폐기 공지 기간
