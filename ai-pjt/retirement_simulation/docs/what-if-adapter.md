# What-if 변경 입력 어댑터

작성일: 2026-09-09. `feat/ai-retirement-analysis`의 2번 작업.
기준 규격은 [통합 호출·입출력 규격](analysis-contract.md)의 3절이다.

## 1. 구현 범위

공개 함수 `map_what_if_scenarios(values) -> tuple[WhatIfScenario, ...]`를
[what_if_adapter.py](../what_if_adapter.py)에 구현했다. 패키지 최상위에서 import할 수 있다.
변경 dict를 기존 불변 `WhatIfChanges`·`RetirementExpensePeriod`·`WhatIfScenario`로 변환한다.

- 저장 입력과 별도로 받은 변경 정의만 처리한다. 기준 프로필이나 시장 가정을 받지 않는다.
- 기존 입력 어댑터의 구조·정수 검사와 `InputMappingError`, 기존 엔진의 ID 검사·개수 상한을 재사용한다.
- 원본을 수정하거나 참조 컨테이너를 보관하지 않는다. 요청 순서와 생활비 행 순서를 유지한다.
- 프로필 적용·계산·난수 생성·DB/파일/네트워크/환경변수/시각 조회·로그 출력은 하지 않는다.
- 원본 입력은 호출 중에 다른 곳에서 수정하지 않아야 한다. 동시 수정이나 snapshot 일관성을 보장하지 않는다.

기존 모델·입력 어댑터·계산 엔진의 규칙/버전은 바꾸지 않았다. BE·FE·Agent·컨텍스트·
매뉴얼·공통 설정·의존성도 변경하지 않았다. 후속 3번에서
[통합 실행 함수·결과·실행 오류](analysis-execution.md), 후속 4번에서
[결과/오류 JSON 변환](analysis-output.md)까지 구현했다. **통합 CLI·서비스 연결은 아직 미구현**이며
이 매퍼가 HTTP API나 인증 검사를 대신하지 않는다.

## 2. 입력 규칙

| 위치 | 허용 값과 의미 |
|---|---|
| `values` | 0~10개 행의 list/tuple. `None`, 단독 dict, generator, 기존 모델 목록은 거절 |
| 시나리오 행 | `Mapping`. `scenario_id`, `changes` 모두 필수이며 추가 키 거절 |
| `scenario_id` | ASCII `[a-z][a-z0-9_-]{0,63}`, `baseline` 예약어, 배치 내 유일. 공백/대소문자 보정 없음 |
| `changes` | 아래 세 키만 허용하는 `Mapping`. `{}`는 명시적 무변경 비교, `None`은 오류 |
| `monthly_savings` | 생략/`None`은 유지. 0 이상 정수 원/월. 증액분이 아니라 변경 후 총액 |
| `retirement_age` | 생략/`None`은 유지. 18~100세 정수 |
| `retirement_expenses` | 생략/`None`은 기준 일정 유지. 명시적 값은 비어 있지 않은 기간 행 list/tuple로 전체 교체 |
| 생활비 행 | `from_age`, `to_age`, `monthly_amount`만 허용하고 모두 필수. 나이 1~120세, 시작≤종료, 월액 0 이상 정수 |

명시적 0은 유지한다. bool·실수·숫자 문자열·`Decimal`을 정수로 변환하지 않는다.
생활비 행의 null은 오류다. 변경 필드 자체의 null과 구분한다. Python 정수 금액의 상한을
새로 만들지 않았으며, 출력 계층의 JavaScript 안전 수치 제한은 이 매퍼의 책임이 아니다.
매핑 성공이 큰 금액의 계산/JSON 변환 성공까지 보장하지는 않는다.

`[]`와 `()`는 빈 tuple을 반환한다. 통합 실행에서는 기준 MC 모드 선택에 사용한다.
기존 `calculate_what_if()`는 여전히 **1~10개** 시나리오만 받으므로 빈 결과를 전달하지 않는다.
반대로 `[{'scenario_id': 'unchanged', 'changes': {}}]`는 하나의 무변경 비교 정의다.

### 적용 단계와의 경계

생활비 중복·은퇴 연도 공백·기준 현재 나이보다 이른 은퇴·이미 과거인 은퇴 나이의 변경·
종료 나이 초과는 `create_what_if_profile()`가 검사한다. 매퍼만 통과한 변경 정의는
유효한 적용 프로필이나 실행 가능한 시나리오가 확정된 상태가 아니다.

조기 은퇴와 전체 생활비를 함께 바꾸면 기존 함수가 동시에 적용한 뒤 검증한다.
기간 합산/병합·누락 연도 채우기·연금 이동은 없다. 각 변경안은 동일한 원래 기준에
독립 적용하며 앞 변경안의 프로필에 누적하지 않는다. 적용 프로필의 나이순 정렬과
입력 변경 정의의 요청 순서 보존도 구분한다.

## 3. 기존 엔진과 함께 사용하는 예시

Miniforge 환경을 활성화하고 저장소의 `ai-pjt/`에서 아래 Python 예시를 실행할 수 있다.
금액·가정은 손계산 검증용 합성값이며 운영 시장 가정이나 금융상품 추천이 아니다.
저장 원천의 필드 규칙과 필수 안내는 [입력 어댑터 가이드](input-adapter.md)를 따른다.

```python
from retirement_simulation import (
    calculate_what_if, map_retirement_profile,
    map_simulation_assumptions, map_what_if_scenarios,
)

saved_data = {
    "basic_info": {"age": 60, "retirement_age": 60, "end_age": 60},
    "financial_assets": [{
        "id": 1, "asset_type": "예금/적금",
        "current_value": 10_000, "monthly_savings": 0,
    }],
    "pension_assets": [],
    "goal_periods": [{
        "id": 1, "phase": "after", "from_age": 60, "to_age": 60,
        "items": [{"id": 1, "amount": 1_000}],
    }],
    "real_estate_assets": [], "liabilities": [], "goal_milestones": [],
}
# 합성 원천에서 연금이 실제로 없음을 알고 있다. 서비스의 없음 확인을 생략하는 예시가 아니다.
mapped = map_retirement_profile(saved_data, confirmed_empty_sections=("pension_assets",))
config = map_simulation_assumptions({
    "annual_return_mean": 0, "annual_return_volatility": 0,
    "annual_inflation_rate": 0, "num_paths": 2, "seed": 7,
})
definitions = map_what_if_scenarios([{
    "scenario_id": "lower_expenses",
    "changes": {"retirement_expenses": [
        {"from_age": 60, "to_age": 60, "monthly_amount": 800},
    ]},
}])
result = calculate_what_if(mapped.profile, config, definitions)
assert result.baseline.final_balance_percentiles.p50 == -2_000
assert result.scenarios[0].result.final_balance_percentiles.p50 == 400
assert result.scenarios[0].success_probability_delta_pp == 100
assert mapped.notices  # 후속 통합 결과에는 모든 안내와 mapping_version을 보존해야 한다.
```

기준 `10,000 − 12 × 1,000 = −2,000원`, 변경 `10,000 − 12 × 800 = 400원`이다.
같은 고정 경로 2개의 성공률은 0→1, 차이는 +100pp다. 이 예시는 기존 함수의 조합이며
`analyze_retirement()`를 구현한 것이 아니다. 실제 서비스에서는 요약 수치만 떼어 내지 말고
반영 범위 안내·가정·실행 출처 등을 함께 전달해야 한다.

## 4. 오류와 검증 순서

`InputMappingError`의 `code`, `field_name`, 고정 `message`를 사용한다.
알 수 없는 키 이름·입력값·시나리오 ID를 오류에 넣지 않는다. 경로는 고정 키와 배열 위치만
사용하고 추가 키 오류는 해당 객체까지만 표시한다. 원문 모델 예외의 chaining 표시도 억제한다.

| 코드 | 대표 원인·경로 |
|---|---|
| `invalid_type` | 목록/객체 구조 오류: `scenarios`, `scenarios[0].changes` |
| `missing_field` | 필수 키 누락: `scenarios[0].scenario_id` |
| `invalid_value` | 개수·ID 문법·나이·금액·역전 기간: `scenarios[0].changes.monthly_savings` |
| `missing_value` | 명시적 빈 생활비 목록: `scenarios[0].changes.retirement_expenses` |
| `duplicate_id` | 같은 ID의 두 번째 행: `scenarios[1].scenario_id` |
| `unknown_field` | 허용하지 않은 키: `scenarios[0].changes` |

여러 오류가 있으면 다음 순서의 첫 오류만 발생한다. dict 키 삽입 순서에 의존하지 않는다.

1. 최상위 list/tuple 여부와 개수 상한을 먼저 검사한다. 11개면 개별 행에 접근하지 않는다.
2. 각 행을 입력 순서대로 완전히 검사한 뒤 다음 행으로 이동한다.
3. 행의 Mapping 여부 → 추가 키 → `scenario_id` 존재 → `changes` 존재 → ID 문법 → 중복 ID.
4. `changes`의 Mapping 여부 → 추가 키 → 월 적립 → 은퇴 나이 → 생활비 목록.
5. 생활비는 목록 타입 → 비어 있음 → 행 순서대로 Mapping/추가 키 → 시작 나이 존재/값 →
   종료 나이 존재/값 → 시작≤종료 → 월액 존재/값을 검사한다.

실패하면 부분 결과는 반환하지 않으며 앞서 읽은 원본도 바꾸지 않는다. 필수 Python 인자 누락이나
미지원 keyword는 `TypeError`다. 기준 의존 적용 실패는 여전히 `ModelValidationError`이며,
통합 함수는 후속 3번에서 구현한 `RetirementAnalysisError`로 감싸며 이 매퍼의 독립 호출은
기존 오류를 유지한다. 출력 변환도 후속 4번에서 별도 함수로 구현했다.

## 5. 검증과 후속 작업

전용 [테스트](../tests/test_what_if_adapter.py)는 43개다. 구조/필수·추가 키/ID/개수,
생략/null/0/큰 정수/잘못된 자료형, 기간·나이 경계와 적용 책임, 순서·불변성·오류 경로/우선순위,
입력 비노출·I/O/난수/계산 미호출, 기존 저장 매퍼→실제 What-if 손계산을 검증한다.
[패키지 경계 테스트](../tests/test_package.py)에 새 export/import도 포함했다.

2026-09-09, Miniforge `retirewise-ai` / CPython 3.13.15, 2번(`9700c62`) 당시 검증 결과:

- 전용 **43개** 통과. 기존 404개를 포함한 전체 **447개**가 일반·`-S` 실행에서 모두 통과했다.
- 전체 447개를 역순으로 실행한 `-S` 검사도 통과했다. 기존 엔진·입력 매핑·CLI 회귀를 포함한다.
- 이 문서의 Python 예시를 실제 실행했고 출력 없이 손계산 assertion이 통과했다.
- 당시 신규 매퍼의 export를 확인했고 후속 통합 함수/모델/직렬화 함수는 존재하지 않음을 확인했다.
- 모듈 Python 31개 파일 구문과 로컬 Markdown 링크 219개·anchor 5개를 확인했다.
- 기존 Python 중 export/import 경계 테스트 2개를 제외한 27개는 `debddd4`와 byte 단위 동일하다.
- 변경 8개 파일 모두 시뮬레이션 모듈 내부다. 새 파일은 매퍼·전용 테스트·이 가이드뿐이다.

저장소 루트에서 Miniforge 환경으로 재실행한다.

```bash
conda run -n retirewise-ai python -S -B -m unittest discover -s ai-pjt/retirement_simulation/tests -p test_what_if_adapter.py -v
conda run -n retirewise-ai python -B -m unittest discover -s ai-pjt/retirement_simulation/tests -q
conda run -n retirewise-ai python -S -B -m unittest discover -s ai-pjt/retirement_simulation/tests -q
```

같은 브랜치의 **3번 통합 실행 함수·불변 결과·실행 오류 및 4번 결과/오류 JSON 변환까지 완료**했다.
후속 5번의 [합성 성능 측정·초기 연동 후보 한도](analysis-performance.md)까지 완료했다.
다음은 6번 통합 샘플/인수인계다.
독립 모듈 검증은 실제 DB/API/FE/배포 검증을 대신하지 않는다.
