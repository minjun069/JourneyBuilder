# 기본 정보 인터페이스 가이드
step_id: basic-info
registry_source: backend-pjt/consulting/field_registry.py@ef3f8b7

고객의 기본 인적·소득·은퇴 계획 정보를 canonical 필드로 정리한다.
필드명·자료형·단위·제약은 canonical 필드 레지스트리를 그대로 따른다. 외부 금융 통계나 권장값은 이 문서에 넣지 않는다.

| section | field_name | 자료형 | 단위 | 제약 | AI 변경 |
|---|---|---|---|---|---|
| `basic_info` | `age` | `integer` | `age` | `{"max": 100, "min": 18}` | 허용 |
| `basic_info` | `job` | `string` | `none` | `{"choices": ["직장인", "공무원", "자영업자", "프리랜서", "전업주부", "학생", "퇴직자", "기타"]}` | 허용 |
| `basic_info` | `marital_status` | `string` | `none` | `{"choices": ["미혼", "기혼"]}` | 허용 |
| `basic_info` | `dependents` | `object` | `none` | `{}` | 허용 |
| `basic_info` | `dependents.children[].age` | `integer` | `age` | `{"max": 40, "min": 0}` | 읽기 전용 |
| `basic_info` | `dependents.parents[].age` | `integer` | `age` | `{"max": 120, "min": 0}` | 읽기 전용 |
| `basic_info` | `dependents.others[].name` | `string` | `none` | `{}` | 읽기 전용 |
| `basic_info` | `dependents.others[].age` | `integer` | `age` | `{"max": 120, "min": 0}` | 읽기 전용 |
| `basic_info` | `tendency` | `array` | `none` | `{}` | 허용 |
| `basic_info` | `interest` | `string` | `none` | `{"max_length": 200}` | 허용 |
| `basic_info` | `need` | `string` | `none` | `{"choices": ["은퇴", "투자", "주택자금", "교육자금", "보장", "기타"]}` | 허용 |
| `basic_info` | `monthly_income` | `integer` | `KRW` | `{"min": 0, "period": "month"}` | 허용 |
| `basic_info` | `retirement_age` | `integer` | `age` | `{"max": 100, "min": 18}` | 허용 |
| `basic_info` | `end_age` | `integer` | `age` | `{"max": 120, "min": 18}` | 허용 |

---

## age: 나이
- section: `basic_info`
- 자료형: `integer`
- 단위: `age`
- 제약: `{"max": 100, "min": 18}`
- AI 변경: 허용된 draft 변경 대상
- 입력 규칙: 만 나이 정수를 사용한다.

---

## job: 직업
- section: `basic_info`
- 자료형: `string`
- 단위: `none`
- 제약: `{"choices": ["직장인", "공무원", "자영업자", "프리랜서", "전업주부", "학생", "퇴직자", "기타"]}`
- AI 변경: 허용된 draft 변경 대상
- 입력 규칙: 사용자의 표현을 의미가 바뀌지 않도록 canonical 값으로 정리한다.

---

## marital_status: 혼인 상태
- section: `basic_info`
- 자료형: `string`
- 단위: `none`
- 제약: `{"choices": ["미혼", "기혼"]}`
- AI 변경: 허용된 draft 변경 대상
- 입력 규칙: 사용자의 표현을 의미가 바뀌지 않도록 canonical 값으로 정리한다.

---

## dependents: 부양가족
- section: `basic_info`
- 자료형: `object`
- 단위: `none`
- 제약: `{}`
- AI 변경: 허용된 draft 변경 대상
- 입력 규칙: 현재 section의 canonical 구조를 유지하며 임의 키를 추가하지 않는다.

---

## dependents.children[].age: 자녀 나이
- section: `basic_info`
- 자료형: `integer`
- 단위: `age`
- 제약: `{"max": 40, "min": 0}`
- AI 변경: 읽기 전용이며 draft 변경 대상이 아님
- 입력 규칙: 만 나이 정수를 사용한다.

---

## dependents.parents[].age: 부모 나이
- section: `basic_info`
- 자료형: `integer`
- 단위: `age`
- 제약: `{"max": 120, "min": 0}`
- AI 변경: 읽기 전용이며 draft 변경 대상이 아님
- 입력 규칙: 만 나이 정수를 사용한다.

---

## dependents.others[].name: 기타 부양가족 관계
- section: `basic_info`
- 자료형: `string`
- 단위: `none`
- 제약: `{}`
- AI 변경: 읽기 전용이며 draft 변경 대상이 아님
- 입력 규칙: 사용자의 표현을 의미가 바뀌지 않도록 canonical 값으로 정리한다.

---

## dependents.others[].age: 기타 부양가족 나이
- section: `basic_info`
- 자료형: `integer`
- 단위: `age`
- 제약: `{"max": 120, "min": 0}`
- AI 변경: 읽기 전용이며 draft 변경 대상이 아님
- 입력 규칙: 만 나이 정수를 사용한다.

---

## tendency: 재무 성향
- section: `basic_info`
- 자료형: `array`
- 단위: `none`
- 제약: `{}`
- AI 변경: 허용된 draft 변경 대상
- 입력 규칙: 현재 section의 canonical 구조를 유지하며 임의 키를 추가하지 않는다.

---

## interest: 주요 관심사
- section: `basic_info`
- 자료형: `string`
- 단위: `none`
- 제약: `{"max_length": 200}`
- AI 변경: 허용된 draft 변경 대상
- 입력 규칙: 사용자의 표현을 의미가 바뀌지 않도록 canonical 값으로 정리한다.

---

## need: 주요 니즈
- section: `basic_info`
- 자료형: `string`
- 단위: `none`
- 제약: `{"choices": ["은퇴", "투자", "주택자금", "교육자금", "보장", "기타"]}`
- AI 변경: 허용된 draft 변경 대상
- 입력 규칙: 사용자의 표현을 의미가 바뀌지 않도록 canonical 값으로 정리한다.

---

## monthly_income: 세후 월 소득
- section: `basic_info`
- 자료형: `integer`
- 단위: `KRW`
- 제약: `{"min": 0, "period": "month"}`
- AI 변경: 허용된 draft 변경 대상
- 입력 규칙: 원 단위 정수로 정규화한다. 만 원 단위 표시값을 그대로 저장하지 않는다.

---

## retirement_age: 은퇴 예정 나이
- section: `basic_info`
- 자료형: `integer`
- 단위: `age`
- 제약: `{"max": 100, "min": 18}`
- AI 변경: 허용된 draft 변경 대상
- 입력 규칙: 만 나이 정수를 사용한다.

---

## end_age: 계획 종료 나이
- section: `basic_info`
- 자료형: `integer`
- 단위: `age`
- 제약: `{"max": 120, "min": 18}`
- AI 변경: 허용된 draft 변경 대상
- 입력 규칙: 만 나이 정수를 사용한다.
