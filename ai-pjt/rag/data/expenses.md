# 지출 인터페이스 가이드
step_id: expenses
registry_source: backend-pjt/consulting/field_registry.py@ef3f8b7

지출 목록의 금액·고정 여부·주기를 정리한다.
필드명·자료형·단위·제약은 canonical 필드 레지스트리를 그대로 따른다. 외부 금융 통계나 권장값은 이 문서에 넣지 않는다.

| section | field_name | 자료형 | 단위 | 제약 | AI 변경 |
|---|---|---|---|---|---|
| `expenses` | `name` | `string` | `none` | `{"max_length": 100}` | 허용 |
| `expenses` | `amount` | `integer` | `KRW` | `{"min": 0}` | 허용 |
| `expenses` | `is_fixed` | `boolean` | `none` | `{}` | 허용 |
| `expenses` | `frequency` | `object` | `none` | `{}` | 허용 |
| `expenses` | `frequency.none` | `boolean` | `none` | `{}` | 읽기 전용 |
| `expenses` | `frequency.unit` | `string` | `none` | `{"choices": ["연", "월", "주"]}` | 읽기 전용 |
| `expenses` | `frequency.count` | `integer` | `none` | `{"min": 1}` | 읽기 전용 |

---

## name: 지출 항목
- section: `expenses`
- 자료형: `string`
- 단위: `none`
- 제약: `{"max_length": 100}`
- AI 변경: 허용된 draft 변경 대상
- 입력 규칙: 사용자의 표현을 의미가 바뀌지 않도록 canonical 값으로 정리한다.

---

## amount: 지출 금액
- section: `expenses`
- 자료형: `integer`
- 단위: `KRW`
- 제약: `{"min": 0}`
- AI 변경: 허용된 draft 변경 대상
- 입력 규칙: 원 단위 정수로 정규화한다. 만 원 단위 표시값을 그대로 저장하지 않는다.

---

## is_fixed: 고정 여부
- section: `expenses`
- 자료형: `boolean`
- 단위: `none`
- 제약: `{}`
- AI 변경: 허용된 draft 변경 대상
- 입력 규칙: `true` 또는 `false`를 사용한다.

---

## frequency: 지출 주기
- section: `expenses`
- 자료형: `object`
- 단위: `none`
- 제약: `{}`
- AI 변경: 허용된 draft 변경 대상
- 입력 규칙: 현재 section의 canonical 구조를 유지하며 임의 키를 추가하지 않는다.

---

## frequency.none: 주기 해당 없음
- section: `expenses`
- 자료형: `boolean`
- 단위: `none`
- 제약: `{}`
- AI 변경: 읽기 전용이며 draft 변경 대상이 아님
- 입력 규칙: `true` 또는 `false`를 사용한다.

---

## frequency.unit: 주기 단위
- section: `expenses`
- 자료형: `string`
- 단위: `none`
- 제약: `{"choices": ["연", "월", "주"]}`
- AI 변경: 읽기 전용이며 draft 변경 대상이 아님
- 입력 규칙: 사용자의 표현을 의미가 바뀌지 않도록 canonical 값으로 정리한다.

---

## frequency.count: 주기 횟수
- section: `expenses`
- 자료형: `integer`
- 단위: `none`
- 제약: `{"min": 1}`
- AI 변경: 읽기 전용이며 draft 변경 대상이 아님
- 입력 규칙: 사용자의 표현을 의미가 바뀌지 않도록 canonical 값으로 정리한다.
