# 저장 입력 어댑터 사용 가이드

상태: **입력 어댑터·기본 테스트·확대 경계 검증·전용 연동 샘플 완료.**
실제 BE 데이터 조회·API·FE 연결·배포·시장 가정 설정은 구현하지 않았다.
구현은 [input_adapter.py](../input_adapter.py), 정책은 [입력 매핑 규격](input-mapping-rules.md),
기본 검증은 [test_input_adapter.py](../tests/test_input_adapter.py)에 있다.
조합형 독립 검산·수치/기간/상태 격리와 재실행 결과는
[확대 경계 검증 기록](input-adapter-validation.md)을 참고한다.

## 1. 공개 Python 인터페이스

| 인터페이스 | 반환·역할 |
|---|---|
| `map_retirement_profile(saved_data, *, confirmed_empty_sections=())` | 저장형 mapping을 `ProfileMappingResult`로 변환. 계산·DB 조회·폼 병합은 하지 않음 |
| `map_simulation_assumptions(values)` | 명시적 가정 5개를 기존 `SimulationAssumptions`로 검증. 기본값 주입·percent 추정 없음 |
| `ProfileMappingResult` | `profile`, `notices`, `mapping_version="retirement-input-v1"`. frozen keyword dataclass |
| `InputMappingNotice` | `code`, `field_name`. 허용 코드와 원천 경로만 담는 frozen keyword dataclass |
| `InputMappingError` | 변환 실패의 `code`, `field_name`, 고정 `message`. `ValueError` 하위 클래스 |

모두 `retirement_simulation`에서 import한다. 별도 웹 endpoint나 Agent 도구가 아니다.
기존 `RetirementProfile`, MC/What-if 계산식·출력·모델 버전은 변경하지 않았다.
매핑 버전은 계산 모델 버전과 별개이며 결과를 재현하려면 둘 다 고려해야 한다.

입력은 `customer_info` 전체가 아닌 `customer_info["saved_data"]`에 대응한다.
계산 가정은 그 객체에 섞지 않고 별도로 전달한다. 최상위 객체와 행은 Python `Mapping`,
배열은 list/tuple을 받는다. ORM/iterator/generator는 받지 않는다.
`basic_info`와 6개 필수 목록은 규격대로 요구한다. 사용하지 않는 추가 저장 필드는
허용하지만 이름·상품명·ID·미저장 폼·현재 소득/지출을 계산 모델에 복사하지 않는다.

`confirmed_empty_sections`는 `financial_assets`/`pension_assets`의 **실제 없음이 확인된 경우만**
문자열 list/tuple로 전달한다. 자동 확인·누락 보충·클라이언트 신뢰 우회용 옵션이 아니다.
이 사실을 얻을 실제 서비스 수단은 아직 정해지지 않았다. 모듈은 인증/증거를 조회하지 않는다.

## 2. 검증 순서와 오류

1. 최상위/기본 객체·필수 목록·행 구조·목록 내 ID, 빈 목록 확인의 형식과 모순을 검사한다.
2. 기본 나이와 종료 조건, 금융자산 유형을 검사한다. 모델링하는 5종은 평가액·월 적립액을
   검증하고 합산한다. `기타`는 금액을 읽지 않고 합산에서 제외하며 원본 행 위치의 안내를 남긴다.
3. 목표 기간의 phase·항목 구조·전체 항목 ID를 검사하고 `after` 월액만 합산한다.
   `before`는 항목 구조/ID만 확인하며 사용하지 않는 나이·금액은 계산 검증하지 않는다.
4. 연금 수령방식·나이·예상액·기간을 모두 확인한 뒤 범위 밖 일정 제외/기간 제한을 한다.
5. 기존 `RetirementProfile`에서 생활비 정렬·중복·은퇴 연도 coverage를 최종 검증한다.

검증 오류가 하나라도 발생하면 결과를 반환하지 않는다. 원본을 수정하거나 오류 행만 버린 채
계산하지 않는다. 여러 오류가 있으면 위 순서에서 처음 발견한 오류를 반환하므로 전체 오류 목록은 아니다.
`기타`의 명시적 모델링 제외는 오류 행을 임의로 건너뛰는 처리와 다르며, 미지원 문자열은 여전히 오류다.
원본 목록 순서를 오류 경로의 index로 사용하고 DB ID·고객 입력 문자열은 경로에 넣지 않는다.

| 오류 코드 | 대표 의미·경로 |
|---|---|
| `missing_field` | 필수 키 누락. 예: `financial_assets[0].monthly_savings` |
| `missing_value` | 필수 텍스트 null/빈 문자열, `after`의 빈 items |
| `invalid_type` | 잘못된 객체·배열·행 또는 수령 문자열 자료형 |
| `invalid_value` | 금액/나이/ID/가정의 자료형·범위 오류, 기간 시작>끝 |
| `unsupported_value` | 미지원 자산 유형·phase·수령방식·기간 문법 |
| `duplicate_id` | 같은 원천 목록 또는 전체 목표 항목에서 ID 중복 |
| `empty_confirmation_required` | 금융자산/연금 빈 목록에 확인 근거 없음 |
| `invalid_confirmation` | 잘못된 확인 이름·중복·비어 있지 않은 목록과 모순 |
| `invalid_profile` | 내부 모델의 최종 검증 실패. 생활비 중복/공백은 `goal_periods` |
| `unknown_field` | 가정에 미지원 키 존재. 그 키를 노출하지 않고 `assumptions` 반환 |

계산에 사용하는 금액의 `None`을 숫자 0으로 보충하지 않는다. 숫자 null/bool/문자열도
`invalid_value`다. `기타`의 미사용 금액은 누락·null을 포함해 계산 검증 대상이 아니지만,
행 구조·ID 검증은 그대로 수행한다. 상세 범위는 [금융자산 매핑 규칙](input-mapping-rules.md)을 따른다.
가정은 알 수 없는 키부터 검사하고 고정된 필드 순서로 필수값을 읽어 내부 모델에 전달한다.
내부 `ModelValidationError`는 고정 매핑 오류로 변환하고 원래 예외 표시를 억제한다.
HTTP 상태/envelope·사용자 안내 문구·재시도 처리는 후속 서비스 연결부의 책임이다.
traceback의 로컬 변수까지 외부 로그/응답에 기록해서는 안 된다.

호출 인자 누락·미지원 keyword는 Python의 `TypeError`다. 결과/안내 dataclass를 직접
잘못 생성한 경우에는 기존 규칙대로 `ModelValidationError`다. 직접 생성은 검증된
원천이나 서비스 승인 증명이 아니므로 실제 변환에는 위 두 함수를 사용한다.

## 3. 반영 범위 안내

`notices`는 옵션 출력이 아니다. 후속 통합 결과 변환과 BE/FE 연결에서도 보존하고
안내해야 한다. 금융자산 기반 결과를 전체 은퇴 재무상태의 성공확률이라고 표시하지 않는다.

| 코드 | 발생 조건·해석 |
|---|---|
| `partial_financial_scope` | 항상. 입력된 유동 금융자산·적립·생활비·월 연금에 한정 |
| `single_portfolio_assumption` | 항상. 하나의 포트폴리오, 개별 자산 유동성·상관관계 미모델링 |
| `pre_retirement_cashflow_not_recalculated` | 항상. 월 적립 총액 사용, 소득/현재 지출/은퇴 전 목표 재계산 없음 |
| `pension_account_not_modeled` | 연금 행 존재. 입력된 수령액만 사용, 계좌 평가·납입·인출·급여 추정 없음 |
| `real_estate_not_modeled` | 부동산 행 존재. 평가/처분·임대료·보증금·담보대출 미반영 |
| `liabilities_not_modeled` | 부채 행 존재. 원금·이자·상환 현금흐름 미반영 |
| `milestones_not_modeled` | 일회성 목표 행 존재. 월 생활비 변환/별도 현금흐름 반영 없음 |
| `confirmed_empty_financial_assets` | 금융자산 없음 확인 후 자산/적립 0 적용 |
| `confirmed_empty_pensions` | 연금 없음 확인 후 빈 일정 적용 |
| `other_financial_asset_not_modeled` | `기타` 금융자산 행마다 생성. 평가액·월 적립액 모두 제외, 원본 위치 `financial_assets[N]` 보존 |
| `pension_outside_horizon` | 원천 연금 행은 유효하지만 현재~종료 나이에 수령하지 않음 |
| `pension_horizon_limited` | 종신 또는 유한 기간이 계획 끝을 초과하여 종료 나이까지만 반영 |

공통 3개 → 원천 존재에 따른 제외 안내 → 없음 확인 안내 → 기타 금융자산 행별 안내 →
연금 행별 기간 안내 순서다. 각 행별 안내는 해당 원본 배열의 순서를 유지한다.
범위 밖 연금 행에는 `pension_outside_horizon`만 추가하고 기간 제한 안내를 중복하지 않는다.
연금 관련 마지막 두 안내의 경로는 `pension_assets[0]`처럼 원천 행 위치이며 원천 ID가 아니다.
서로 다른 연금의 같은 일정은 유지하고, 목표 생활비 구간끼리의 중복은 거절한다.

`기타`만 있는 금융자산 목록은 모델링 자산·적립 합계가 0이지만 실제 없음으로 확정한
목록이 아니다. 행별 제외 안내를 유지하고 `confirmed_empty_financial_assets`를 만들지 않는다.
기존 입력/출력 계약과 모델 버전은 유지하며 새 notice 코드가 추가된다. 연결부가 notice 코드를
열거해 처리한다면 새 코드도 전달할 수 있는지 확인해야 한다.

이 안내로 부채/일회성 지출을 모델링한 것이 되지는 않는다. 실제 서비스가 부분 분석을
허용할지, 일부 조건에서 실행을 보류할지는 연결 전에 결정해야 한다.

## 4. 실행 가능한 합성 예시

`ai-pjt/`에서 실행한다. 금액과 0% 가정은 검산용이며 시장 전망/권장 설정이 아니다.
실제 저장소를 조회하지 않는 예제이고, 확인된 연금 없음도 합성 사실이다.

```python
from copy import deepcopy
from retirement_simulation import (
    RetirementExpensePeriod, WhatIfChanges, WhatIfScenario,
    calculate_monte_carlo, calculate_what_if,
    map_retirement_profile, map_simulation_assumptions,
)

snapshot = {
    "basic_info": {"age": 60, "retirement_age": 60, "end_age": 61},
    "financial_assets": [
        {"asset_type": "예금/적금", "current_value": 10000, "monthly_savings": 0}
    ],
    "pension_assets": [],
    "goal_periods": [
        {"phase": "after", "from_age": 60, "to_age": 61, "items": [{"amount": 500}]}
    ],
    "real_estate_assets": [], "liabilities": [], "goal_milestones": [],
}
original = deepcopy(snapshot)
mapped = map_retirement_profile(snapshot, confirmed_empty_sections=("pension_assets",))
market = map_simulation_assumptions({
    "annual_return_mean": 0, "annual_return_volatility": 0,
    "annual_inflation_rate": 0, "num_paths": 2, "seed": 7,
})
baseline = calculate_monte_carlo(mapped.profile, market)
comparison = calculate_what_if(mapped.profile, market, (
    WhatIfScenario(scenario_id="lower_expenses", changes=WhatIfChanges(
        retirement_expenses=(RetirementExpensePeriod(from_age=60, to_age=61, monthly_amount=400),),
    )),
))
assert baseline.final_balance_percentiles.p50 == -2000  # 10000 - 2 * 12 * 500
assert comparison.scenarios[0].result.final_balance_percentiles.p50 == 400
assert comparison.scenarios[0].success_probability_delta_pp == 100
assert snapshot == original
assert mapped.notices[0].code == "partial_financial_scope"
# 후속 연결부는 mapped.notices와 mapping_version도 결과 메타데이터에 보존해야 한다.
```

개별 조회 없이 `snapshot`이 이미 존재한다는 가정의 호출 예시다. 운영 BE에서 이 객체를
읽는 방법·동일 시점·소유권 검사·보고서 진입 조건은 별도 연결 작업이다.
프로필/안내는 원본과 분리된 frozen 모델과 tuple이다. 원본 수정은 하지 않지만 읽는 도중
다른 스레드/DB가 원천을 변경하지 않도록 보장하는 동시성 제어까지 제공하지는 않는다.

## 5. 검증과 후속 작업

기본 테스트는 규격의 합성 40/60/90세 입력을 실제 어댑터에서 변환해 자산/생활비/연금
일정을 검산하고 기존 엔진으로 -36,000,000원·최초 고갈 85세·변경 후 24,000,000원·
+100pp를 확인한다. 구조·금액·빈 목록·ID·기간·미지원 안내·가정·원본 불변도 검사한다.
이는 합성 입력 기반의 모듈 통합 검증이며 실제 고객·API·FE·배포 테스트가 아니다.

저장소 루트에서 실행한다.

```bash
conda run -n retirewise-ai python -S -B -m unittest discover -s ai-pjt/retirement_simulation/tests -p test_input_adapter.py -v
conda run -n retirewise-ai python -B -m unittest discover -s ai-pjt/retirement_simulation/tests -q
conda run -n retirewise-ai python -S -B -m unittest discover -s ai-pjt/retirement_simulation/tests -q
```

최초 구현 `7253c68`의 2026-09-09, Miniforge `retirewise-ai` / Python 3.13.15 검증 기록:

- 신규 기본 테스트 **39개 통과**, 전체 **343개**가 일반 실행과 `-S` 실행에서 각각 통과.
- 공개 import 검증에 어댑터를 포함했고 Django/FastAPI/LLM 의존성 없이 패키지 import 확인.
- 이 가이드의 Python 예제 1개 실제 실행, 규격 JSON 2개와 테스트 fixture의 일치 확인.
- 오류/안내 코드가 모두 문서에 있는지, 모듈 로컬 링크 **177개**와 anchor **5개** 확인.
- Python 구문 검사, 변경 문서의 제목/코드 블록/공백과 `git diff --check` 통과.
- 기존 모델·계산 엔진 6개 파일은 작업 시작 커밋과 byte 단위 동일. 변경은 모듈 내부
  구현·export·테스트·문서 **8개 파일**뿐이며 BE·FE·Agent·공통 설정은 수정하지 않았다.

후속 확대 경계/회귀 검증도 완료했고 전체 381개 테스트가 일반·`-S`·역순 실행에서 통과했다.
후속 [전용 합성 샘플·가이드](input-samples.md)도 완료했다. 저장형 dict를 실제 어댑터에서
변환해 MC/What-if로 계산하고 반영 범위 안내를 함께 출력한다. 신규 샘플 검증을 포함한
전체 테스트는 404개이며 위 343/381개는 각 작업 시점의 기록이다.
What-if 변경 dict 파싱·통합 실행/화면 JSON·운영 성능 검증과 실제
BE·FE 연결은 이 기본 입력 매핑 작업에 포함하지 않는다.
