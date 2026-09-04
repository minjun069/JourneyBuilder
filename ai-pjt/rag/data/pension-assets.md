# 연금자산 인터페이스 가이드
step_id: pension-assets
registry_source: backend-pjt/consulting/field_registry.py@ef3f8b7

사적 연금자산 목록의 가입·납입·수령 정보를 정리한다.
필드명·자료형·단위·제약은 canonical 필드 레지스트리를 그대로 따른다. 외부 금융 통계나 권장값은 이 문서에 넣지 않는다.

| section | field_name | 자료형 | 단위 | 제약 | AI 변경 |
|---|---|---|---|---|---|
| `pension_assets` | `type` | `string` | `none` | `{"max_length": 50}` | 허용 |
| `pension_assets` | `name` | `string` | `none` | `{"max_length": 100}` | 허용 |
| `pension_assets` | `current_value` | `integer` | `KRW` | `{"min": 0}` | 허용 |
| `pension_assets` | `monthly_savings` | `integer` | `KRW` | `{"min": 0, "period": "month"}` | 허용 |
| `pension_assets` | `expected_return_rate` | `decimal` | `percent` | `{"max": 20, "min": 0}` | 허용 |
| `pension_assets` | `subscription_date` | `date` | `date` | `{}` | 허용 |
| `pension_assets` | `payment_period_years` | `integer` | `year` | `{"max": 50, "min": 1}` | 허용 |
| `pension_assets` | `receive_method` | `string` | `none` | `{"choices": ["연금형", "일시금"]}` | 허용 |
| `pension_assets` | `pension_start_age` | `integer` | `age` | `{"max": 80, "min": 55}` | 허용 |
| `pension_assets` | `pension_period` | `string` | `none` | `{"max_length": 50}` | 허용 |
| `pension_assets` | `expected_amount` | `integer` | `KRW` | `{"min": 0, "period": "month"}` | 허용 |

---

## type: 연금 유형
- section: `pension_assets`
- 자료형: `string`
- 단위: `none`
- 제약: `{"max_length": 50}`
- AI 변경: 허용된 draft 변경 대상
- 입력 규칙: 사용자의 표현을 의미가 바뀌지 않도록 canonical 값으로 정리한다.

---

## name: 상품명
- section: `pension_assets`
- 자료형: `string`
- 단위: `none`
- 제약: `{"max_length": 100}`
- AI 변경: 허용된 draft 변경 대상
- 입력 규칙: 사용자의 표현을 의미가 바뀌지 않도록 canonical 값으로 정리한다.

---

## current_value: 현재 평가금액
- section: `pension_assets`
- 자료형: `integer`
- 단위: `KRW`
- 제약: `{"min": 0}`
- AI 변경: 허용된 draft 변경 대상
- 입력 규칙: 원 단위 정수로 정규화한다. 만 원 단위 표시값을 그대로 저장하지 않는다.

---

## monthly_savings: 월 적립액
- section: `pension_assets`
- 자료형: `integer`
- 단위: `KRW`
- 제약: `{"min": 0, "period": "month"}`
- AI 변경: 허용된 draft 변경 대상
- 입력 규칙: 원 단위 정수로 정규화한다. 만 원 단위 표시값을 그대로 저장하지 않는다.

---

## expected_return_rate: 기대수익률
- section: `pension_assets`
- 자료형: `decimal`
- 단위: `percent`
- 제약: `{"max": 20, "min": 0}`
- AI 변경: 허용된 draft 변경 대상
- 입력 규칙: 백분율 숫자를 사용한다. 예를 들어 4.5%는 `4.5`다.

---

## subscription_date: 가입일
- section: `pension_assets`
- 자료형: `date`
- 단위: `date`
- 제약: `{}`
- AI 변경: 허용된 draft 변경 대상
- 입력 규칙: `YYYY-MM-DD` 형식을 사용한다.

---

## payment_period_years: 납입기간
- section: `pension_assets`
- 자료형: `integer`
- 단위: `year`
- 제약: `{"max": 50, "min": 1}`
- AI 변경: 허용된 draft 변경 대상
- 입력 규칙: 사용자의 표현을 의미가 바뀌지 않도록 canonical 값으로 정리한다.

---

## receive_method: 수령방식
- section: `pension_assets`
- 자료형: `string`
- 단위: `none`
- 제약: `{"choices": ["연금형", "일시금"]}`
- AI 변경: 허용된 draft 변경 대상
- 입력 규칙: 사용자의 표현을 의미가 바뀌지 않도록 canonical 값으로 정리한다.

---

## pension_start_age: 연금 개시 연령
- section: `pension_assets`
- 자료형: `integer`
- 단위: `age`
- 제약: `{"max": 80, "min": 55}`
- AI 변경: 허용된 draft 변경 대상
- 입력 규칙: 만 나이 정수를 사용한다.

---

## pension_period: 연금 기간
- section: `pension_assets`
- 자료형: `string`
- 단위: `none`
- 제약: `{"max_length": 50}`
- AI 변경: 허용된 draft 변경 대상
- 입력 규칙: 사용자의 표현을 의미가 바뀌지 않도록 canonical 값으로 정리한다.

---

## expected_amount: 예상 수령액
- section: `pension_assets`
- 자료형: `integer`
- 단위: `KRW`
- 제약: `{"min": 0, "period": "month"}`
- AI 변경: 허용된 draft 변경 대상
- 입력 규칙: 원 단위 정수로 정규화한다. 만 원 단위 표시값을 그대로 저장하지 않는다.
