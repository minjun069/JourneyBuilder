# 은퇴 시뮬레이션 통합 실행

작성일: 2026-09-09. `feat/ai-retirement-analysis`의 3번 작업, 시작 커밋 `9700c62`.
[통합 호출·입출력 규격](analysis-contract.md)의 실행·불변 결과·실행 오류를 구현했다.
JSON 변환은 후속 4번에서 구현했으며 [출력 변환 가이드](analysis-output.md)를 따른다.
후속 5번의 [합성 성능 측정·후보 한도](analysis-performance.md)도 완료했다.
후속 6번의 [통합 분석 CLI·확대 회귀](analysis-samples.md)와 [BE 인수인계](be-handoff.md)도 완료했다.
운영 성능 검증·BE/FE 연결은 아직 완료 범위가 아니다.

## 1. 공개 인터페이스와 실행 순서

패키지 최상위에서 다음 세 이름을 import할 수 있다.

```text
analyze_retirement(
    saved_data, assumption_values, *,
    scenarios=(), confirmed_empty_sections=(),
) -> RetirementAnalysisResult

RetirementAnalysisResult(
    *, mapped_input, scenario_definitions, scenario_profiles, calculation,
)
RetirementAnalysisError(code, field_name, *, reason_code=None)
```

실행 코드는 [analysis.py](../analysis.py), 오류 규칙은 [analysis_errors.py](../analysis_errors.py)에 있다.
입력/가정/변경 매퍼와 기존 MC/What-if 엔진을 호출하며 금융 계산식을 복제하지 않는다.

1. 저장형 입력과 없음 확인을 기존 `map_retirement_profile()`에 한 번 전달한다.
2. 명시적 가정 5개를 `map_simulation_assumptions()`로 한 번 변환한다.
3. 변경 목록을 `map_what_if_scenarios()`로 한 번 변환한다.
4. 각 변경안을 **원래 기준 프로필**에 독립 적용하여 모두 검증한다.
5. 변경안 0개면 `calculate_monte_carlo()` 한 번, 1~10개면 `calculate_what_if()` 한 번 호출한다.
6. 기존 계산 결과와 입력·안내·변경 정의·적용 프로필을 불변 결과 묶음으로 반환한다.

`scenarios=None`은 잘못된 입력이다. 빈 목록은 기준 모드이며, 빈 `changes`가 있는 행은
무변경 What-if 비교다. 명시적 0은 유지하고 앞 변경안을 다음 변경안에 누적하지 않는다.
한 변경안이라도 적용 실패하면 엔진/경로 생성을 시작하지 않는다. 수치 계산 도중 실패해도
일부 성공한 경로나 시나리오를 반환하지 않는다.

What-if는 기존 엔진에서 공통 경로를 한 번 생성하고 기준 계산도 한 번만 수행한다.
별도 MC를 먼저 실행하거나 변경안마다 새 시장 경로를 생성하지 않는다. 기존 엔진과 결과 모델이
프로필 정합성을 다시 검사하는 것은 허용한다. 이 반복은 **순수 적용 검증**이며 계산 재실행이 아니다.

## 2. 반환 모델과 재현성

`RetirementAnalysisResult`는 frozen/slots/keyword dataclass다.

| 필드 | 보존 내용·검사 |
|---|---|
| `mapped_input` | 기준 프로필, 모든 notices의 순서·내용, 기존 `retirement-input-v1` 보존 |
| `scenario_definitions` | 0~10개 불변 정의, 유일한 ID·요청 순서·명시적 변경 보존 |
| `scenario_profiles` | 각 정의를 기준에 독립 적용한 프로필과 같아야 함. 변경 정의 수와 일치 |
| `calculation` | 기준 모드는 `MonteCarloResult`, 비교 모드는 `WhatIfResult`. 기간·배치/하위 메타데이터·시나리오 수/ID/순서 일치 |
| `contract_version` | `retirement-analysis-v1`, 생성 인자나 `replace()`로 지정할 수 없음 |
| `mode` | 시나리오 수에서 계산하는 property: `baseline` 또는 `what_if` |

수동 생성에서 list를 받으면 tuple로 복사한다. 매퍼는 원천 dict/list를 보관하지 않고,
기준/변경/가정도 불변 모델이다. 반환 모델의 repr에는 재무 데이터·시나리오 ID를 표시하지 않는다.
검증된 내부 모델을 강제 변경하는 `object.__setattr__` 우회에 대한 보안 경계는 아니며,
수동으로 조립한 결과가 실제 실행이나 계산값의 진위를 증명하는 것도 아니다.

통합 함수는 **생성 경로 결과만** 반환한다. 직접 지정한 수익률 경로를 쓰는 고급 호출은 기존
`calculate_monte_carlo_paths()`/`calculate_what_if_paths()`에 남긴다. 배치 메타데이터는 `generated`,
What-if 내부 기준/변경 MC는 `provided`로 유지한다. 설정 시드는 `metadata.assumptions.seed`,
실제 사용 시드는 기존 `metadata.seed`다. 변동성 0이면 실제 시드/RNG는 None이다.
JSON의 `effective_seed`는 이 기존 property에서 매핑한다.

원본·전역 난수 상태·기준 입력은 유지한다. 같은 입력·가정·시드·코드·Python 환경이면 결과가 같다.
시각·실행 ID·사용자 정보·금융 API 조회·캐시·원본 병합은 추가하지 않는다. 호출자는 매핑 중
입력을 동시에 수정하지 않아야 하며, DB snapshot 일관성·소유권·최신 보고서와의 일치는 별도 책임이다.

## 3. 실행 가능한 합성 예시

Miniforge 환경을 활성화한 뒤 저장소의 `ai-pjt/`에서 아래 Python을 실행할 수 있다.
0% 가정은 손계산 검증용이며 운영 시장 가정이 아니다. 실제 서비스는 검증한 저장형 snapshot과
서버 가정을 전달해야 한다. 원천의 자세한 규칙은 [입력 어댑터 가이드](input-adapter.md)를 따른다.

```python
from retirement_simulation import RetirementAnalysisError, analyze_retirement

saved_data = {
    "basic_info": {"age": 60, "retirement_age": 60, "end_age": 60},
    "financial_assets": [{
        "asset_type": "예금/적금", "current_value": 10_000, "monthly_savings": 0,
    }],
    "pension_assets": [],
    "goal_periods": [{
        "phase": "after", "from_age": 60, "to_age": 60, "items": [{"amount": 1_000}],
    }],
    "real_estate_assets": [], "liabilities": [], "goal_milestones": [],
}
assumptions = {
    "annual_return_mean": 0, "annual_return_volatility": 0,
    "annual_inflation_rate": 0, "num_paths": 2, "seed": 7,
}
# 이 합성 원천에는 연금이 실제로 없다. 브라우저의 주장만으로 확인 처리하면 안 된다.
baseline = analyze_retirement(saved_data, assumptions, confirmed_empty_sections=("pension_assets",))
comparison = analyze_retirement(
    saved_data, assumptions, confirmed_empty_sections=("pension_assets",),
    scenarios=[{
        "scenario_id": "lower_expenses",
        "changes": {"retirement_expenses": [
            {"from_age": 60, "to_age": 60, "monthly_amount": 800},
        ]},
    }],
)
assert baseline.mode == "baseline"
assert baseline.calculation.final_balance_percentiles.p50 == -2_000
assert comparison.mode == "what_if"
assert comparison.calculation.baseline.final_balance_percentiles.p50 == -2_000
changed = comparison.calculation.scenarios[0]
assert changed.result.final_balance_percentiles.p50 == 400
assert changed.final_balance_delta.p50 == 2_400
assert changed.success_probability_delta_pp == 100
assert comparison.mapped_input.notices == baseline.mapped_input.notices

try:
    analyze_retirement(saved_data, assumptions)  # 연금 없음 확인을 전달하지 않은 경우
except RetirementAnalysisError as error:
    assert error.code == "input_mapping_failed"
    assert error.stage == "input"
    assert error.field_name == "pension_assets"
    assert error.reason_code == "empty_confirmation_required"
else:
    raise AssertionError("missing confirmation must not succeed")
```

이 예시는 기준 모드와 비교 모드를 **각각 별도 호출**해 차이를 확인한다. 서비스에서 What-if를
실행할 때는 `comparison` 호출 하나로 기준과 변경 결과를 함께 받으며 기준 MC를 먼저 호출할 필요가 없다.
기준 `10,000 − 12×1,000 = −2,000원`, 변경 `10,000 − 12×800 = 400원`, 성공률 차이는 +100pp다.
반환 모델은 JSON 응답이 아니며 `analysis_to_dict()`로 변환한다.
notices를 누락한 임의 응답이나 `asdict()`를 공개 계약으로 사용하지 않는다.

## 4. 실행 오류

이번 단계에서 구현한 오류 코드는 다음 **5개**다. stage/message는 코드로 결정되고 공개 진단
property는 읽기 전용이다. 수동 생성 시 코드·reason 필요 유무/허용 값·경로를 검사하며
잘못된 생성은 안전한 `ModelValidationError`를 낸다.

| code / stage | reason_code·경로 |
|---|---|
| `input_mapping_failed` / `input` | 기존 매핑 reason·안전한 저장 입력 경로 유지 |
| `assumption_mapping_failed` / `assumptions` | 가정 매핑 reason·`assumptions...` |
| `scenario_mapping_failed` / `scenarios` | 변경 매핑 reason·`scenarios...` |
| `scenario_application_failed` / `scenarios` | `invalid_profile`·`scenarios[i].changes` 또는 허용 변경 필드 |
| `calculation_failed` / `calculation` | None·아래 안전한 계산 위치 |

적용 실패의 `end_age`는 실제 변경 가능한 `retirement_age`로 연결한다. 생활비 공백/중복은
`retirement_expenses`로, 알 수 없는 모델 필드는 `.changes` 객체까지로 축약한다.
계산 오류 위치는 허용한 scope/stage/field 조합만 사용한다.

- 경로 생성: `calculation.market.return_generation.annual_returns` 등 기존 생성기 필드.
- 기준 계산: `calculation.baseline.cashflow.liquid_assets`, `calculation.baseline.aggregation.num_paths` 등.
- 변경 계산/비교: `calculation.scenarios[0].cashflow.annual_expenses`,
  `calculation.scenarios[0].comparison.asset_deltas.p50` 등.
- 알 수 없는 stage/field/scope, 잘못된 시나리오 index나 조합은 `calculation`으로 축약.

배열 위치 외에 시나리오 ID·입력값·알 수 없는 키·모델 원문 문구를 진단 속성/메시지로 복사하지 않는다.
path index·age 같은 하위 상세 객체도 노출하지 않는다. 알려진 오류를 감쌀 때 원문 exception chaining
표시는 억제한다. 다만 Python traceback의 프레임/지역 변수까지 제거하는 기능은 아니므로
서비스는 예외/입력/지역 변수 전체를 로그에 덤프해서는 안 된다.

예상하지 못한 버그·`MemoryError`·중단은 그대로 전파한다. 엔진이 요청과 다른 가정이나
구조의 결과를 반환하는 내부 계약 위반도 정상 입력 오류로 숨기지 않는다. `None`/0/성공 응답으로
대체하거나 재시도·timeout·HTTP status를 이 계층에서 결정하지 않는다.

`invalid_analysis_result`, `unsafe_json_number`와 출력 경로 검증·`analysis_to_dict()`·
`analysis_error_to_dict()`는 **후속 4번 JSON 계층에서 구현했다**. 오류 생성자는 7개 코드를
지원하며 출력의 숫자/정합성 규칙은 출력 변환 가이드를 따른다.
내부 계산의 큰 정수/유한 float 허용 범위를 출력 제한으로 바꾸지 않았다.
기존 독립 함수의 `InputMappingError`/`ModelValidationError`/계산 오류 계약은 그대로다.

## 5. 검증·작업 경계

[실행 테스트](../tests/test_analysis.py)는 실제 엔진 손계산·0/1/10개 모드·공통 경로/기준 계산
1회·순차 검증/전체 실패·기준 독립 적용·재현성·원본/난수 상태 격리를 확인한다.
[모델·오류 테스트](../tests/test_analysis_models.py)는 불변성·입력/결과 정합성·생성 경로 출처·
안전한 오류 생성·기존 매핑 오류 경로 보존을 확인한다. 패키지 경계 검사에 신규 export도 추가했다.

2026-09-09, Miniforge `retirewise-ai` / CPython 3.13.15, 3번(`4615527`) 당시 검증 결과:

- 신규 **60개**(실행 34개·모델/오류 26개) 통과. 기존 447개를 포함한 전체 **507개**가
  일반·`-S` 실행과 `-S` 역순 실행에서 모두 통과했다.
- 금지 필드 `replace()` 테스트는 Python 버전에 따른 `TypeError`/`ValueError` 차이를 허용하도록
  보완했다. 계산 코드의 예외를 무시하거나 예상 실패로 처리한 테스트는 없다.
- 이 가이드와 변경 입력 가이드의 Python 예시 2개를 실제 실행했다. 계약 JSON 3개를 파싱하고
  실제 통합 호출의 주요 수치·안내·실행 출처·오류 속성을 대조했다. 당시에는 JSON 직렬화 검증을 포함하지 않았다.
- Python 35개 파일 구문, 로컬 Markdown 링크 232개·anchor 5개·코드 블록을 확인했다.
- 기존 Python 파일 중 export/import 경계 테스트 2개를 제외한 **29개**는 `9700c62`와 byte 단위 동일하다.
  변경 **12개 파일**은 모두 시뮬레이션 모듈 내부다.

저장소 루트에서 Miniforge 환경으로 실행한다.

```bash
conda run -n retirewise-ai python -S -B -m unittest discover -s ai-pjt/retirement_simulation/tests -p 'test_analysis*.py' -v
conda run -n retirewise-ai python -B -m unittest discover -s ai-pjt/retirement_simulation/tests -q
conda run -n retirewise-ai python -S -B -m unittest discover -s ai-pjt/retirement_simulation/tests -q
```

변경 위치는 `ai-pjt/retirement_simulation/`뿐이다. 기존 계산 엔진·모델·입력/변경 매퍼·CLI는
수정하지 않았고 BE·FE·Agent·컨텍스트·매뉴얼·공통 의존성·배포 설정·Jira도 건드리지 않았다.
후속 **4번 결과/오류 JSON 변환·5번 합성 성능 측정·6번 통합 샘플/인수인계까지 완료**했다.
다음은 브랜치 최종 변경 검토·최신 master 충돌 확인이며 원격 작업은 명시 요청 시 진행한다.
실제 서비스 연결 완료와 독립 모듈 구현 완료는 구분한다.
