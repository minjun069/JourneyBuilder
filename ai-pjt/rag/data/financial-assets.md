# 금융자산 인터페이스 가이드
step_id: financial-assets
registry_source: backend-pjt/consulting/field_registry.py@ef3f8b7

금융자산 목록의 상품·원금·평가금액·적립 정보를 정리한다.
필드명·자료형·단위·제약은 canonical 필드 레지스트리를 그대로 따른다. 외부 금융 통계나 권장값은 이 문서에 넣지 않는다.

| section | field_name | 자료형 | 단위 | 제약 | AI 변경 |
|---|---|---|---|---|---|
| `financial_assets` | `asset_type` | `string` | `none` | `{"max_length": 50}` | 허용 |
| `financial_assets` | `name` | `string` | `none` | `{"max_length": 100}` | 허용 |
| `financial_assets` | `principal` | `integer` | `KRW` | `{"min": 0}` | 허용 |
| `financial_assets` | `current_value` | `integer` | `KRW` | `{"min": 0}` | 허용 |
| `financial_assets` | `monthly_savings` | `integer` | `KRW` | `{"min": 0, "period": "month"}` | 허용 |
| `financial_assets` | `purpose` | `string` | `none` | `{"max_length": 50}` | 허용 |

---

## asset_type: 자산 유형
- section: `financial_assets`
- 자료형: `string`
- 단위: `none`
- 제약: `{"max_length": 50}`
- AI 변경: 허용된 draft 변경 대상
- 입력 규칙: 사용자의 표현을 의미가 바뀌지 않도록 canonical 값으로 정리한다.

---

## name: 상품명
- section: `financial_assets`
- 자료형: `string`
- 단위: `none`
- 제약: `{"max_length": 100}`
- AI 변경: 허용된 draft 변경 대상
- 입력 규칙: 사용자의 표현을 의미가 바뀌지 않도록 canonical 값으로 정리한다.

---

## principal: 원금
- section: `financial_assets`
- 자료형: `integer`
- 단위: `KRW`
- 제약: `{"min": 0}`
- AI 변경: 허용된 draft 변경 대상
- 입력 규칙: 원 단위 정수로 정규화한다. 만 원 단위 표시값을 그대로 저장하지 않는다.

---

## current_value: 현재 평가금액
- section: `financial_assets`
- 자료형: `integer`
- 단위: `KRW`
- 제약: `{"min": 0}`
- AI 변경: 허용된 draft 변경 대상
- 입력 규칙: 원 단위 정수로 정규화한다. 만 원 단위 표시값을 그대로 저장하지 않는다.

---

## monthly_savings: 월 적립액
- section: `financial_assets`
- 자료형: `integer`
- 단위: `KRW`
- 제약: `{"min": 0, "period": "month"}`
- AI 변경: 허용된 draft 변경 대상
- 입력 규칙: 원 단위 정수로 정규화한다. 만 원 단위 표시값을 그대로 저장하지 않는다.

---

## purpose: 운용 목적
- section: `financial_assets`
- 자료형: `string`
- 단위: `none`
- 제약: `{"max_length": 50}`
- AI 변경: 허용된 draft 변경 대상
- 입력 규칙: 사용자의 표현을 의미가 바뀌지 않도록 canonical 값으로 정리한다.
