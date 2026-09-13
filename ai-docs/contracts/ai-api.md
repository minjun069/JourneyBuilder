# AI 서비스 API 계약

상태: **D1 기준 Django↔AI·승인 생명주기와 chart 입력 v2 단일 원천 동결 — citation·chart insight 출력은 후속 계약**

이 문서는 Django→AI SSE 요청·terminal 결과, 서비스 인증·상태·오류와 step별 Agent allowlist를 소유한다. Browser↔Django 계약은 [Django HTTP API](http-api.md), canonical 필드 의미와 데이터 소유권은 [데이터 스키마](../data/schema.md), 동결 계약과 아직 다른 실제 구현은 [현재 계약 불일치 조사](current-state.md)를 따른다.

일반 상담, Elicit과 Error Review의 `POST /api/ai/v2/chat/stream` 요청과 이벤트는
[AI 상담 SSE v2 계약](sse-v2.md)을 따른다. 비스트리밍 `POST /v2/chat`과
AI→Django HTTP callback은 제공하지 않는다.

## 호출 주체와 책임

Browser는 AI 서버를 직접 호출하지 않는다. Django가 사용자 인증, 상담 세션 소유권, canonical snapshot, SSE terminal 검증과 최종 폼 저장을 담당한다. change별 승인·거절은 Frontend 로컬 UI가 소유한다.

AI 서비스는 Django가 전달한 snapshot을 바탕으로 6-Agent를 실행하고 자연어 답변, 인용, draft 변경 제안, 분석 결과를 SSE로 반환한다. AI 서비스는 Django 업무 DB에 직접 쓰지 않는다.

| 주체 | 책임 |
|---|---|
| Frontend | 메시지·단계·현재 폼과 Error Review trigger 전달, 모든 대화형 응답의 SSE terminal 표시, 승인된 단일 change 또는 검토 후보의 draft 적용 |
| Django | 사용자·세션 소유권, request/thread/session 결합, terminal receipt, SSE terminal과 form change 재검증 |
| AI | 문맥 복원, Agent 라우팅·실행, RAG·Function Calling·MCP 실행, 도구 입출력·구조화 출력·보안 검사, 성공·실패 SSE terminal |
| MCP | 허용 도구 실행, 스키마·권한·timeout·감사 결과 반환 |

## 공용 서비스 인증

Django→AI SSE는 shared credential인 `AI_SERVICE_BEARER_TOKEN`을 사용한다.

```text
Authorization: Bearer <AI_SERVICE_BEARER_TOKEN>
```

- token은 사용자 JWT와 다르며 Browser, LLM, MCP payload, 일반 로그에 전달하지 않는다.
- 빈 값, 누락, 불일치는 `401 SERVICE_UNAUTHORIZED`로 실패 폐쇄한다. 개발·테스트도 명시적인 test token을 주입한다.
- 모든 서비스 통신은 TLS를 사용한다. token만으로 request 소유권을 판단하지 않고 `request_id`, `session_id`, `thread_id` 결합을 함께 검증한다.
- token이 유출되면 Django와 AI에서 동시에 폐기하고 교체한다.
- 회전할 때는 새 stream 연결을 중지하고 활성 stream을 drain한 뒤 두 서비스를 같은 release에서 새 token으로 전환한다. 전환 뒤 SSE 인증 smoke test가 끝나야 접수를 재개한다.

## Django → AI 작업 요청

`POST /api/ai/v2/chat/stream`은 bearer 인증 뒤 `start -> delta* -> done | error` SSE를 반환한다. 요청 payload로 임의 callback URL을 받지 않는다.

```json
{
  "contract_version": "2.0",
  "request_id": "ai_req_...",
  "session_id": 1,
  "thread_id": "user-3-session-1",
  "step_id": "basic-info",
  "message": "월급은 세후 300만 원이에요.",
  "deadline_at": "2026-09-02T06:01:00Z",
  "customer_info": {
    "saved_data": {},
    "current_form": {}
  },
  "field_registry": [
    {
      "step_id": "basic-info",
      "section": "basic_info",
      "path": "monthly_income",
      "value_type": "integer",
      "unit": "KRW",
      "constraints": {"min": 0, "period": "month"},
      "cardinality": "single",
      "sensitivity": "financial_sensitive",
      "writable_by_ai": true,
      "nullable": true,
      "label": "세후 월 소득",
      "operations": ["set", "add", "update", "remove"]
    }
  ],
  "chart_payload": null
}
```

| 필드 | 필수 | 계약 |
|---|---:|---|
| `contract_version` | Y | v2는 정확히 `2.0` |
| `request_id` | Y | Django가 생성하는 SSE 실행·terminal idempotency 식별자 |
| `session_id` | Y | Django 상담 세션 ID; thread·request와 결합 검증 |
| `thread_id` | Y | 사용자와 상담 세션을 함께 식별하고 다른 조합에서 재사용 금지 |
| `step_id` | Y | Frontend 실제 단계 ID. 미지원 값은 fallback 처리 |
| `message` | Y | 1~2000자의 현재 사용자 메시지 |
| `deadline_at` | Y | AI 실행 60초 상한의 UTC 시각 |
| `customer_info.saved_data` | Y | DB에 확정 저장된 canonical snapshot |
| `customer_info.current_form` | Y | 아직 저장되지 않은 현재 폼 snapshot |
| `field_registry` | Y | 현재 단계의 canonical 필드·자료형·단위·제약·민감도·쓰기 가능 여부 |
| `chart_payload` | N | chart 단계의 읽기 전용 원본 수치와 계산 metadata. 다른 단계 또는 보고서 미준비 상태는 `null` |

같은 Browser idempotency key가 이미 종결됐다면 Django는 AI graph를 다시 실행하지 않고
terminal receipt의 결과를 SSE로 재생한다. 같은 key의 payload가 다르면
`409 IDEMPOTENCY_CONFLICT`, 아직 실행 중이면 `409 AI_STREAM_IN_PROGRESS`다.

## step_id 레지스트리

`step_id`와 단계별 허용 Agent의 기계 판독 원천은 저장소의
`contract-fixtures/ai-lifecycle/v2/step-registry.json` 하나다. 이 Markdown은 값을
복제하지 않는다. fixture는 계약 버전, fallback Agent, 전체 Agent 목록·설명과 각 단계의
`step_id`·`allowed_agents`, 화면 단계가 아닌 transport 전용 fallback ID를 정의한다.

- AI router는 시작 시 fixture를 직접 읽고 중복 단계, 미등록 Agent, fallback 누락을 거부한다.
- 라우팅 평가도 같은 fixture를 읽으며 이 문서의 표를 파싱하지 않는다.
- Frontend 계약 검사는 `processSteps.js`의 단계 ID 집합이 fixture와 같은지 확인한다.
- 지원되지 않은 `step_id`는 `fallback`으로 처리한다.

`general`은 폼 맥락 없이 Browser 요청에서 사용하는 transport 전용 ID이며 Frontend의
24개 화면 단계에는 포함되지 않는다. fixture의 `transport_fallback_step_ids`에 별도로
등록하고 AI는 classifier를 호출하지 않은 채 `fallback`으로 처리한다.

`elicit`은 `financial-mindset`에서만 허용한다. `financial-info`는 저장 대상이 없는 안내
단계이고, `presentation`은 특정 차트가 아닌 상위 단계이므로 각각 입력 변경 Agent와
`chart_interpret`을 허용하지 않는다. 정확한 allowlist 변경은 fixture를 먼저 수정하고
AI·Frontend 계약 검사를 함께 통과시킨다.

## 읽기 전용 chart 입력

chart 단계에서 Django는 `chart_payload`로 렌더링 option이나 screenshot이 아닌 검증된 보고서 원본을 전달한다. v2 대표 계약은 `lifetime-cashflow`이며 상세 예시는 저장소의 `contract-fixtures/ai-lifecycle/v2/chart-payloads.json`을 따른다.

| 필드 | 계약 |
|---|---|
| `chart_id` | 실제 chart `step_id`와 같은 값 |
| `title`·`status`·`generated_at` | 차트명, `ready` 상태, UTC 생성 시각 |
| `source_report` | Django report ID·type·version |
| `period` | 축, 시작·종료, 간격과 기간 단위 |
| `units` | series와 assumption 각 수치의 단위. 금액은 원, 비율은 decimal |
| `assumptions` | 계산에 사용한 가정과 출처 |
| `summary` | 고갈 여부·나이와 종료 시점 결과 |
| `series` | 나이별 현금 유입·유출·순흐름·자산·부채·순자산 원본 |

- `chart_payload`는 읽기 전용이며 `chart_interpret`가 form change를 생성하는 근거로 사용할 수 없다.
- `cash_outflow`는 series에서 음수로 표현하고, `net_flow = cash_inflow + cash_outflow`를 만족해야 한다.
- 보고서가 없거나 `ready`가 아니면 `null`을 전달한다. AI는 이때 수치를 추측하지 않고 분석 불가 상태를 안내한다.
- Browser나 Django의 렌더링 option은 전달하지 않는다. 로그·trace에는 원본 금융 수치를 그대로 남기지 않는다.

## AI → Django SSE terminal

AI는 `POST /api/ai/v2/chat/stream`의 `done` 또는 `error` event로 `completed` 또는 `failed`
terminal payload를 보낸다. 두 상태 모두 `contract_version`, 세 식별자와
`completed_at`이 필수다.

완료 terminal:

```json
{
  "contract_version": "2.0",
  "request_id": "ai_req_...",
  "session_id": 1,
  "thread_id": "user-3-session-1",
  "status": "completed",
  "completed_at": "2026-09-02T06:00:42Z",
  "output": {
    "response": "두 항목을 각각 확인해주세요.",
    "step_id": "basic-info",
    "action": {
      "type": "form_change",
      "target": "draft_form",
      "user_requested": false,
      "confidence": 0.85,
      "changes": [
        {
          "section": "basic_info",
          "path": "monthly_income",
          "operation": "set",
          "value": 3000000,
          "reason": "사용자 발화에서 추출"
        },
        {
          "section": "basic_info",
          "path": "job",
          "operation": "set",
          "value": "직장인",
          "reason": "사용자 발화에서 추출"
        }
      ]
    }
  }
}
```

AI는 `action_id`, `change_id`, 최종 `mode`, 결정 상태를 만들지 않는다. Django가 terminal의 section/path/operation을 canonical 레지스트리로 검증하고 모든 form action을 `proposal`로 정규화한다. 모든 change에 표시 ID와 초기 `pending` 상태를 부여하며 Browser가 독립 승인·거절한다. 이 결정은 Frontend에서만 전이하며 Django로 전송하거나 저장하지 않는다. AI의 `user_requested`와 confidence는 승인 증거가 아니고 별도 사용자 결정 없이 draft 적용을 허용하지 않는다.

반복 항목은 `item_id` 또는 `client_item_id`, 중첩 추가는 부모 ID를 사용한다. 상세 필드·operation·mode 계약은 [데이터 스키마](../data/schema.md)를 따른다.

실패 terminal:

```json
{
  "contract_version": "2.0",
  "request_id": "ai_req_...",
  "session_id": 1,
  "thread_id": "user-3-session-1",
  "status": "failed",
  "completed_at": "2026-09-02T06:01:00Z",
  "error": {
    "code": "LLM_TIMEOUT",
    "message": "AI 응답 생성 시간이 초과되었습니다.",
    "retryable": true
  }
}
```

Django는 SSE terminal의 contract version, request·session·thread 결합, terminal 전이,
output/error schema를 모두 검사한다. 누락값을 기존 상태로 보완하거나 status 누락을
completed로 간주하지 않는다.

## 상태·timeout·재시도·TTL

| 구간 | 상한·동작 |
|---|---|
| Django→AI SSE | connect/write/pool 5초, read idle 120초; 자동 transport 전환 없음 |
| AI graph 실행 | `deadline_at`까지 최대 60초; 초과 시 `LLM_TIMEOUT` 실패 terminal |
| Browser stream | terminal 없는 EOF는 실패; 자동 polling·callback fallback 없음 |
| 활성 request 상태 | 실행 중 중복 연결 판정에 사용 |
| terminal receipt·idempotency | 24시간 보존 |

SSE transport는 자동 재시도나 polling fallback을 하지 않는다. 인증, contract/schema,
권한, policy, ID conflict는 재시도하지 않는다. terminal 상태는 되돌릴 수 없다.

- 최초 terminal은 assistant message와 `request_id` unique receipt를 같은 transaction에서 확정한다.
- 동일 terminal payload digest는 기존 message를 재사용한다.
- 같은 request의 terminal status 또는 payload가 다르면 `409 TERMINAL_CONFLICT`다.
- Redis terminal 값이 유실돼도 receipt에서 같은 상태와 message를 복구하며 graph와 저장을 다시 실행하지 않는다.

## 내부 오류 코드

| HTTP | code | retryable |
|---:|---|---:|
| 401 | `SERVICE_UNAUTHORIZED` | false |
| 409 | `REQUEST_ID_CONFLICT` | false |
| 409 | `TERMINAL_BINDING_MISMATCH` | false |
| 409 | `TERMINAL_CONFLICT` | false |
| 422 | `CONTRACT_VERSION_UNSUPPORTED` | false |
| 422 | `REQUEST_SCHEMA_INVALID` | false; `/api/ai/v2/chat/stream` 요청·terminal 필수값·자료형 오류 |
| 429 | `RATE_LIMITED` | true |
| 503 | `UPSTREAM_UNAVAILABLE` | true |

AI 실패 terminal의 code는 `LLM_TIMEOUT`, `UPSTREAM_UNAVAILABLE`, `OUTPUT_SCHEMA_INVALID`, `POLICY_BLOCKED`, `INTERNAL_ERROR` 중 하나다. 내부 stack·credential·원본 금융정보는 message에 포함하지 않는다.

## idempotency와 공용 fixture

Browser 접수 key, AI `request_id`, terminal payload digest는 서로 다른 중복 경계를 보호한다. Frontend 로컬 승인·거절에는 네트워크 요청과 멱등성 key가 없다. 정상·누락·잘못된 자료형·권한·timeout·중복 경계는 저장소의 `contract-fixtures/ai-lifecycle/v2/lifecycle.json`, 단일·정성·반복·중첩 draft 변경은 `contract-fixtures/ai-lifecycle/v2/form-actions.json`, 읽기 전용 분석 입력은 `contract-fixtures/ai-lifecycle/v2/chart-payloads.json`을 기계 단일 예제로 사용한다.

step registry, lifecycle, form action, chart payload 네 파일은 `ai-contract-v2` fixture set이며 AI·Backend·Frontend는 필요한 case를 직접 provider·consumer test에서 읽는다. 별도 사본을 만들지 않고 문서 예시와 fixture 의미가 달라지면 계약 검사를 실패시킨다. fixture의 credential 표시는 테스트 placeholder이며 실제 secret이나 사용자 원문을 넣지 않는다.

## v2 전환 정책

- v2 release에서 `/api/v1`, `/chat[/stream]`, `/v2/chat`, `/api/ai/callback[/]`, `/api/v2/internal/ai/callback/`을 종료하고 SSE endpoint로 한 번에 전환한다. 병행·dual-read·dual-write 기간은 두지 않는다.
- 선택 필드 추가만 같은 major에서 허용한다. 필수값, 이름, 자료형, 상태 의미, 인증·idempotency 규칙 변경은 다음 major다.
- 다음 major도 모든 producer·consumer와 fixture를 준비한 뒤 조정 배포한다.
- rollback은 세 서비스를 이전 release로 함께 되돌린다. v2 consumer와 이전 provider를 섞어 운영하지 않는다.
- 구현 인수는 provider test, consumer test와 Front→Django→AI SSE→FE 로컬 승인·거절→명시적 폼 저장 E2E 및 결정 시 네트워크 요청 0건 증거가 있어야 완료된다.

## 후속 출력 계약

`citations`, AI가 생성하는 chart insight, error review detail의 최종 terminal JSON schema는 각 기능 구현 전에 별도 동결한다. `chart_payload` 입력은 위 계약으로 동결됐으며 후속 출력 계약은 현재 request lifecycle, 인증, 오류, timeout, idempotency, TTL을 바꾸지 않는다.
