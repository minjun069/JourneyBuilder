# 재무목표 인터페이스 가이드
step_id: financial-goals
registry_source: backend-pjt/consulting/field_registry.py@ef3f8b7

은퇴 전후 기간별 현금흐름 항목과 마일스톤을 정리한다.
필드명·자료형·단위·제약은 canonical 필드 레지스트리를 그대로 따른다. 외부 금융 통계나 권장값은 이 문서에 넣지 않는다.

| section | field_name | 자료형 | 단위 | 제약 | AI 변경 |
|---|---|---|---|---|---|
| `goal_periods` | `phase` | `string` | `none` | `{"choices": ["before", "after"]}` | 허용 |
| `goal_periods` | `from_age` | `integer` | `age` | `{"max": 120, "min": 1}` | 허용 |
| `goal_periods` | `to_age` | `integer` | `age` | `{"max": 120, "min": 1}` | 허용 |
| `goal_periods` | `items` | `array` | `none` | `{}` | 허용 |
| `goal_periods` | `items[].name` | `string` | `none` | `{"max_length": 100}` | 허용 |
| `goal_periods` | `items[].amount` | `integer` | `KRW` | `{"min": 0, "period": "month"}` | 허용 |
| `goal_periods` | `items[].is_fixed` | `boolean` | `none` | `{}` | 허용 |
| `goal_milestones` | `phase` | `string` | `none` | `{"choices": ["before", "after"]}` | 허용 |
| `goal_milestones` | `age` | `integer` | `age` | `{"max": 120, "min": 1}` | 허용 |
| `goal_milestones` | `category` | `string` | `none` | `{"choices": ["소득", "지출"]}` | 허용 |
| `goal_milestones` | `content` | `string` | `none` | `{"max_length": 200}` | 허용 |
| `goal_milestones` | `amount` | `integer` | `KRW` | `{"min": 0}` | 허용 |
| `goal_milestones` | `note` | `string` | `none` | `{"max_length": 200}` | 허용 |

---

## phase: 은퇴 전후 구분
- section: `goal_periods`, `goal_milestones`
- 자료형: `string`
- 단위: `none`
- 제약: `{"choices": ["before", "after"]}`
- AI 변경: 허용된 draft 변경 대상
- 입력 규칙: 사용자의 표현을 의미가 바뀌지 않도록 canonical 값으로 정리한다.

---

## from_age: 시작 나이
- section: `goal_periods`
- 자료형: `integer`
- 단위: `age`
- 제약: `{"max": 120, "min": 1}`
- AI 변경: 허용된 draft 변경 대상
- 입력 규칙: 만 나이 정수를 사용한다.

---

## to_age: 종료 나이
- section: `goal_periods`
- 자료형: `integer`
- 단위: `age`
- 제약: `{"max": 120, "min": 1}`
- AI 변경: 허용된 draft 변경 대상
- 입력 규칙: 만 나이 정수를 사용한다.

---

## items: 기간별 항목
- section: `goal_periods`
- 자료형: `array`
- 단위: `none`
- 제약: `{}`
- AI 변경: 허용된 draft 변경 대상
- 입력 규칙: 현재 section의 canonical 구조를 유지하며 임의 키를 추가하지 않는다.

---

## items[].name: 기간별 항목명
- section: `goal_periods`
- 자료형: `string`
- 단위: `none`
- 제약: `{"max_length": 100}`
- AI 변경: 허용된 draft 변경 대상
- 입력 규칙: 사용자의 표현을 의미가 바뀌지 않도록 canonical 값으로 정리한다.

---

## items[].amount: 기간별 금액
- section: `goal_periods`
- 자료형: `integer`
- 단위: `KRW`
- 제약: `{"min": 0, "period": "month"}`
- AI 변경: 허용된 draft 변경 대상
- 입력 규칙: 원 단위 정수로 정규화한다. 만 원 단위 표시값을 그대로 저장하지 않는다.

---

## items[].is_fixed: 기간별 고정 여부
- section: `goal_periods`
- 자료형: `boolean`
- 단위: `none`
- 제약: `{}`
- AI 변경: 허용된 draft 변경 대상
- 입력 규칙: `true` 또는 `false`를 사용한다.

---

## age: 마일스톤 나이
- section: `goal_milestones`
- 자료형: `integer`
- 단위: `age`
- 제약: `{"max": 120, "min": 1}`
- AI 변경: 허용된 draft 변경 대상
- 입력 규칙: 만 나이 정수를 사용한다.

---

## category: 마일스톤 분류
- section: `goal_milestones`
- 자료형: `string`
- 단위: `none`
- 제약: `{"choices": ["소득", "지출"]}`
- AI 변경: 허용된 draft 변경 대상
- 입력 규칙: 사용자의 표현을 의미가 바뀌지 않도록 canonical 값으로 정리한다.

---

## content: 마일스톤 내용
- section: `goal_milestones`
- 자료형: `string`
- 단위: `none`
- 제약: `{"max_length": 200}`
- AI 변경: 허용된 draft 변경 대상
- 입력 규칙: 사용자의 표현을 의미가 바뀌지 않도록 canonical 값으로 정리한다.

---

## amount: 마일스톤 금액
- section: `goal_milestones`
- 자료형: `integer`
- 단위: `KRW`
- 제약: `{"min": 0}`
- AI 변경: 허용된 draft 변경 대상
- 입력 규칙: 원 단위 정수로 정규화한다. 만 원 단위 표시값을 그대로 저장하지 않는다.

---

## note: 마일스톤 비고
- section: `goal_milestones`
- 자료형: `string`
- 단위: `none`
- 제약: `{"max_length": 200}`
- AI 변경: 허용된 draft 변경 대상
- 입력 규칙: 사용자의 표현을 의미가 바뀌지 않도록 canonical 값으로 정리한다.
