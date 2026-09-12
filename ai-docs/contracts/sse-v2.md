# AI 상담 SSE v2 계약

상태: **ADR-0002·ADR-0003에 따라 Browser↔Django↔AI 스트리밍 계약 동결**

이 문서는 일반 AI 상담의 v2 SSE endpoint, 이벤트 상태 머신, 인증, 멱등성,
오류와 저장 시점을 소유한다. Elicit의 `start`·`resume`·활성 continuation도 일반
상담과 같은 SSE 경로를 사용한다. callback + polling 계약은 Error Review와 명시적
장애 전환 경로로 유지하며 장기 작업은 RabbitMQ 작업 계약에서 별도로 다룬다.

기계 판독 가능한 단일 예시는
`contract-fixtures/ai-lifecycle/v2/sse.json`이다.

## Browser → Django

```text
POST /api/v2/consultations/{session_id}/messages/stream/
Authorization: Bearer <user-jwt>
Idempotency-Key: <client-generated-uuid>
Accept: text/event-stream
Content-Type: application/json
```

요청 body는 기존 v2 메시지 접수와 같다.

```json
{
  "contract_version": "2.0",
  "content": "월급은 세후 300만 원이에요.",
  "step_id": "basic-info",
  "form_data": {}
}
```

Elicit 시작과 재개도 같은 endpoint를 사용한다. 시작은
`step_id=financial-mindset`, `request_mode=chat`, `elicit_action=start`, 재개는
`elicit_action=resume`을 보낸다. 활성 Elicit 질문의 일반 답변은
`elicit_action`을 생략하며 checkpoint 상태가 continuation으로 라우팅한다.
Frontend는 세 경우 모두 callback + polling endpoint를 호출하지 않는다.

Django는 응답 header를 보내기 전에 사용자 JWT, 상담 세션 소유권, 종료 상태,
body, contract version과 idempotency key를 검증한다. 실패하면 기존 v2 JSON 오류
envelope와 적절한 HTTP status를 반환한다.

## Django → AI

```text
POST /v2/chat/stream
Authorization: Bearer <AI_SERVICE_BEARER_TOKEN>
Accept: text/event-stream
Content-Type: application/json
```

요청 body는 기존 `POST /v2/chat`의 `ChatV2Request`와 동일하다. 임의 callback URL은
받지 않는다. SSE 연결의 종료가 이 실행의 수명주기다.

## 응답 공통 규칙

```text
Content-Type: text/event-stream; charset=utf-8
Cache-Control: no-cache
X-Accel-Buffering: no
```

- 이벤트는 UTF-8이며 빈 줄로 끝난다.
- 각 `data`는 한 줄 JSON object다.
- 모든 이벤트는 `contract_version: "2.0"`과 같은 `request_id`를 가진다.
- 정상 상태 머신은 `start -> delta* -> done`이다.
- 실패 상태 머신은 `start -> delta* -> error`다.
- `done`과 `error`는 terminal event이며 둘 중 하나만 보낸다.
- 연결 EOF 자체는 완료가 아니다. terminal event가 없으면 client는 실패로 처리한다.

## AI → Django 내부 이벤트

### start

```text
event: start
data: {"contract_version":"2.0","request_id":"ai_req_...","session_id":1,"thread_id":"user-3-session-1"}

```

### delta

```text
event: delta
data: {"contract_version":"2.0","request_id":"ai_req_...","content":"월급 정보를 "}

```

공개 최종 답변 node의 content만 전달한다. 내부 라우팅, 검색 판단과 구조화 출력
token은 전달하지 않는다.

### done

`done.data`는 callback의 `CompletedCallback`과 같은 terminal payload다.

```text
event: done
data: {"contract_version":"2.0","request_id":"ai_req_...","session_id":1,"thread_id":"user-3-session-1","status":"completed","completed_at":"2026-09-08T00:00:01Z","output":{"response":"월급 정보를 확인했습니다.","step_id":"basic-info","action":null}}

```

### error

`error.data`는 callback의 `FailedCallback`과 같은 terminal payload다.

```text
event: error
data: {"contract_version":"2.0","request_id":"ai_req_...","session_id":1,"thread_id":"user-3-session-1","status":"failed","completed_at":"2026-09-08T00:00:01Z","error":{"code":"INTERNAL_ERROR","message":"AI 응답 생성에 실패했습니다.","retryable":true}}

```

## Django → Browser 외부 이벤트

Django는 내부 식별자와 terminal payload를 검증한 뒤 Browser용 이벤트로 변환한다.

- `start`에는 저장된 `user_message_id`를 추가한다.
- `delta`에는 사용자에게 보여줄 content만 전달한다.
- `done` 전에 Assistant 메시지를 한 번 저장하고 직렬화된 `message`를 전달한다.
- `error.error`는 기존 v2 `{code,message,retryable}` 구조를 사용한다.

```text
event: done
data: {"contract_version":"2.0","request_id":"ai_req_...","status":"completed","message":{"id":42,"role":"assistant","content":"월급 정보를 확인했습니다.","extracted_data":{"action":null}}}

```

`message.extracted_data.action`이 존재하면 Django가 발급한 `action_id`, 각
`change_id`, `pending` 결정 상태를 포함한다. Browser는 기존 v2 change별 승인·거절
endpoint를 그대로 사용한다.

## 멱등성

- 범위는 사용자 ID, 상담 세션 ID와 `Idempotency-Key`의 조합이다.
- 같은 key와 다른 body digest는 `409 IDEMPOTENCY_CONFLICT`다.
- 같은 key의 stream이 처리 중이면 `409 AI_STREAM_IN_PROGRESS`이며
  `retryable: true`다. 새 사용자 요청을 생성하지 않는다.
- 같은 key가 이미 완료됐으면 AI를 재실행하지 않고 저장된 결과로
  `start -> done`을 재생한다.
- 실패 또는 연결 취소 후 재시도는 새 idempotency key를 사용하는 새 요청이다.
- idempotency record와 terminal 결과는 24시간 보존한다.

## 저장과 취소

- 사용자 메시지는 FastAPI 연결 전에 저장한다.
- Assistant 메시지는 검증된 `done`에서 한 번만 저장한다.
- `error`, protocol 위반, terminal event 없는 EOF와 client 취소에서는 부분
  Assistant 메시지를 저장하지 않는다.
- Browser 연결이 닫히면 Django upstream client를 닫고 FastAPI async generator에
  취소를 전파한다.
- 자동 재연결과 `Last-Event-ID` replay는 이번 계약 범위가 아니다.

## timeout

- Django→AI connect/write/pool timeout: 5초
- stream read idle timeout: 60초
- Gateway Nginx read/send timeout: 130초
- Host Nginx read/send timeout: 140초

장기 작업 deadline은 SSE timeout을 늘려 해결하지 않고 RabbitMQ 작업으로 분리한다.

## 전환

일반 채팅과 Elicit `start`·`resume`·continuation은 Frontend 기본 SSE 경로를
사용한다. 기존 v2 callback + polling은 Error Review와 명시적 fallback에만
유지하며 Elicit 요청에는 사용하지 않는다. v1 SSE는 제거 대상으로 표시하며 신규
기능을 추가하지 않는다.
