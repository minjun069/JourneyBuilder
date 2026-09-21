# AI 오케스트레이션 명세

상태: **현재 작업별 모델 계약 반영 — 실제 모델 품질 평가는 별도 실행**

이 문서는 Agent 상태, 라우팅, 종료 조건, 프롬프트·모델 운용 방식의 단일 원천이다. 사용자 관찰 가능 책임과 인수 기준은 [AI Agent 요구사항](../requirements/ai-agent.md)을 따른다.

## Agent 내부 책임

| Agent | 내부 책임 | 주요 입력 | 주요 출력 |
|---|---|---|---|
| qa | 질문·검색 계획 일괄 생성, 근거 답변과 동일 근거 검토 | 작업 설명, 관련 현재값·binding, RAG 본문 | 답변, 근거 한계 |
| input_assist | 발화에서 폼 변경 추출, 코드 검증, 필요한 의미 검토 | 작업 설명, 짧은 필드·행 참조, 현재 폼 | 코드가 복원한 FormChangeAction, 답변 |
| error_review | 기본 검증을 통과한 값의 명확한 오기·단위 착오·직접 모순 검토 | 필드 참조키·이름·단위·값, 관계 검토 기준 | 코드가 복원한 ErrorReviewResult |
| chart_interpret | Backend가 검증한 차트 사실 설명 | 검증 메타를 제외한 설명용 사실, 관련 설명 자료 | 핵심 해석, 주의점, 근거 수치 |
| elicit | 과거 대화와 현재 정성 발화에 자연스럽게 응답 | 현재 메시지, 대화 문맥 | 자연어 응답 |

현재 실행 역할은 다섯 Agent이며 과거 [ADR-0001](../decisions/0001-six-agent-ai-architecture.md)의 별도 Fallback Agent는 사용하지 않는다. 실패 안내는 코드와 Aggregator가 담당한다. 사용자의 승인 없는 재무 데이터 저장, 실제 투자 실행, 검토되지 않은 상품 추천, 결정론적 검증의 LLM 대체는 수행하지 않는다.

## 공통 상태

| 상태 | 설명 |
|---|---|
| request_id | 한 AI SSE 실행의 idempotency·terminal 식별자 |
| thread_id | 사용자와 상담 세션을 함께 포함한 대화 문맥 식별자 |
| step_id | 프론트의 실제 프로세스 단계 |
| request_mode | 일반 chat, 저장 시 error_review, 최초 presentation_entry |
| conversation_context | 같은 session·thread의 최근 대화, 요약과 출처가 있는 기억 |
| customer_info | 확정 정보와 현재 미저장 폼이 구분된 snapshot |
| chart_payload | Backend 검증 보고서. ready는 수치 설명, guidance_only는 목적 안내, 비차트 단계만 null |
| route_plan | 코드가 합성하고 검증한 최대 3개 task와 dependency·binding |
| task_input | 해당 작업의 description과 검증된 binding |
| output | 자연어 답변, FormChangeAction 또는 ErrorReviewResult |

필드 레지스트리는 AI 로컬 코드에서 step_id로 읽는다. 대화·archive는 같은 session·thread에서만 복원한다. 기억은 사용자·AI·기존 요약을 모두 포함하고 금융 주제·숫자·참조 token을 추가 배제하지 않는다. 상류 마스킹, 원문 근거 인용·기본 형식·scope·CAS 검증은 유지한다. AI 제안과 사용자 결정을 구분하고 현재 폼·보고서 사실은 Backend snapshot을 따른다. 모델 대화 입력에서 저장 ID·검색 점수·예산 메타는 제외한다.


### 미저장 폼 변경 정책

input_assist가 내보내는 `FormChangeAction`은 저장된 DB가 아니라 `target=draft_form`인 현재 미저장 폼만 대상으로 한다. AI에는 단일 객체와 반복 객체를 구분하지 않는 동일한 operation 언어를 제공한다.

- `set`: 경로의 값을 지정한다.
- `add`: 반복 항목을 추가한다.
- `update`: 안정적인 `item_id` 또는 `client_item_id`로 항목을 수정한다.
- `remove`: 안정적인 ID로 항목을 제거한다.

AI가 만든 모든 변경은 `proposal`로 반환해 사용자가 change별로 승인하거나 거절한다. 변경 요청 자체는 승인 결정이 아니며 draft에 자동 적용하지 않는다. confidence는 계약에 없다. Django는 현재 단계의 레지스트리와 operation allowlist를 다시 검증하고 실제 DB 저장은 기존 폼 저장 API와 serializer 검증을 거친다.

반복 항목은 DB 저장 후 서버 `id`를 사용하고, 저장 전에는 프론트가 발급한 `client_item_id`를 사용한다. 중첩 항목 추가는 `parent_item_id` 또는 `parent_client_item_id`로 부모를 지정한다.
## 공통 흐름

1. 요청 스키마와 권한을 검증한다.
2. 세션 문맥을 복원한다.
3. Planner가 status와 requests를 반환한다. 각 요청은 type·description과 선택 reference_id/reference_age만 가진다. 작업 설명은 조건·값·단위·마스킹 참조를 보존한다.
4. request_plan compiler가 ID·조회 작업·도구 인자·dependency·provenance binding을 합성하고 plan policy가 단계별 허용 작업과 구조를 검증한다. executor는 의존 순서대로 최대 3개 task를 실행한다.
5. 각 Agent에는 검증된 작업 설명과 필요한 사실·binding만 전달한다. provider·schema·compile 오류로 계획 생성·검증이 두 차례 실패하면 계획 실패 안내로 끝내며 원문을 description으로 대신 넣는 QA 우회는 하지 않는다. runtime이 권한·deadline·도구 계약을 검증하며 failed dependency의 후속 task는 실행하지 않는다.
6. Agent 최종 출력은 form action 유무와 관계없이 항상 `validate_form_update` 파이프라인 노드를 통과한다. 이 노드는 일반 응답 계약과 form action의 canonical 필드·자료형·단위·연산을 결정론적으로 검사한다.
7. Aggregator가 성공·실패 안내와 action/review를 코드로 합치고 성공이 하나여도 answer를 모델로 다듬는다. response 한 필드만 받고 숫자 순서·값·조건·승인·저장 문장이 보존되지 않으면 코드 응답으로 되돌린다. 보안 검사를 통과한 결과만 terminal로 반환한다.

## Agent별 종료와 재시도

- qa: plan → 필요한 검색 전체 → answer → 같은 근거 judge로 기본 모델 3회다. 표현 문제는 같은 근거로, 근거 부족은 계획·검색부터 한 번 재시도한다. 별도 답변 병합 모델은 없다.
- input_assist: extract → 코드 검증 → 필요한 후보만 일괄 review → respond로 기본 모델 2회, 의미 검토 시 3회다. ref를 외부 field·row ID로 복원하는 작업은 코드가 담당한다.
- error_review: 모델은 field_ref·reason·선택 suggested_value만 반환하고 코드는 현재값·식별자를 복원한다. confidence·confidence_level은 없으며 결과 없음·오류는 기존 저장을 막지 않는다.
- chart_interpret: 제공된 표시값·단위를 그대로 사용한다. 숫자 검증도 같은 설명용 projection을 사용하며 메타 숫자와 임의 백분율 환산을 허용하지 않는다. 최초 안내는 최대 3개 주요 항목, 자료는 현재 보고서와 질문 관련 구간만 전달한다.
- elicit: 현재 정성 대화에 응답하며 전용 필드 상태·확인 후보·capture·form action을 만들지 않는다.

구조화 출력 파싱 실패와 일시적 외부 오류만 제한적으로 자동 재시도한다. 부작용 도구는 idempotency가 없으면 자동 재시도하지 않는다.

`chart_interpret`는 `chart_payload.status=ready`이고 `chart_id`가 현재 `step_id`와 같을 때만 수치 해석을 수행한다. 렌더링 option이나 screenshot을 입력으로 사용하지 않고 form change를 출력하지 않는다. 입력 구조와 미준비 동작은 저장소의 `contract-fixtures/ai-lifecycle/v2/chart-payloads.json`을 따른다.

## 프롬프트와 모델

- `prompts/definitions`, catalog와 manifest가 작업별 prompt·입출력 계약을 소유한다. 공통 지시는 역할 준수·없는 사실 금지·참조 보존·대화의 출처 구분으로 줄이고 사용자용 문체는 답변 생성 호출에만 적용한다.
- prompt ID·버전, 모델 provider·모델명, temperature, tool schema 버전을 trace에 남긴다.
- Planner 의미 요청은 구조화 출력으로 제한한다. 임의 JSON 값이 필요한 Input extract와 Error Review는 JSON mode 응답을 Pydantic으로 파싱하고 공통 출력 보안 검사를 거친다. allowlist 밖 값은 실행 전에 거부한다.
- 모델이 생성한 사용자·세션·step·권한 값은 신뢰하지 않으며 Function tool에는 runtime의 `ToolExecutionContext`만 사용한다.
- 모델은 품질, 구조화 출력 성공률, 지연, 비용, 데이터 처리 조건을 동일 평가셋으로 비교해 결정한다.
- 프로덕션 모델·Embedding은 재현 가능한 버전으로 고정한다.
- 완료된 자산배분 연구는 [프로젝트 요구사항](../REQUIREMENTS.md) §4의 비제품 문헌·설계 조사 산출물로만 유지하며 모델, 도구, graph, 화면에 연결하지 않는다. 개인별 비중 계산·제약 최적화·백테스트 구현은 별도 요구사항이 채택되기 전에는 수행하지 않는다.

## 관련 세부 명세

| 확인 대상 | 단일 원천 |
|---|---|
| RAG 문서·검색·근거 | [retrieval.md](retrieval.md) |
| Function Calling 도구·공통 출력 후검증 | [tools.md](tools.md) |
| 평가셋·지표·관측·rollback | [evaluation.md](evaluation.md) |
| 데이터 보호·권한·공격 통제 | [AI 보안 정책 v0.1](../security/ai-security-policy.md) |
| AI request·terminal·공용 fixture | [AI 서비스 API 계약](../contracts/ai-api.md) |
| 3주 MVP 범위와 제외 범위 | [프로젝트 요구사항](../REQUIREMENTS.md) §4 |

## 열린 결정

- 운영 모델·Embedding provider와 고정 버전
- 대화 요약 임계값과 보존기간
