# 재무 만족도 인터페이스 가이드
step_id: financial-satisfaction
registry_source: backend-pjt/consulting/field_registry.py@ef3f8b7

재무 영역별 만족도를 1~5점으로 정리한다.
필드명·자료형·단위·제약은 canonical 필드 레지스트리를 그대로 따른다. 외부 금융 통계나 권장값은 이 문서에 넣지 않는다.

| section | field_name | 자료형 | 단위 | 제약 | AI 변경 |
|---|---|---|---|---|---|
| `financial_satisfaction` | `cashflow_score` | `integer` | `score` | `{"max": 5, "min": 1}` | 허용 |
| `financial_satisfaction` | `risk_score` | `integer` | `score` | `{"max": 5, "min": 1}` | 허용 |
| `financial_satisfaction` | `investment_score` | `integer` | `score` | `{"max": 5, "min": 1}` | 허용 |
| `financial_satisfaction` | `management_score` | `integer` | `score` | `{"max": 5, "min": 1}` | 허용 |
| `financial_satisfaction` | `inheritance_score` | `integer` | `score` | `{"max": 5, "min": 1}` | 허용 |

---

## cashflow_score: 현금흐름 관리 만족도
- section: `financial_satisfaction`
- 자료형: `integer`
- 단위: `score`
- 제약: `{"max": 5, "min": 1}`
- AI 변경: 허용된 draft 변경 대상
- 입력 규칙: 사용자의 표현을 의미가 바뀌지 않도록 canonical 값으로 정리한다.

---

## risk_score: 위험 관리 만족도
- section: `financial_satisfaction`
- 자료형: `integer`
- 단위: `score`
- 제약: `{"max": 5, "min": 1}`
- AI 변경: 허용된 draft 변경 대상
- 입력 규칙: 사용자의 표현을 의미가 바뀌지 않도록 canonical 값으로 정리한다.

---

## investment_score: 투자 만족도
- section: `financial_satisfaction`
- 자료형: `integer`
- 단위: `score`
- 제약: `{"max": 5, "min": 1}`
- AI 변경: 허용된 draft 변경 대상
- 입력 규칙: 사용자의 표현을 의미가 바뀌지 않도록 canonical 값으로 정리한다.

---

## management_score: 재무관리 만족도
- section: `financial_satisfaction`
- 자료형: `integer`
- 단위: `score`
- 제약: `{"max": 5, "min": 1}`
- AI 변경: 허용된 draft 변경 대상
- 입력 규칙: 사용자의 표현을 의미가 바뀌지 않도록 canonical 값으로 정리한다.

---

## inheritance_score: 상속 만족도
- section: `financial_satisfaction`
- 자료형: `integer`
- 단위: `score`
- 제약: `{"max": 5, "min": 1}`
- AI 변경: 허용된 draft 변경 대상
- 입력 규칙: 사용자의 표현을 의미가 바뀌지 않도록 canonical 값으로 정리한다.
