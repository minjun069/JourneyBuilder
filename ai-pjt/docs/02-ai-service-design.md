# AI 서비스 설계 문서

## 1. 역할

`ai-pjt`는 은퇴 설계 AI 상담 전용 FastAPI 서버입니다.
사용자 인증, 세션 검증, DB 저장, Redis 작업 상태 관리는 Django 백엔드가 담당합니다.
`ai-pjt`는 Django가 넘겨준 `message`, `step_id`, `customer_info`를 바탕으로 LLM/LangGraph 처리를 수행하고, 완료 결과를 Django callback API로 전달합니다.

## 2. 아키텍처

```
Browser (front-pjt)
    | POST /api/v1/consultations/{session_id}/messages/
    v
Django (backend-pjt)         <- 인증, DB 저장, Redis 상태 관리, customer_info 조합
    | POST http://ai-pjt:8001/chat
    v
ai-pjt (FastAPI + LangGraph)
    | - thread_id로 PostgreSQL checkpointer 히스토리 관리
    | - QA/RAG/input_assist 등 agent 실행
    | POST /api/ai/callback/
    v
Django (backend-pjt)
```

- `ai-pjt`는 docker-compose에서 `ports` 매핑 없이 내부 네트워크에만 연결합니다.
- 브라우저는 `ai-pjt`를 직접 호출하지 않습니다.
- Django -> ai-pjt 구간에는 사용자 JWT를 전달하지 않습니다.

## 3. 엔드포인트

| Method | Path | 설명 |
|---|---|---|
| POST | `/chat` | AI 작업 요청 수신 후 background task 등록 |
| GET | `/health` | 컨테이너 헬스체크 |

## 4. `/chat` 요청 스키마

Django -> ai-pjt 요청 body:

```json
{
  "request_id": "ai_req_6f9c4b7e6e984bb3a7c3df6d91e58c1a",
  "callback_url": "http://backend:8000/api/ai/callback/",
  "thread_id": "user-3-session-1",
  "session_id": 1,
  "step_id": "basic-info",
  "message": "이 정도 자산으로 은퇴 가능할까요?",
  "customer_info": {
    "saved_data": {
      "basic_info": {
        "age": 55,
        "monthly_income": 3000000
      },
      "financial_assets": []
    },
    "current_form": {
      "retirement_age": 60
    }
  }
}
```

| 필드 | 출처 | 설명 |
|---|---|---|
| `request_id` | Django | AI 작업 식별자. callback 때 그대로 반환 |
| `callback_url` | Django | 완료 결과를 보낼 Django callback URL |
| `thread_id` | Django | LangGraph checkpointer 히스토리 구분값 |
| `session_id` | Django | 상담 세션 ID |
| `step_id` | Django/Front | 현재 재무 진단 단계. 폼 맥락 없는 일반 대화는 `general` |
| `message` | Front | 사용자가 방금 입력한 메시지 |
| `customer_info` | Django 조합 | DB 저장 데이터 + 현재 화면의 미저장 폼 값 |

`/chat`은 요청을 받으면 `202 Accepted`로 즉시 응답합니다. 최종 답변은 HTTP 응답으로 직접 돌려주지 않고 callback으로 전달합니다.

## 5. customer_info 조합 규칙

Django가 다음 값을 합쳐서 `customer_info`를 구성합니다.

- `saved_data`: DB에 이미 저장된 상담 데이터
- `current_form`: 사용자가 현재 화면에 입력 중이지만 아직 저장하지 않은 폼 값

`ai-pjt`는 두 값을 구분해서 받을 수 있지만, 최종 판단에서는 모두 AI context로 사용합니다.

## 6. 채팅 히스토리 관리

LangGraph PostgreSQL Checkpointer를 사용합니다.

- `thread_id`를 기준으로 대화 히스토리를 저장/로드합니다.
- Django는 사용자 메시지를 서비스 DB에 저장하고, `ai-pjt`는 LangGraph 실행 히스토리를 checkpointer에 저장합니다.
- 매 요청마다 현재 입력인 `message` 하나만 전달하면 LangGraph가 이전 히스토리를 조합합니다.

Redis checkpointer로 전환하려면 `core/checkpointer.py`와 `requirements.txt`의 주석을 참고합니다.

## 7. LangGraph 상태 구조

```python
class ChatState(TypedDict):
    message: str
    step_id: str
    customer_info: dict
    next: str
    output: dict
```

agent는 최종적으로 `output`을 반환합니다.

QA 계열 예시:

```json
{
  "response": "은퇴 희망 나이는 예상 생활비와 국민연금 수령 시점을 함께 고려해 정하면 좋습니다.",
  "update": {}
}
```

input_assist 계열 예시:

```json
{
  "response": "월 소득 300만원, 은퇴 희망 나이 60세로 이해했어요. 이대로 입력할까요?",
  "update": {
    "monthly_income": 3000000,
    "retirement_age": 60
  }
}
```

## 8. callback 응답 스키마

AI 처리가 완료되면 `callback_url`로 POST합니다.

### QA

```json
{
  "request_id": "ai_req_...",
  "session_id": 1,
  "status": "completed",
  "thread_id": "user-3-session-1",
  "output": {
    "response": "은퇴 희망 나이는 예상 생활비와 국민연금 수령 시점을 함께 고려해 정하면 좋습니다.",
    "intent": "qa",
    "update": {}
  }
}
```

### input_assist

```json
{
  "request_id": "ai_req_...",
  "session_id": 1,
  "status": "completed",
  "thread_id": "user-3-session-1",
  "output": {
    "response": "월 소득 300만원, 은퇴 희망 나이 60세로 이해했어요. 이대로 입력할까요?",
    "intent": "input_assist",
    "update": {
      "monthly_income": 3000000
    }
  }
}
```

### 실패

```json
{
  "request_id": "ai_req_...",
  "session_id": 1,
  "status": "failed",
  "error_code": "AI_PROCESSING_FAILED"
}
```

## 9. intent 규칙

| intent | 의미 | 승인 필요 |
|---|---|---|
| `qa` | 사용자의 질문에 답변 | N |
| `input_assist` | 폼 입력값 추출/정리 제안 | Y |
| `clarification` | 추가 질문 필요 | N |
| `validation_warning` | 입력값 이상 경고 | N |
| `fallback` | 기타 안내 | N |

처음 구현에서는 `qa`와 `input_assist`를 우선 지원합니다.

## 10. 환경변수

| 변수 | 기본값 | 설명 |
|---|---|---|
| `OPENAI_API_KEY` | - | LLM API 키 |
| `LLM_BASE_URL` | GMS 게이트웨이 URL | OpenAI 호환 엔드포인트 |
| `LLM_MODEL` | `gpt-4o-mini` | 사용할 모델명 |
| `DATABASE_URL` | `postgresql://...` | Checkpointer용 PostgreSQL |
| `REDIS_URL` | `redis://redis:6379` | Redis 전환 시 사용 |
| `AI_CALLBACK_SECRET` | - | 선택. callback 요청에 포함할 내부 시크릿 |
| `AI_CALLBACK_TIMEOUT_SECONDS` | `10` | Django callback 요청 제한 시간 |
