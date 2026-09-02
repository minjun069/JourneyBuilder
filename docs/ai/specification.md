# AI 오케스트레이션 명세

상태: **3주 MVP 방향 승인 — 구현·baseline 측정 대기**

이 문서는 Agent 상태, 라우팅, 종료 조건, 프롬프트·모델 운용 방식의 단일 원천이다. 사용자 관찰 가능 책임과 인수 기준은 [AI Agent 요구사항](../requirements/ai-agent.md)을 따른다.

## Agent 내부 책임

| Agent | 내부 책임 | 주요 입력 | 주요 출력 |
|---|---|---|---|
| qa | 질문 명세, 검색 필요 판단, 근거 답변 | 메시지, 대화 문맥, 고객 정보, RAG | 답변, 인용, 불확실성 |
| input_assist | 발화에서 폼 변경 의도·값 추출 | 현재 단계, Canonical 필드 레지스트리, 미저장 폼, 발화 | 답변, FormChangeAction |
| error_review | 코드 검증 결과를 사용자 관점에서 설명 | 검증 오류, 현재 값, 제약 | 오류 설명, 수정 제안 |
| chart_interpret | 구조화된 차트 수치와 계산 결과 설명 | chart payload, 고객 정보 | 핵심 해석, 주의점, 근거 수치 |
| elicit | 누락된 정성정보를 한 항목씩 수집 | 누락 항목, 턴 상태, 대화 문맥 | 한 개 질문, 수집 상태 |
| fallback | 모호·미지원·실패 요청을 안전하게 복구 | 실패 범주, 지원 범위 | 재질문, 범위 안내, 재시도 안내 |

역할 수와 경계의 채택 근거는 [ADR-0001](../decisions/0001-six-agent-ai-architecture.md)을 따른다. 사용자의 승인 없는 재무 데이터 저장, 실제 투자 실행, 검토되지 않은 상품 추천, 결정론적 검증의 LLM 대체는 수행하지 않는다.

## 공통 상태

| 상태 | 설명 |
|---|---|
| request_id | 한 AI 작업의 idempotency·callback 식별자 |
| thread_id | 사용자와 상담 세션을 함께 포함한 대화 문맥 식별자 |
| session_id | Django 상담 세션 식별자 |
| step_id | 프론트의 실제 프로세스 단계 |
| field_registry | 현재 단계에서 허용된 section/path·자료형·제약·쓰기 권한 |
| messages | 역할이 구분된 bounded 대화 이력 |
| customer_info | 확정 정보와 현재 미저장 폼이 구분된 snapshot |
| route | 선택 Agent와 선택 근거 코드 |
| output | 자연어 답변, 인용, FormChangeAction, 오류의 구조화 결과 |
| trace_context | 가명화된 관측 식별자와 모델·프롬프트·인덱스 버전 |

messages는 같은 thread_id에서만 복원한다. 예산을 넘으면 최근 대화와 확정 사실을 우선하며, 요약문은 원문과 구분한다. 사용자가 값을 정정하면 최신 확정값을 우선한다.


### 미저장 폼 변경 정책

input_assist가 내보내는 `FormChangeAction`은 저장된 DB가 아니라 `target=draft_form`인 현재 미저장 폼만 대상으로 한다. AI에는 단일 객체와 반복 객체를 구분하지 않는 동일한 operation 언어를 제공한다.

- `set`: 경로의 값을 지정한다.
- `add`: 반복 항목을 추가한다.
- `update`: 안정적인 `item_id` 또는 `client_item_id`로 항목을 수정한다.
- `remove`: 안정적인 ID로 항목을 제거한다.

사용자가 변경을 명시적으로 요청했고 모델 확신도가 `0.90` 이상인 경우에만 `direct`로 적용한다. 그 밖의 변경은 `proposal`로 반환해 사용자가 미저장 폼에서 승인하거나 거절한다. Django는 현재 단계의 레지스트리와 operation allowlist를 다시 검증해 모델 출력만으로 쓰기 범위가 확장되지 않게 한다. 프론트는 step 불일치나 대상 폼 부재 시 `direct`도 `proposal`로 하향한다. 실제 DB 저장은 기존 폼 저장 API와 serializer 검증을 반드시 거친다.

반복 항목은 DB 저장 후 서버 `id`를 사용하고, 저장 전에는 프론트가 발급한 `client_item_id`를 사용한다. 중첩 항목 추가는 `parent_item_id` 또는 `parent_client_item_id`로 부모를 지정한다.
## 공통 흐름

1. 요청 스키마와 권한을 검증한다.
2. 세션 문맥을 복원한다.
3. 현재 단계의 Agent 후보를 제한하고 의도를 라우팅한다.
4. 선택 Agent가 필요한 RAG·도구를 호출한다.
5. 도구·검색 결과와 구조화 출력을 검증한다.
6. 보안 검사를 통과한 결과를 callback한다.

## Agent별 종료와 재시도

- qa: 답변과 필요한 인용을 생성했거나 근거 부족을 명시하면 종료한다.
- input_assist: 식별 항목을 성공·실패로 분류하고 변경안을 생성하면 종료한다.
- error_review: 결정론적 오류의 설명과 가능한 수정안을 제공하면 종료한다.
- chart_interpret: 전달된 핵심 수치를 설명하고 원본에 없는 계산을 하지 않았음을 검증하면 종료한다.
- elicit: 한 번에 한 질문만 반환한다. 항목이 채워졌거나 턴 상한에 도달하면 다음 항목 또는 fallback으로 전환한다.
- fallback: 재질문·지원 범위·재시도 중 하나의 다음 행동을 반환하면 종료한다.

구조화 출력 파싱 실패와 일시적 외부 오류만 제한적으로 자동 재시도한다. 부작용 도구는 idempotency가 없으면 자동 재시도하지 않는다.

## 프롬프트와 모델

- Agent별 system prompt와 구조화 출력 스키마를 코드의 단일 원천으로 관리한다.
- prompt ID·버전, 모델 provider·모델명, temperature, tool schema 버전을 trace에 남긴다.
- 라우터, 입력 보조, 도구 인자는 구조화 출력으로 제한하고 allowlist 밖 값은 fallback으로 보낸다.
- 모델은 품질, 구조화 출력 성공률, 지연, 비용, 데이터 처리 조건을 동일 평가셋으로 비교해 결정한다.
- 프로덕션 모델·Embedding은 재현 가능한 버전으로 고정한다.
- 자산배분 연구에서 LLM은 사용자 제약 추출과 설명만 맡고, 계산·제약 충족·백테스트는 결정론적 코드가 수행한다.

## 관련 세부 명세

| 확인 대상 | 단일 원천 |
|---|---|
| RAG 문서·검색·근거 | [retrieval.md](retrieval.md) |
| MCP·Function Calling 도구 | [tools.md](tools.md) |
| 평가셋·지표·관측·rollback | [evaluation.md](evaluation.md) |
| 데이터 보호·권한·공격 통제 | [AI 보안 정책 v0.1](../security/ai-security-policy.md) |
| 3주 MVP 범위와 제외 범위 | [프로젝트 요구사항](../REQUIREMENTS.md) §4 |

## 열린 결정

- 운영 모델·Embedding provider와 고정 버전
- 대화 요약 임계값과 보존기간
