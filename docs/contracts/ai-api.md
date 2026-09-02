# AI 서비스 API 계약

상태: **3주 MVP 목표 계약 — D1 합동 검토 후 필드 동결**

## 호출 주체와 책임

Browser는 AI 서버를 직접 호출하지 않는다. Django가 사용자 인증, 상담 세션 소유권, 저장 데이터 조회, 현재 폼 조합, 작업 상태, 사용자 승인과 영속화를 담당한다.

AI 서비스는 Django가 전달한 snapshot을 바탕으로 6-Agent를 실행하고 자연어 답변, 인용, 변경 제안, 분석 결과를 callback한다. AI 서비스는 Django 업무 DB에 직접 쓰지 않는다.

| 주체 | 책임 |
|---|---|
| Frontend | 메시지·현재 단계·현재 폼 전달, 처리 상태·인용·변경 제안 표시, 승인·거절 입력 |
| Django | 인증·인가, request/thread/session 결합, canonical customer snapshot, 작업 상태, callback 검증, 변경안 재검증·승인 후 저장 |
| AI | 문맥 복원, Agent 라우팅·실행, RAG·MCP 호출, 구조화 출력·보안 검사, 성공·실패 callback |
| MCP | 허용 도구 실행, 스키마·권한·timeout·감사 결과 반환 |

## 요청·응답

### Django → AI 작업 요청

`POST /chat`은 작업을 접수하고 `202 Accepted`를 반환한다.

서비스 간 요청은 MVP에서 `X-Internal-Secret`을 검증한다. 운영 인증 방식이 바뀌면 이 계약과 [`AI 보안 정책`](../security/ai-security-policy.md)을 함께 변경한다.

```json
{
  "request_id": "ai_req_...",
  "callback_url": "http://backend:8000/api/ai/callback/",
  "thread_id": "user-3-session-1",
  "session_id": 1,
  "step_id": "basic-info",
  "message": "월급은 세후 300만 원이에요.",
  "customer_info": {
    "saved_data": {},
    "current_form": {}
  },
  "chart_context": null
}
```

| 필드 | 필수 | 계약 |
|---|---:|---|
| `request_id` | Y | Django가 생성하는 작업·idempotency 식별자 |
| `callback_url` | Y | 허용된 내부 callback 주소. 임의 외부 URL 금지 |
| `thread_id` | Y | 사용자와 상담 세션을 함께 식별하며 소유권 검증 후 생성 |
| `session_id` | Y | Django 상담 세션 ID |
| `step_id` | Y | 프론트 실제 단계 ID. 미지원 값은 fallback 처리 |
| `message` | Y | 1~2000자의 현재 사용자 메시지 |
| `customer_info.saved_data` | Y | DB에 확정 저장된 canonical snapshot |
| `customer_info.current_form` | Y | 아직 저장되지 않은 현재 폼 snapshot |
| `chart_context` | N | 차트 분석 시 원본 수치·단위·계산 결과·기준 시점 |

### step_id 레지스트리

`step_id`의 기준 구현은 Frontend의 [`processSteps.js`](../../front-pjt/src/views/ai/data/processSteps.js)다. AI는 아래 23개 값만 현재 지원 값으로 인식하며, 그 밖의 값은 `fallback`으로 처리한다. 각 행의 Agent 목록은 라우터가 LLM에 제시할 allowlist다.

| 단계 ID | 허용 Agent |
|---|---|
| `intro` | `qa`, `fallback` |
| `collection` | `qa`, `fallback` |
| `basic-info` | `qa`, `input_assist`, `error_review`, `fallback` |
| `financial-mindset` | `qa`, `input_assist`, `elicit`, `error_review`, `fallback` |
| `financial-satisfaction` | `qa`, `input_assist`, `error_review`, `fallback` |
| `financial-info` | `qa`, `fallback` |
| `financial-assets` | `qa`, `input_assist`, `error_review`, `fallback` |
| `pension-assets` | `qa`, `input_assist`, `error_review`, `fallback` |
| `real-estate` | `qa`, `input_assist`, `error_review`, `fallback` |
| `liabilities` | `qa`, `input_assist`, `error_review`, `fallback` |
| `income` | `qa`, `input_assist`, `error_review`, `fallback` |
| `expenses` | `qa`, `input_assist`, `error_review`, `fallback` |
| `financial-goals` | `qa`, `input_assist`, `error_review`, `fallback` |
| `presentation` | `qa`, `fallback` |
| `balance-sheet` | `qa`, `chart_interpret`, `fallback` |
| `cash-flow` | `qa`, `chart_interpret`, `fallback` |
| `financial-indicators` | `qa`, `chart_interpret`, `fallback` |
| `financial-goal-result` | `qa`, `chart_interpret`, `fallback` |
| `retirement-fund` | `qa`, `chart_interpret`, `fallback` |
| `retirement-readiness` | `qa`, `chart_interpret`, `fallback` |
| `pension-schedule` | `qa`, `chart_interpret`, `fallback` |
| `lifetime-cashflow` | `qa`, `chart_interpret`, `fallback` |
| `required-savings` | `qa`, `chart_interpret`, `fallback` |

`elicit`은 `financial-mindset`에서만 허용한다. `financial-info`는 저장 대상이 없는 안내 단계이고, `presentation`은 특정 차트가 아닌 상위 단계이므로 각각 `input_assist`·`error_review`와 `chart_interpret`을 허용하지 않는다.

접수 응답:

```json
{
  "request_id": "ai_req_...",
  "status": "accepted"
}
```

### AI → Django 완료 callback

```json
{
  "request_id": "ai_req_...",
  "thread_id": "user-3-session-1",
  "session_id": 1,
  "status": "completed",
  "output": {
    "agent": "input_assist",
    "response": "월 소득 300만 원으로 이해했어요. 반영할까요?",
    "citations": [],
    "actions": [
      {
        "type": "form_update_proposal",
        "step_id": "basic-info",
        "changes": [
          {
            "section": "basic_info",
            "path": "monthly_income",
            "operation": "set",
            "value": 3000000,
            "value_type": "integer",
            "unit": "KRW",
            "label": "세후 월 소득",
            "reason": "사용자 발화에서 추출"
          }
        ],
        "requires_approval": true
      }
    ],
    "meta": {
      "prompt_version": "TODO",
      "model": "TODO",
      "knowledge_version": "TODO"
    }
  }
}
```

공통 출력 규칙:

- `agent`는 채택된 6개 Agent 이름 중 하나다.
- `response`는 사용자에게 표시할 텍스트다.
- `citations`는 QA·금융 근거 답변에서 문서 ID·제목·기준일·근거 구간을 가진다.
- `actions`는 실행 결과가 아니라 제안이다. Django가 schema·권한·현재 상태를 재검증한다.
- `meta`는 재현에 필요한 모델·프롬프트·지식 버전을 가진다.
- Agent별 추가 결과는 공통 envelope 안의 버전된 구조화 필드로 추가하며 임의 최상위 필드를 만들지 않는다.

필드·목록·중첩 구조는 [`데이터 스키마`](../data/schema.md)의 `FormUpdateProposal` 계약을 따른다.

## 오류와 재시도

실패 callback:

```json
{
  "request_id": "ai_req_...",
  "thread_id": "user-3-session-1",
  "session_id": 1,
  "status": "failed",
  "error": {
    "code": "LLM_TIMEOUT",
    "message": "AI 응답 생성 시간이 초과되었습니다.",
    "retryable": true
  }
}
```

- 지원 상태는 `accepted`, `processing`, `completed`, `failed`다.
- Django는 같은 `request_id`의 완료 callback을 한 번만 영속화한다.
- AI는 구조화 출력 파싱과 일시적 외부 오류만 제한적으로 재시도한다.
- callback timeout은 같은 payload와 `request_id`로 재시도할 수 있어야 한다.
- 인증 실패, schema 오류, 권한 거부, 미지원 도구는 자동 재시도하지 않는다.
- callback이 최종 실패하면 작업을 성공으로 보고하지 않고 운영 오류로 남긴다.
- 오류의 내부 stack·비밀·원본 금융정보는 사용자 메시지에 포함하지 않는다.

## 호환성과 인수인계

- 계약 변경은 [`서비스 계약 변경·인수인계`](../engineering/contract-handoff.md)를 따른다.
- 필수 필드 삭제·이름 변경·자료형 변경은 breaking change다.
- producer와 consumer가 동시에 배포되지 않으면 additive 변경과 양쪽 호환 기간을 둔다.
- AI·Backend는 같은 JSON fixture로 provider·consumer contract test를 실행한다.
- Frontend 표시까지 포함한 E2E 결과가 없으면 계약 인수 완료로 보지 않는다.

## 열린 결정

- D1 합동 검토에서 고정할 callback 인증 방식과 credential rotation 절차
- `citations`, chart insight, error review detail의 최종 JSON schema
- callback 재시도 간격·상한과 작업 상태 TTL
- API schema 버전 필드와 breaking change 버전 정책
