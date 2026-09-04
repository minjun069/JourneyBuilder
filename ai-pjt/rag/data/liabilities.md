# 부채 인터페이스 가이드
step_id: liabilities
registry_source: backend-pjt/consulting/field_registry.py@ef3f8b7

부채 목록의 잔액·금리·상환 조건을 정리한다.
필드명·자료형·단위·제약은 canonical 필드 레지스트리를 그대로 따른다. 외부 금융 통계나 권장값은 이 문서에 넣지 않는다.

| section | field_name | 자료형 | 단위 | 제약 | AI 변경 |
|---|---|---|---|---|---|
| `liabilities` | `type` | `string` | `none` | `{"max_length": 50}` | 허용 |
| `liabilities` | `name` | `string` | `none` | `{"max_length": 200}` | 허용 |
| `liabilities` | `balance` | `integer` | `KRW` | `{"min": 0}` | 허용 |
| `liabilities` | `interest_rate` | `decimal` | `percent` | `{"max": 30, "min": 0}` | 허용 |
| `liabilities` | `rate_type` | `string` | `none` | `{"choices": ["고정", "변동"]}` | 허용 |
| `liabilities` | `base_rate` | `string` | `none` | `{"max_length": 20}` | 허용 |
| `liabilities` | `repayment_method` | `string` | `none` | `{"max_length": 50}` | 허용 |
| `liabilities` | `loan_date` | `date` | `date` | `{}` | 허용 |
| `liabilities` | `maturity_date` | `date` | `date` | `{}` | 허용 |
| `liabilities` | `grace_period_years` | `integer` | `year` | `{"max": 10, "min": 0}` | 허용 |

---

## type: 부채 유형
- section: `liabilities`
- 자료형: `string`
- 단위: `none`
- 제약: `{"max_length": 50}`
- AI 변경: 허용된 draft 변경 대상
- 입력 규칙: 사용자의 표현을 의미가 바뀌지 않도록 canonical 값으로 정리한다.

---

## name: 대출 상품명
- section: `liabilities`
- 자료형: `string`
- 단위: `none`
- 제약: `{"max_length": 200}`
- AI 변경: 허용된 draft 변경 대상
- 입력 규칙: 사용자의 표현을 의미가 바뀌지 않도록 canonical 값으로 정리한다.

---

## balance: 대출잔액
- section: `liabilities`
- 자료형: `integer`
- 단위: `KRW`
- 제약: `{"min": 0}`
- AI 변경: 허용된 draft 변경 대상
- 입력 규칙: 원 단위 정수로 정규화한다. 만 원 단위 표시값을 그대로 저장하지 않는다.

---

## interest_rate: 대출금리
- section: `liabilities`
- 자료형: `decimal`
- 단위: `percent`
- 제약: `{"max": 30, "min": 0}`
- AI 변경: 허용된 draft 변경 대상
- 입력 규칙: 백분율 숫자를 사용한다. 예를 들어 4.5%는 `4.5`다.

---

## rate_type: 금리유형
- section: `liabilities`
- 자료형: `string`
- 단위: `none`
- 제약: `{"choices": ["고정", "변동"]}`
- AI 변경: 허용된 draft 변경 대상
- 입력 규칙: 사용자의 표현을 의미가 바뀌지 않도록 canonical 값으로 정리한다.

---

## base_rate: 기준금리
- section: `liabilities`
- 자료형: `string`
- 단위: `none`
- 제약: `{"max_length": 20}`
- AI 변경: 허용된 draft 변경 대상
- 입력 규칙: 사용자의 표현을 의미가 바뀌지 않도록 canonical 값으로 정리한다.

---

## repayment_method: 상환방식
- section: `liabilities`
- 자료형: `string`
- 단위: `none`
- 제약: `{"max_length": 50}`
- AI 변경: 허용된 draft 변경 대상
- 입력 규칙: 사용자의 표현을 의미가 바뀌지 않도록 canonical 값으로 정리한다.

---

## loan_date: 대출일
- section: `liabilities`
- 자료형: `date`
- 단위: `date`
- 제약: `{}`
- AI 변경: 허용된 draft 변경 대상
- 입력 규칙: `YYYY-MM-DD` 형식을 사용한다.

---

## maturity_date: 만기일
- section: `liabilities`
- 자료형: `date`
- 단위: `date`
- 제약: `{}`
- AI 변경: 허용된 draft 변경 대상
- 입력 규칙: `YYYY-MM-DD` 형식을 사용한다.

---

## grace_period_years: 거치기간
- section: `liabilities`
- 자료형: `integer`
- 단위: `year`
- 제약: `{"max": 10, "min": 0}`
- AI 변경: 허용된 draft 변경 대상
- 입력 규칙: 사용자의 표현을 의미가 바뀌지 않도록 canonical 값으로 정리한다.
