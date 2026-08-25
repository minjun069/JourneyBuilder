# input_assist Agent — LLM 발생 가능 에러 및 대처 방안

## 개요

input_assist 파이프라인은 5개의 LLM 호출 노드로 구성됩니다.
각 노드에서 발생 가능한 LLM 오류와 현재 설계상의 대처 방안을 정리합니다.

```
classify → identify → next_field → error_check_1 → resolve → error_check_2 → merge
                                         ↑__________________________|  (재시도, MAX_RETRIES=2)
```

---

## 노드별 에러 분석

### 1. classify

**출력**: `intent: "fill_avg" | "find_field" | "organize"`

| 에러 | 원인 | 영향 |
|------|------|------|
| 의도 오분류 | 사용자 발화가 모호하거나 복합 의도를 포함 | 잘못된 intent_instruction으로 resolve 수행 |

**대처 방안**
- 현재: 없음 (단일 호출, 검증 없음)
- 오분류된 intent는 `_resolve`의 `intent_instruction`에 반영되어 잘못된 방식으로 값을 추출하게 됨
- `_error_check_2`에서 `unreasonable` 또는 `hallucination`으로 걸러질 수 있으나 보장 없음
- **개선 고려**: 낮은 신뢰도 케이스를 위한 분류 재확인 또는 사용자 확인 요청

---

### 2. identify

**출력**: `items: [{"field_name": str, "excerpt": str}, ...]`

| 에러 | 원인 | 영향 |
|------|------|------|
| 존재하지 않는 field_name 반환 | 가이드라인에 없는 필드명 생성 | error_check_1 → error_check_2에서 unreasonable로 실패 처리 |
| 잘못된 field_name 배정 | 발화 내용과 맞지 않는 항목에 배정 | error_check_1에서 교정 시도 |
| excerpt 부정확 | 관련 없는 내용을 발췌하거나 누락 | resolve에서 잘못된 값 추출 |
| 과잉 추출 | 관련 없는 항목까지 포함 | 불필요한 필드가 처리되어 의도치 않은 입력 발생 |

**대처 방안**
- `_error_check_1`이 field_name 오배정을 CoT로 교정
- 교정 후에도 잘못된 경우 `_error_check_2`에서 최종 차단

---

### 3. error_check_1

**출력**: `corrected_field_name: str` (CoT: `error_analysis` 생성 후 폐기)

| 에러 | 원인 | 영향 |
|------|------|------|
| corrected_field_name도 잘못됨 | CoT 품질 불량 또는 가이드라인 오해 | 잘못된 field_name으로 resolve 진행 |
| 올바른 배정을 잘못 교정 | 과잉 수정 | 원래 맞는 배정을 틀린 것으로 변경 |
| 가이드라인에 없는 field_name 반환 | 환각 | resolve에서 RAG 검색 실패, 빈 field_spec으로 진행 |

**대처 방안**
- 현재: 교정된 field_name이 form_fields에 없어도 그대로 진행
- `_resolve`에서 해당 field_name으로 RAG 검색 → 명세서를 못 찾을 경우 resolve 품질 저하
- `_error_check_2`에서 `unreasonable`로 최종 실패 처리됨

---

### 4. resolve

**출력**: `value: str | int | float`

| 에러 | 원인 | 영향 |
|------|------|------|
| constraints 범위 초과 | 프롬프트 규칙 미준수 | error_check_2에서 type_unit 또는 unreasonable 판정 |
| 잘못된 타입 반환 | 숫자 항목에 문자열 반환 등 | error_check_2에서 type_unit 판정 |
| 근거 없는 값 생성 (hallucination) | field_spec 미준수, 임의값 생성 | error_check_2에서 hallucination 판정 |
| 선택지 외 값 반환 | select 타입 항목에서 유효하지 않은 선택지 | error_check_2에서 type_unit 판정 |

**대처 방안**
- `_error_check_2` → `_resolve` 재시도 루프 (MAX_RETRIES=2, 총 3회 시도)
- 재시도 시 `validation_reason`이 `{hint}`로 resolve 프롬프트에 포함되어 이전 실패 원인을 반영
- MAX_RETRIES 초과 시 해당 항목 `success: False`로 partial_results에 기록, merge에서 실패 안내

---

### 5. error_check_2

**출력**: `error_type: "ok" | "hallucination" | "unorganized" | "type_unit" | "unreasonable"`

| 에러 | 원인 | 영향 |
|------|------|------|
| False negative (ok 오판정) | 검증 기준 미달 | 잘못된 값이 form에 입력됨 — **가장 치명적** |
| False positive (오류 오판정) | 유효한 값을 오류로 판단 | MAX_RETRIES 소진 후 실패 처리, 사용자 직접 입력 필요 |
| 부정확한 error_type 분류 | 오류 유형을 잘못 판단 | hint 내용이 부정확해져 재시도 효과 감소 |

**대처 방안**
- 현재: `error_check_2` 자체에 대한 재검증 없음
- False negative가 통과된 경우 `_merge`의 응답에서 사용자가 확인할 수 있으나 완전한 보장은 없음
- **개선 고려**: 주요 항목(금액, 날짜 등)에 대해 후처리 코드 레벨 검증 추가

---

### 6. merge

**출력**: 자연어 안내 텍스트

| 에러 | 원인 | 영향 |
|------|------|------|
| 처리 내용 오설명 | partial_results 해석 오류 | 사용자에게 잘못된 안내 전달 |
| 실패 항목 미언급 | 프롬프트 지시 미준수 | 사용자가 실패 항목을 인지 못함 |

**대처 방안**
- 현재: `partial_results`에 `success`, `reason`이 구조화되어 있어 LLM이 참조 가능
- merge 오류는 기능적 영향(잘못된 입력)이 아닌 UX 영향에 그침

---

## 에러 전파 요약

```
classify 오분류
  └─ resolve에서 잘못된 방식으로 값 추출
      └─ error_check_2가 재시도 또는 실패 처리

identify 잘못된 field_name
  └─ error_check_1이 교정
      └─ 교정 실패 시 → resolve → error_check_2 재시도 → MAX_RETRIES 초과 → 실패 처리

resolve 잘못된 값
  └─ error_check_2 판정 → 재시도 (최대 2회)
      └─ MAX_RETRIES 초과 → partial_results에 success: False 기록
          └─ merge에서 사용자에게 실패 안내

error_check_2 false negative (가장 치명적)
  └─ 잘못된 값이 update dict에 포함되어 프론트에 전달됨
```

---

## 현재 미대응 케이스

1. **error_check_2 false negative**: 구조적으로 통과되면 막을 방법 없음. 코드 레벨 후처리 검증 고려 필요.
2. **error_check_1이 잘못 교정한 경우**: field_name 유효성 검사 없이 그대로 진행됨.
3. **classify 오분류**: 재분류 메커니즘 없음.


+ 아직 테스트 안해봄
