# 데이터 스키마와 소유권

상태: **Canonical AI 필드·change별 결정 계약 v2 동결 — 전체 물리 스키마 미정**

## 데이터 소유권

| 데이터 | 소유자 | 쓰기 주체 | AI 접근 |
|---|---|---|---|
| 상담 세션·사용자 메시지 | Django | Django | 인증·소유권 검증된 snapshot만 요청으로 수신 |
| 확정 재무 정보 | Django | Django serializer·transaction | Agent별 allowlist로 읽기 |
| 미저장 현재 폼 | Frontend, 요청 시점 snapshot | 사용자·direct change·개별 승인된 proposal change | Django가 검증·정규화해 AI에 전달 |
| AI 대화 checkpoint | AI 서비스 | LangGraph checkpointer | 같은 `thread_id`에서만 읽기·쓰기 |
| RAG 문서·인덱스 | AI 지식 파이프라인 | 승인된 적재 작업 | 검색 도구를 통해 읽기 |
| AI 작업 상태 | Django | Django callback 처리 | request 상태 callback만 전달 |

AI와 MCP는 Django 업무 테이블에 직접 쓰지 않는다. v2 AI action은 미저장 draft만 대상으로 하며, DB 영속화는 사용자가 폼 저장을 실행하고 Django serializer가 현재 draft 전체를 검증한 뒤에만 일어난다.

## 논리 모델

### 식별자 불변 조건

- `thread_id`는 사용자와 상담 세션을 함께 식별하며 다른 조합에서 재사용하지 않는다.
- `request_id`는 하나의 AI 작업을 식별하고 callback idempotency에 사용한다.
- `action_id`는 한 assistant message의 form change 묶음을 식별한다.
- `change_id`는 action 안의 한 change를 식별하며 승인·거절의 최소 단위다.
- request, thread, session, user 소유권이 일치해야 polling·callback·승인을 처리한다.

### Canonical 필드 레지스트리

Django model·serializer의 snake_case 필드명을 저장·AI 계약의 canonical 이름으로 사용한다. Frontend의 camelCase·표시 단위는 adapter가 변환한다.

전체 필드의 기계 단일 원천은 [field_registry.py](../../backend-pjt/consulting/field_registry.py)의 `FIELDS`다. AI 요청에는 현재 `step_id`의 필드만 전달한다. RAG 문서의 과거 field name은 쓰기 계약으로 사용하지 않는다.

필드 레지스트리는 최소 다음을 정의한다.

| 속성 | 설명 |
|---|---|
| `step_id`·`section`·`path` | 화면 단계, 저장 영역, canonical 필드 경로 |
| `cardinality` | 단일 객체, 반복 목록, 중첩 목록 |
| `value_type` | string, integer, decimal, boolean, date, object, array |
| `unit` | KRW, percent, year, age, none 등 |
| `constraints` | 필수·범위·선택지·상호 필드 조건 |
| `label` | 사용자에게 표시할 이름 |
| `sensitivity` | 보안 데이터 등급 |
| `writable_by_ai` | AI가 draft 직접 변경 또는 변경 제안을 만들 수 있는지 여부. DB 직접 쓰기 권한은 아님 |
| `nullable` | 모름·미입력 상태를 `null`로 허용하는지 여부 |
| `operations` | 해당 경로에 허용된 operation 목록 |

레지스트리의 section 범위는 다음과 같이 동결한다.

| `step_id` | `section` | cardinality |
|---|---|---|
| `basic-info` | `basic_info` | `single` |
| `financial-mindset` | `financial_mindset` | `single` |
| `financial-satisfaction` | `financial_satisfaction` | `single` |
| `financial-assets` | `financial_assets` | `list` |
| `pension-assets` | `pension_assets` | `list` |
| `real-estate` | `real_estate_assets` | `list` |
| `liabilities` | `liabilities` | `list` |
| `income` | `incomes` | `list` |
| `expenses` | `expenses` | `list` |
| `financial-goals` | `goal_periods` | `nested_list` |
| `financial-goals` | `goal_milestones` | `list` |

업무 필드뿐 아니라 서버 ID, 감사 시각, `dependents.*`, `frequency.*`, `goal_periods.items[].*` 같은 JSON 중첩 경로도 포함한다. ID와 감사 시각은 `writable_by_ai=false`다.

### 0, null, false, 해당 없음

| 값 | 의미 | 처리 |
|---|---|---|
| `0` | 사용자가 확인한 실제 0 | 필드 제약이 `min: 0`이면 보존 |
| `null` | 모름, 미입력, 미확정 | `nullable=true`에서만 허용 |
| `false` | 확인된 boolean 거짓 | `null`로 바꾸지 않고 보존 |
| `frequency.none=true` | 지출 주기가 명시적으로 해당 없음 | `frequency=null`과 구분 |

나이·만족도처럼 0이 유효하지 않은 필드는 0을 null로 바꾸지 않고 serializer가 범위 오류로 거부한다. 금액·잔액처럼 `min: 0`인 필드는 0을 보존한다. 단위 없는 숫자 문자열은 canonical 단위로 보고, `억`·`만`이 명시된 문자열만 원 단위로 변환한다.


### FormChangeAction

Django가 AI callback을 검증하고 정규화한 draft 변경은 다음 구조를 따른다.

```json
{
  "action_id": "act_...",
  "type": "form_change",
  "step_id": "basic-info",
  "target": "draft_form",
  "mode": "proposal",
  "status": "pending",
  "user_requested": false,
  "confidence": 0.85,
  "changes": [
    {
      "change_id": "chg_income",
      "decision_status": "pending",
      "section": "basic_info",
      "path": "monthly_income",
      "operation": "set",
      "value": 3000000,
      "label": "세후 월 소득",
      "reason": "사용자 발화에서 추출"
    },
    {
      "change_id": "chg_job",
      "decision_status": "pending",
      "section": "basic_info",
      "path": "job",
      "operation": "set",
      "value": "직장인",
      "label": "직업",
      "reason": "사용자 발화에서 추출"
    }
  ]
}
```

AI callback에는 `action_id`, `change_id`, `mode`, 결정 상태가 없다. Django가 canonical 레지스트리 검증 뒤 안정 ID와 상태를 부여한다.

| operation | cardinality와 무관한 공통 의미 |
|---|---|
| `set` | 지정한 대상 값을 정확히 교체 |
| `add` | 새 대상·값·항목을 추가 |
| `update` | 기존 대상·객체·항목의 일부를 수정 |
| `remove` | 지정한 대상·값·항목을 제거 |

단일·반복 객체의 실제 자료구조 차이는 Frontend resolver와 Django 저장 로직이 처리한다.

### 안정 ID

- 저장된 반복 항목은 Django `id`를 `item_id`로 사용한다.
- 미저장 반복 항목은 Frontend UUID `client_item_id`를 사용한다.
- 중첩 항목 추가는 부모의 `parent_item_id` 또는 `parent_client_item_id`를 함께 지정한다.
- 배열 index는 식별 계약으로 사용하지 않는다.
- 목록 PUT은 기존 ID의 세션 소유권을 확인해 upsert하고 응답 ID를 Frontend 행에 동기화한다.

### direct와 proposal 판정

Django는 target이 `draft_form`이고, 사용자가 변경을 명시적으로 요청했으며, 전체 변경의 최저 confidence가 `0.90` 이상일 때만 `direct`를 허용한다. 그 외는 `proposal`이다.

Frontend는 응답 `step_id`가 현재 화면과 다르거나 대상을 찾지 못하면 direct change도 proposal처럼 표시하고 해당 change의 결정 endpoint를 사용한다. direct와 승인된 proposal은 미저장 draft만 바꾸며 DB 저장 완료로 표시하지 않는다.

proposal의 각 change는 `pending → approved | rejected`로 독립 전이한다. direct change는 `not_required`이고 화면에서 proposal로 낮아지면 같은 결정 endpoint로 종결한다. action 전체 상태는 `direct`, `pending`, `partially_resolved`, `resolved` 중 하나이며 전체 승인·전체 거절 endpoint는 없다. 같은 결정의 재전송은 같은 결과를 반환하고 반대 결정은 conflict다.

개별 승인 당시에는 그 change의 식별자·operation·대상만 다시 검증한다. 여러 change 사이의 최종 상호 필드 제약은 사용자가 폼 저장을 실행할 때 serializer가 현재 draft 전체에 강제한다.

### 대표 데이터 형태와 구현 순서

| 형태 | 우선 검증 단계 | 확장 대상 |
|---|---|---|
| 단일 필드·선택값 | `basic-info` | `financial-satisfaction` |
| 정성 텍스트 | `financial-mindset` | 정성정보 유도 대상 필드 |
| 반복 목록 | `financial-assets` | 연금·부동산·부채·소득·지출 |
| 중첩 목록 | 별도 계약 테스트 | `financial-goals` |
| 읽기 전용 분석 | 별도 chart payload 테스트 | 재무 진단·차트 단계 |

대표 기계 예시는 [form action fixture](../contracts/fixtures/ai-lifecycle/v2/form-actions.json)와 [chart payload fixture](../contracts/fixtures/ai-lifecycle/v2/chart-payloads.json)를 따른다. `financial-goals`의 중첩은 `goal_periods[]` 부모마다 `items[]` 자식 목록을 가지는 구조를 뜻한다. 자식 추가는 부모 ID를, 기존 자식 수정·삭제는 자식의 `item_id` 또는 `client_item_id`를 사용한다.

`lifetime-cashflow` chart payload는 Django 보고서가 생산하는 읽기 전용 분석 입력이다. 금액은 KRW 원 단위, 가정 비율은 decimal, 현금 유출 series는 음수로 고정한다. 이 payload는 canonical form이나 AI 쓰기 가능 필드 레지스트리에 포함되지 않는다.

## 물리 스키마

전체 물리 스키마와 DDL 단일 원천은 추가 합의가 필요하다. AI 작업에서 기존 Django migration과 실제 DB catalog를 직접 확인하며 문서만으로 존재를 추정하지 않는다.

AI checkpoint, RAG vector table, 작업 상태 저장소의 schema·인덱스·보존기간은 구현 전 별도로 고정한다.

## 마이그레이션과 트랜잭션

- Django 업무 데이터 변경은 Django migration과 transaction을 사용한다.
- AI proposal의 change별 결정 기록은 idempotent하게 갱신한다. 이 결정 transaction은 업무 재무정보를 직접 변경하지 않는다.
- 필드명·자료형·단위 변경은 Frontend adapter, Django serializer·model, AI field registry, RAG guide, fixture를 함께 migration한다.
- 반복·중첩 구조 migration은 기존 제안·메시지의 호환성과 rollback 절차를 포함한다.
- RAG 인덱스 재구축은 업무 DB migration과 분리하고 활성 인덱스 버전을 전환할 수 있어야 한다.

## 열린 결정

- 전체 Django 물리 스키마와 migration 상태의 인수 점검 결과
- checkpoint·vector·작업 상태 DB의 분리 여부와 보존기간
