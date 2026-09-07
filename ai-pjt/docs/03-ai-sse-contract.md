# AI 채팅 SSE 이벤트 계약

## 1. 목적과 범위

이 문서는 Django가 FastAPI AI 서비스의 답변을 실시간으로 중계할 때 사용하는 최소 SSE 계약을 정의합니다.
브라우저는 AI 서비스를 직접 호출하지 않으며, Django가 인증·상담 세션 검증·메시지 저장을 담당합니다.

이번 범위는 FastAPI의 `POST /chat/stream`까지입니다. Django 프록시와 프런트엔드 스트림 수신은 다음 작업에서 구현합니다.
기존 비동기 callback 엔드포인트인 `POST /chat`은 그대로 유지합니다.

## 2. 요청

### Endpoint

`POST /chat/stream`

### Headers

```http
Content-Type: application/json
Accept: text/event-stream
X-Internal-Secret: <internal secret>
```

### Body

```json
{
  "request_id": "ai_req_6f9c4b7e6e984bb3a7c3df6d91e58c1a",
  "session_id": 1,
  "thread_id": "user-3-session-1",
  "step_id": "basic-info",
  "message": "월급은 300만원이에요.",
  "customer_info": {
    "saved_data": {},
    "current_form": {"monthly_income": 3000000}
  },
  "field_registry": []
}
```

`callback_url`은 스트림 요청에 사용하지 않습니다. 스트림의 생명주기는 HTTP 연결과 동일합니다.

## 3. 응답 헤더

```http
Content-Type: text/event-stream; charset=utf-8
Cache-Control: no-cache
X-Accel-Buffering: no
```

각 이벤트는 빈 줄로 끝나며, `data`는 한 줄 JSON입니다.

## 4. 이벤트

정상 흐름은 `start -> delta(1회 이상) -> done`입니다.

### start

요청 검증이 끝나고 스트림 처리를 시작했음을 알립니다.

```text
event: start
data: {"request_id":"ai_req_...","session_id":1}

```

### delta

사용자에게 보여줄 최종 답변의 일부입니다. 라우팅, 검색 판단, 품질 검증 등 내부 LLM 출력은 전달하지 않습니다.

```text
event: delta
data: {"request_id":"ai_req_...","content":"은퇴 준비는 "}

```

모델 또는 공급자가 토큰 스트림을 지원하지 않으면 완성된 답변 전체가 한 번의 `delta`로 전달될 수 있습니다.

### done

정상 완료 이벤트입니다. `output.response`는 전체 답변이며 `output.action` 등 구조화된 결과도 함께 전달합니다.

```text
event: done
data: {"request_id":"ai_req_...","session_id":1,"thread_id":"user-3-session-1","output":{"response":"은퇴 준비는 일찍 시작할수록 좋습니다.","action":null}}

```

`done`을 받은 Django는 누적 답변과 구조화 결과를 검증한 뒤 Assistant 메시지를 한 번만 저장합니다.

### error

스트림 도중 실패한 경우의 종료 이벤트입니다. 내부 예외나 스택 트레이스는 노출하지 않습니다.

```text
event: error
data: {"request_id":"ai_req_...","code":"AI_STREAM_FAILED","message":"AI 응답 생성에 실패했습니다."}

```

`error` 이후에는 `done`을 보내지 않습니다.

## 5. 연결 및 보안 규칙

- `AI_CALLBACK_SECRET`이 설정되어 있으면 `X-Internal-Secret`을 상수 시간 비교로 검증합니다.
- 클라이언트 연결이 끊기면 실행 중인 스트림을 취소하고 `error` 이벤트를 만들기 위해 추가 작업을 시작하지 않습니다.
- SSE 응답은 캐시하거나 프록시에서 버퍼링하지 않습니다.
- 하나의 요청에서는 `done` 또는 `error` 중 하나만 최종 이벤트로 전송합니다.
- `request_id`와 `thread_id`는 Django가 생성하며 FastAPI에서 새로 만들지 않습니다.

## 6. 구현 완료 기준

- 사용자에게 표시할 최종 LLM 노드의 토큰만 `delta`로 전달한다.
- 내부 라우터와 구조화 출력 토큰은 외부 스트림에 포함되지 않는다.
- 스트리밍 미지원 모델에서도 `start -> delta -> done` 계약을 지킨다.
- 실패 응답에 내부 오류 상세가 포함되지 않는다.
- 기존 `POST /chat` callback 처리가 그대로 동작한다.
