# 소득 인터페이스 가이드
step_id: income
registry_source: backend-pjt/consulting/field_registry.py@ef3f8b7

월 기준 소득 목록과 고정 여부를 정리한다.
필드명·자료형·단위·제약은 canonical 필드 레지스트리를 그대로 따른다. 외부 금융 통계나 권장값은 이 문서에 넣지 않는다.

| section | field_name | 자료형 | 단위 | 제약 | AI 변경 |
|---|---|---|---|---|---|
| `incomes` | `type` | `string` | `none` | `{"max_length": 50}` | 허용 |
| `incomes` | `name` | `string` | `none` | `{"max_length": 100}` | 허용 |
| `incomes` | `amount` | `integer` | `KRW` | `{"min": 0, "period": "month"}` | 허용 |
| `incomes` | `is_fixed` | `boolean` | `none` | `{}` | 허용 |

---

## type: 소득 유형
- section: `incomes`
- 자료형: `string`
- 단위: `none`
- 제약: `{"max_length": 50}`
- AI 변경: 허용된 draft 변경 대상
- 입력 규칙: 사용자의 표현을 의미가 바뀌지 않도록 canonical 값으로 정리한다.

---

## name: 소득 내용
- section: `incomes`
- 자료형: `string`
- 단위: `none`
- 제약: `{"max_length": 100}`
- AI 변경: 허용된 draft 변경 대상
- 입력 규칙: 사용자의 표현을 의미가 바뀌지 않도록 canonical 값으로 정리한다.

---

## amount: 소득 금액
- section: `incomes`
- 자료형: `integer`
- 단위: `KRW`
- 제약: `{"min": 0, "period": "month"}`
- AI 변경: 허용된 draft 변경 대상
- 입력 규칙: 원 단위 정수로 정규화한다. 만 원 단위 표시값을 그대로 저장하지 않는다.

---

## is_fixed: 고정 여부
- section: `incomes`
- 자료형: `boolean`
- 단위: `none`
- 제약: `{}`
- AI 변경: 허용된 draft 변경 대상
- 입력 규칙: `true` 또는 `false`를 사용한다.
