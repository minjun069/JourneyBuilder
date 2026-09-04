# 부동산자산 인터페이스 가이드
step_id: real-estate
registry_source: backend-pjt/consulting/field_registry.py@ef3f8b7

부동산자산 목록의 취득·평가·임대·담보 정보를 정리한다.
필드명·자료형·단위·제약은 canonical 필드 레지스트리를 그대로 따른다. 외부 금융 통계나 권장값은 이 문서에 넣지 않는다.

| section | field_name | 자료형 | 단위 | 제약 | AI 변경 |
|---|---|---|---|---|---|
| `real_estate_assets` | `type` | `string` | `none` | `{"max_length": 50}` | 허용 |
| `real_estate_assets` | `name` | `string` | `none` | `{"max_length": 200}` | 허용 |
| `real_estate_assets` | `current_value` | `integer` | `KRW` | `{"min": 0}` | 허용 |
| `real_estate_assets` | `acquisition_price` | `integer` | `KRW` | `{"min": 0}` | 허용 |
| `real_estate_assets` | `acquisition_date` | `date` | `date` | `{}` | 허용 |
| `real_estate_assets` | `monthly_rental_income` | `integer` | `KRW` | `{"min": 0, "period": "month"}` | 허용 |
| `real_estate_assets` | `rental_deposit` | `integer` | `KRW` | `{"min": 0}` | 허용 |
| `real_estate_assets` | `secured_loan` | `integer` | `KRW` | `{"min": 0}` | 허용 |

---

## type: 부동산 유형
- section: `real_estate_assets`
- 자료형: `string`
- 단위: `none`
- 제약: `{"max_length": 50}`
- AI 변경: 허용된 draft 변경 대상
- 입력 규칙: 사용자의 표현을 의미가 바뀌지 않도록 canonical 값으로 정리한다.

---

## name: 부동산 이름
- section: `real_estate_assets`
- 자료형: `string`
- 단위: `none`
- 제약: `{"max_length": 200}`
- AI 변경: 허용된 draft 변경 대상
- 입력 규칙: 사용자의 표현을 의미가 바뀌지 않도록 canonical 값으로 정리한다.

---

## current_value: 현재 평가금액
- section: `real_estate_assets`
- 자료형: `integer`
- 단위: `KRW`
- 제약: `{"min": 0}`
- AI 변경: 허용된 draft 변경 대상
- 입력 규칙: 원 단위 정수로 정규화한다. 만 원 단위 표시값을 그대로 저장하지 않는다.

---

## acquisition_price: 취득가액
- section: `real_estate_assets`
- 자료형: `integer`
- 단위: `KRW`
- 제약: `{"min": 0}`
- AI 변경: 허용된 draft 변경 대상
- 입력 규칙: 원 단위 정수로 정규화한다. 만 원 단위 표시값을 그대로 저장하지 않는다.

---

## acquisition_date: 취득일
- section: `real_estate_assets`
- 자료형: `date`
- 단위: `date`
- 제약: `{}`
- AI 변경: 허용된 draft 변경 대상
- 입력 규칙: `YYYY-MM-DD` 형식을 사용한다.

---

## monthly_rental_income: 월 임대소득
- section: `real_estate_assets`
- 자료형: `integer`
- 단위: `KRW`
- 제약: `{"min": 0, "period": "month"}`
- AI 변경: 허용된 draft 변경 대상
- 입력 규칙: 원 단위 정수로 정규화한다. 만 원 단위 표시값을 그대로 저장하지 않는다.

---

## rental_deposit: 임대보증금
- section: `real_estate_assets`
- 자료형: `integer`
- 단위: `KRW`
- 제약: `{"min": 0}`
- AI 변경: 허용된 draft 변경 대상
- 입력 규칙: 원 단위 정수로 정규화한다. 만 원 단위 표시값을 그대로 저장하지 않는다.

---

## secured_loan: 담보대출금
- section: `real_estate_assets`
- 자료형: `integer`
- 단위: `KRW`
- 제약: `{"min": 0}`
- AI 변경: 허용된 draft 변경 대상
- 입력 규칙: 원 단위 정수로 정규화한다. 만 원 단위 표시값을 그대로 저장하지 않는다.
