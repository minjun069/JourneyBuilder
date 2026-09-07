# AI 채팅 연동 명세 - Polling/Callback 방식

## 1. 배경 및 목적

프론트는 AI 서버(`ai-pjt`)를 직접 호출하지 않고 Django 백엔드만 호출합니다.
Django는 사용자 인증, 세션 검증, DB 저장, Redis 작업 상태 관리, AI 서버 호출을 담당합니다.
AI 서버는 LangGraph/LLM 처리를 수행한 뒤 완료 결과를 Django callback API로 전달합니다.

이 문서는 백엔드와 프론트가 맞춰야 할 API 계약을 정의합니다.

## 2. 아키텍처

```
Browser (front-pjt)
    | 1. POST /api/v1/consultations/{session_id}/messages/
    v
Django (backend-pjt)
    | - 사용자 JWT 인증
    | - 사용자 Message 저장
    | - request_id 생성
    | - Redis에 processing 상태 저장
    | 2. POST http://ai-pjt:8001/chat
    v
ai-pjt (FastAPI + LangGraph)
    | - LLM/RAG/input_assist 처리
    | 3. POST /api/ai/callback/
    v
Django (backend-pjt)
    | - Assistant Message 저장
    | - Redis에 completed/failed 상태 저장
    ^
    | 4. GET /api/v1/consultations/{session_id}/ai-responses/{request_id}/
Browser (front-pjt)
```

- `ai-pjt`는 docker-compose에서 외부 `ports`를 열지 않고 내부 네트워크에만 연결합니다.
- 사용자의 JWT 인증은 Django에서만 수행합니다.
- Django -> ai-pjt 구간은 내부 네트워크 통신으로 보고 사용자 JWT를 전달하지 않습니다.
- AI 작업 상태는 Redis에 저장하고, 최종 채팅 메시지는 DB의 `Message`에 저장합니다.

## 3. 프론트 -> Django: 채팅 전송

### Endpoint

`POST /api/v1/consultations/{session_id}/messages/`

### Headers

`Authorization: Bearer <user JWT>`

### Request Body

```json
{
  "step_id": "basic-info",
  "content": "월급은 300만원이고 은퇴는 60세쯤 생각해요.",
  "form_data": {
    "age": 55,
    "monthly_income": 3000000,
    "retirement_age": 60
  }
}
```

| 필드 | 필수 | 설명 |
|---|---:|---|
| `content` | Y | 사용자가 채팅창에 입력한 메시지 |
| `step_id` | N | 현재 폼/프로세스 단계. 폼 맥락이 없는 일반 대화는 `general` 사용 |
| `form_data` | N | 사용자가 저장하지 않았지만 현재 화면에 입력 중인 폼 값 |

`form_data`는 AI 참고용 컨텍스트입니다. 이 값을 받았다는 이유만으로 Django가 DB에 저장하지 않습니다.
`step_id`를 생략하거나 빈 문자열로 보내면 Django가 `general`로 처리합니다.

일반 대화 예시:

```json
{
  "step_id": "general",
  "content": "은퇴 준비는 보통 몇 살부터 시작하는 게 좋아요?",
  "form_data": {}
}
```

### Response

```json
{
  "success": true,
  "data": {
    "message": {
      "id": 10,
      "role": "user",
      "content": "월급은 300만원이고 은퇴는 60세쯤 생각해요.",
      "extracted_data": null,
      "created_at": "2026-06-24T12:00:00+09:00"
    },
    "ai_request": {
      "request_id": "ai_req_6f9c4b7e6e984bb3a7c3df6d91e58c1a",
      "status": "processing"
    },
    "user_message_id": 10,
    "request_id": "ai_req_6f9c4b7e6e984bb3a7c3df6d91e58c1a",
    "status": "processing"
  }
}
```

`message`는 방금 DB에 저장된 사용자 메시지입니다.
`ai_request`는 이 메시지에 대한 AI 답변 생성 작업 정보입니다.
`request_id`는 Django가 생성하는 AI 작업 ID입니다. 프론트 polling, AI callback, Redis 상태 조회를 연결하는 기준값입니다.
`user_message_id`, `request_id`, `status`는 기존 프론트 코드 호환을 위해 함께 내려줄 수 있습니다.

## 4. 프론트 -> Django: AI 답변 polling

### Endpoint

`GET /api/v1/consultations/{session_id}/ai-responses/{request_id}/`

### 처리 중 응답

```json
{
  "success": true,
  "data": {
    "request_id": "ai_req_6f9c4b7e6e984bb3a7c3df6d91e58c1a",
    "status": "processing"
  }
}
```

### 완료 응답

```json
{
  "success": true,
  "data": {
    "request_id": "ai_req_6f9c4b7e6e984bb3a7c3df6d91e58c1a",
    "status": "completed",
    "message": {
      "id": 11,
      "role": "assistant",
      "content": "월 소득 300만원, 은퇴 희망 나이 60세로 이해했어요. 이대로 입력할까요?",
      "extracted_data": {
        "intent": "input_assist",
        "requires_approval": true,
        "pending_changes": [
          {
            "section": "basic_info",
            "field": "monthly_income",
            "value": 3000000,
            "label": "월 소득"
          }
        ]
      },
      "created_at": "2026-06-24T12:00:00+09:00"
    }
  }
}
```

### 실패 응답

```json
{
  "success": true,
  "data": {
    "request_id": "ai_req_6f9c4b7e6e984bb3a7c3df6d91e58c1a",
    "status": "failed",
    "error": "AI 응답 생성에 실패했습니다."
  }
}
```

## 5. Django -> ai-pjt: 작업 요청

### Endpoint

`POST http://ai-pjt:8001/chat`

실제 URL은 Django 환경변수 `AI_SERVICE_URL`로 관리합니다.

### Request Body

```json
{
  "request_id": "ai_req_6f9c4b7e6e984bb3a7c3df6d91e58c1a",
  "callback_url": "http://backend:8000/api/ai/callback/",
  "thread_id": "user-3-session-1",
  "session_id": 1,
  "step_id": "basic-info",
  "message": "월급은 300만원이고 은퇴는 60세쯤 생각해요.",
  "customer_info": {
    "saved_data": {
      "basic_info": null,
      "financial_assets": []
    },
    "current_form": {
      "age": 55,
      "monthly_income": 3000000,
      "retirement_age": 60
    }
  }
}
```

| 필드 | 설명 |
|---|---|
| `request_id` | Django가 생성한 AI 작업 ID |
| `callback_url` | AI 서버가 완료 결과를 보낼 Django callback URL |
| `thread_id` | LangGraph checkpointer 대화 히스토리 구분값 |
| `session_id` | Django 상담 세션 ID |
| `step_id` | 현재 폼/프로세스 단계. 일반 대화는 `general` |
| `message` | 사용자가 방금 입력한 채팅 |
| `customer_info` | Django DB 저장 데이터 + 프론트의 현재 폼 입력값 |

AI 서버는 요청을 받으면 `202 Accepted`로 즉시 응답하고, 실제 처리는 background task에서 수행합니다.

## 6. ai-pjt -> Django: callback

### Endpoint

`POST /api/ai/callback/`

이 API는 브라우저용이 아니라 AI 서버 내부 호출용입니다.

### 완료 요청

```json
{
  "request_id": "ai_req_6f9c4b7e6e984bb3a7c3df6d91e58c1a",
  "session_id": 1,
  "status": "completed",
  "thread_id": "user-3-session-1",
  "output": {
    "response": "월 소득 300만원, 은퇴 희망 나이 60세로 이해했어요. 이대로 입력할까요?",
    "intent": "input_assist",
    "update": {
      "monthly_income": 3000000,
      "retirement_age": 60
    }
  }
}
```

### 일반 QA 요청

```json
{
  "request_id": "ai_req_...",
  "session_id": 1,
  "status": "completed",
  "thread_id": "user-3-session-1",
  "output": {
    "response": "은퇴 희망 나이는 예상 생활비, 건강 상태, 국민연금 수령 시점 등을 함께 고려해 정하면 좋습니다.",
    "intent": "qa",
    "update": {}
  }
}
```

### 실패 요청

```json
{
  "request_id": "ai_req_...",
  "session_id": 1,
  "status": "failed",
  "error_code": "AI_PROCESSING_FAILED"
}
```

## 7. input_assist 승인/거절

AI가 폼 입력값을 추출해도 바로 DB에 저장하지 않습니다.
`Message.extracted_data.pending_changes`에 저장한 뒤 사용자가 승인할 때만 반영합니다.

### 승인

`POST /api/v1/consultations/{session_id}/ai-actions/approve/`

```json
{
  "message_id": 11
}
```

### 거절

`POST /api/v1/consultations/{session_id}/ai-actions/reject/`

```json
{
  "message_id": 11
}
```

프론트는 `intent=input_assist`이고 `requires_approval=true`인 메시지에만 승인/거절 UI를 표시합니다.

## 8. 환경변수

### Django

| 변수 | 예시 | 설명 |
|---|---|---|
| `AI_SERVICE_URL` | `http://ai-pjt:8001/chat` | Django가 AI 서버에 작업을 요청할 URL |
| `AI_CALLBACK_URL` | `http://backend:8000/api/ai/callback/` | AI 서버에 전달할 callback URL |
| `REDIS_URL` | `redis://redis:6379/0` | AI 작업 상태 저장용 Redis |
| `AI_CALLBACK_SECRET` | `...` | 선택. 설정 시 callback 요청의 `X-Internal-Secret` 검증 |

### ai-pjt

| 변수 | 예시 | 설명 |
|---|---|---|
| `AI_CALLBACK_SECRET` | `...` | 선택. Django callback에 보낼 내부 시크릿 |
| `AI_CALLBACK_TIMEOUT_SECONDS` | `10` | Django callback 요청 제한 시간 |

## 9. 백엔드 구현 체크리스트

- [x] Redis 연결 설정
- [x] `request_id` 생성
- [x] 메시지 POST 시 사용자 메시지 DB 저장
- [x] Redis에 `processing` 상태 저장
- [x] Django -> ai-pjt `/chat` 요청
- [x] polling GET API 구현
- [x] `/api/ai/callback/` 구현
- [x] callback 완료 시 assistant 메시지 DB 저장
- [x] Redis에 `completed`/`failed` 상태 저장
- [x] `input_assist` approve/reject API 구현
