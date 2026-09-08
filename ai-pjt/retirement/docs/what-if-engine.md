# What-if 비교 계산 가이드

상태: **3번 공통 경로 비교·결과 모델·기본 테스트, 4번 확대 경계 검증, 5번 전용 CLI 완료.**
실제 데이터·서비스 연결은 남아 있다. 실행은 [What-if 샘플 가이드](what-if-samples.md)를 참고한다.

[`what_if_engine.py`](../what_if_engine.py)는 [변경안 생성기](what-if-scenarios.md)와
[몬테카를로 엔진](monte-carlo-engine.md)을 조합한다. 현금흐름·분포·분위수 공식을
재구현하지 않으며, 의미는 [What-if 규칙](what-if-rules.md)을 따른다.
아래는 내부 Python 인터페이스이지 HTTP·Agent 도구·외부 DTO 규격이 아니다.

## 1. 호출 규격과 배치 제한

두 함수와 모든 결과 타입은 `retirement` 패키지에서 import한다.

| 함수 | 공통 시장 경로 | 반환 |
|---|---|---|
| `calculate_what_if(profile, assumptions, scenarios)` | 입력 전체 검증 후 `generate_return_paths` 한 번 호출 | `WhatIfResult` |
| `calculate_what_if_paths(profile, assumptions, scenarios, *, annual_return_paths=...)` | 명시적 경로 전체를 검증·불변 복사. 난수 생성 없음 | `WhatIfResult` |

- `profile`은 기준 `RetirementProfile`, `assumptions`는 비교 전체의 공통 가정이다.
- `scenarios`는 **1~10개** `WhatIfScenario`의 list/tuple이다. 빈 배치·dict·generator·
  변경 모델 단독 전달은 거부한다. 변경 없음도 `WhatIfChanges()`를 가진 시나리오로 지정한다.
- `WhatIfScenario(*, scenario_id, changes)`의 `changes`는 기존 `WhatIfChanges`다.
  모든 변경안을 같은 기준 프로필에서 만들며 이전 변경안에 누적하지 않는다.
- `scenario_id`는 정규식 `[a-z][a-z0-9_-]{0,63}`의 1~64자 ASCII 식별자다.
  `baseline`은 예약어다. 배치 내 중복은 거부하며 자동 정리·이름 변경하지 않는다.
  같은 변경값이라도 식별자가 다르면 별개의 안으로 계산한다. 반환 순서는 입력 순서다.
- 식별자는 화면 표시명·자연어 설명·고객 ID 용도가 아니다. 자유 입력 라벨은 받지 않고,
  입력과 결과 모델의 기본 `repr`에서도 식별자·금융 값을 숨긴다.
- `MAX_WHAT_IF_SCENARIOS = 10`은 **임시 자원 보호 제한**이다. 기존 `N <= 100,000`과
  함께 적용하지만 최대 설정의 처리 시간·메모리·운영 안전성을 보장하지 않는다.

명시적 경로는 기존 몬테카를로와 동일한 중첩 list/tuple 계약이다. 정확히 `N`개 경로,
경로당 `end_age - current_age + 1`개 유한한 int/float 수익률 `r > -1`을 받으며 bool은
거부한다. 시나리오 목록·프로필과 전체 경로 검증이 끝나기 전에는 기준안도 계산하지 않는다.
현재·종료 나이와 시장 가정은 변경 조건으로 받지 않으므로 모든 안의 나이별 경로가 정렬된다.

## 2. 실행 흐름과 결과 구조

```text
전체 시나리오 검증·변경 프로필 생성
  → 공통 경로 1회 생성 또는 명시적 경로 전체 검증·복사
  → 기준안 몬테카를로 1회 계산
  → 각 변경안에 동일 경로 적용 → 기준 대비 차이 생성·검증
  → 모든 안이 성공한 경우에만 WhatIfResult 반환
```

여기서 실행 성공과 재무적 고갈은 다르다. 고갈한 경로도 정상 계산 결과이며 분모·분위수에
포함한다. 계산 오류가 발생한 안이나 경로를 빼고 나머지만 반환하지 않는다.
각 계산은 기존 `calculate_monte_carlo_paths`를 호출하므로 내부 검증용 경로 복사는 허용한다.

| 결과 접근 | 의미 |
|---|---|
| `output.metadata` | 실제 **공통 경로** 출처를 기록한 `MonteCarloMetadata`. 가정·기간·경로 수·RNG·Python 정보 |
| `output.model_version` | `retirement-what-if-v1`. `output.metadata.model_version`은 기존 `retirement-mc-v1` |
| `output.num_paths` | 공통 전체 경로 수 `N` |
| `output.baseline` | 기준안 `MonteCarloResult`, 한 번만 계산 |
| `output.scenarios` | 입력 순서의 불변 `WhatIfScenarioResult` tuple |
| `item.scenario_id` | 입력과 결과를 연결하는 식별자 |
| `item.baseline` | 공통 기준 결과의 동일 객체 참조. 원장을 복사한 것이 아님 |
| `item.result` | 해당 변경안의 `MonteCarloResult` |

원본 고객 프로필·변경 조건·전체 수익률·경로별 원장은 결과에 보관하지 않는다. 변경 내용의
표시가 필요하면 호출자가 전달한 시나리오 목록과 식별자로 대응한다. 자동 저장·로깅은 없다.
나중의 외부 DTO는 내부 dataclass 전체를 그대로 직렬화하는 것으로 확정하지 않는다.

### 차이 필드: 항상 변경안 − 기준안

| 필드 | 단위·타입 |
|---|---|
| `success_count_delta`, `depletion_count_delta`, `never_depleted_count_delta` | 부호 있는 경로 개수. 성공 개수 차이 = 미고갈 개수 차이 |
| `success_probability_delta_pp`, `depletion_probability_delta_pp` | `(변경 확률 - 기준 확률) × 100`, 퍼센트포인트(pp) |
| `asset_deltas` | 나이순 `AssetPercentileDelta(age, balance)` tuple, 모든 계산 연도 포함 |
| `asset_deltas[t].balance` | `AmountPercentileDelta(p10, p50, p90)`, 명목 KRW |
| `final_balance_delta` | 마지막 나이의 `balance`와 동일 객체 |
| `final_shortfall_delta` | 각 안의 최종 부족액 분위수끼리 뺀 `AmountPercentileDelta`, 명목 KRW |
| `first_depletion_deltas` | 나이순 `FirstDepletionDelta(age, count)` tuple. 0개인 나이 포함·음수 허용 |

예를 들어 0.75에서 1.0으로 바뀌면 +25pp다. 상대 증가율이나 +25%로 바꾸지 않는다.
차이는 각 안의 요약에서 파생하고, 금액 차이는 결과 생성 중 미리 계산·유한성 검증한다.
`WhatIfScenarioResult`의 기준·변경 결과를 `dataclasses.replace`로 교체하면 금액 차이도
다시 계산한다. 입력 프로필만 바꾸는 것은 기존 결과를 갱신하지 않으므로 계산 함수를 재호출해야 한다.

분위수 차이는 경로별 차이의 분위수가 아니며 **P10 차이 ≤ P50 차이 ≤ P90 차이**를
요구하지 않는다. 기존 정렬 제약이 있는 `AmountPercentiles`와 구분한다. 미고갈 나이를
임의의 숫자로 치환하지 않고, 고갈 평균 지연 연수·개선 확률·추천 순위는 계산하지 않는다.
부족액 감소는 음수로 표시한다. 반올림은 표시 단계에서만 하며 고갈 판정은 바꾸지 않는다.

## 3. 출처와 재현성

| 공통 경로 방식 | `output.metadata`의 출처·모델·유효 시드 | 하위 몬테카를로 결과 |
|---|---|---|
| 내부 생성·변동성 양수 | `generated`, 로그정규·실제 RNG·입력 시드 | `provided`, RNG/유효 시드 `None` |
| 내부 생성·변동성 0 | `generated`, `constant`, 유효 시드 `None` | `provided`, RNG/유효 시드 `None` |
| 직접 전달 | `provided`, 모델/RNG/유효 시드 `None` | `provided`, RNG/유효 시드 `None` |

하위 엔진은 실제로 경로를 전달받았으므로 출처를 `generated`로 사후 수정하지 않는다.
사용하지 않은 설정값은 `metadata.assumptions`에 남는다. 수동 경로를 입력 시드로 생성한
시장 표본이라고 주장하지 않는다. 같은 코드·Python 버전·환경·입력으로 재현성을 검증하며,
Python 버전·플랫폼 간 비트 단위 일치는 보장하지 않는다.

결과 생성자는 기간·가정·버전·하위 출처와 공통 기준안의 일관성을 검사한다. 단,
**수동으로 두 결과를 조합해 생성자를 호출한 것만으로 실제 공통 경로 사용이 증명되지는 않는다.**
경로 재사용 보장은 위 두 계산 함수를 통한 실행에 해당한다.

## 4. 오류와 전체 중단

모델 타입·시나리오 개수·식별자·생활비·명시적 경로 오류는 `ModelValidationError`다.
시나리오 적용 오류에는 `scenarios[1].changes.retirement_expenses`처럼 **0부터 시작하는
입력 인덱스**를 붙인다. 자유 입력 식별자나 금액은 오류 메시지에 넣지 않는다.
호출 인자 누락·미지원 keyword는 Python의 `TypeError`다.

수치 실행 오류는 `WhatIfCalculationError`로 통일한다.

| 필드 | 값·의미 |
|---|---|
| `scope` | `market`: 공통 경로 생성, `baseline`: 기준 계산, `scenario`: 변경 계산·차이 |
| `stage` | `return_generation`, 기존 엔진의 `cashflow`/`aggregation`, 또는 `comparison` |
| `field_name` | 기존 오류 필드 보존. 차이 오류는 `asset_deltas.p10` 등의 필드 |
| `scenario_index` | 변경안 입력 인덱스. 공통 경로·기준안 오류이면 `None` |
| `path_index`, `age` | 알려진 경로 인덱스·나이. 집계·차이처럼 특정 경로를 가리키지 못하면 `None` |

생성·기준안·중간 변경안·차이 계산 어느 단계에서 오류가 나도 전체 호출을 중단한다.
후속 변경안을 실행하거나 이미 계산한 부분 결과를 반환하지 않는다. 유한한 두 잔액도
차이가 비유한 값이 될 수 있으므로 뺄셈 결과를 검사한다. 자르기·재추출·경로 제외는 없다.
기존 예외의 메시지를 그대로 합치지 않고 연쇄 표시를 억제한다. traceback의 로컬 변수나
입력 객체 전체까지 안전하게 로깅된다는 의미는 아니다.

## 5. 실행 가능한 합성 예시

`ai-pjt/`에서 Miniforge `retirewise-ai` Python으로 실행한다. 아래 수동 경로는 규칙의
C 예시 검산용이며 난수로 뽑은 시장 표본이 아니다. 끝부분은 실제 생성 실행의 사용 예다.

```python
from math import isclose
from retirement import (
    PensionIncome, RetirementExpensePeriod, RetirementProfile, SimulationAssumptions,
    WhatIfChanges, WhatIfScenario, calculate_what_if, calculate_what_if_paths,
)

profile = RetirementProfile(
    current_age=60, retirement_age=60, end_age=61,
    liquid_assets=120, monthly_savings=0,
    retirement_expenses=(RetirementExpensePeriod(from_age=60, to_age=61, monthly_amount=10),),
    pension_incomes=(PensionIncome(start_age=61, end_age=61, monthly_amount=20),),
)
assumptions = SimulationAssumptions(
    annual_return_mean=0.05, annual_return_volatility=0.10,
    annual_inflation_rate=0, num_paths=4, seed=42,
)
scenarios = (
    WhatIfScenario(scenario_id="lower_expenses", changes=WhatIfChanges(
        retirement_expenses=(RetirementExpensePeriod(from_age=60, to_age=61, monthly_amount=5),),
    )),
    WhatIfScenario(scenario_id="unchanged", changes=WhatIfChanges()),
)
output = calculate_what_if_paths(profile, assumptions, scenarios,
    annual_return_paths=((-0.5, 0), (0, 0), (0.5, 0), (1, 0)))
item = output.scenarios[0]
assert output.baseline.success_probability == 0.75
assert item.result.success_probability == 1.0
assert item.success_probability_delta_pp == 25.0
assert isclose(item.final_balance_delta.p50, 120.0)
assert item.final_shortfall_delta.p50 == 0.0
assert output.scenarios[1].result == output.baseline
assert output.metadata.path_source == "provided" and output.metadata.seed is None
print(f"{item.scenario_id}: {item.success_probability_delta_pp:+.1f}pp, "
      f"final P50 {item.final_balance_delta.p50:+.1f} KRW")

generated = calculate_what_if(profile, assumptions, scenarios)
assert generated == calculate_what_if(profile, assumptions, scenarios)
assert generated.metadata.path_source == "generated" and generated.metadata.seed == 42
assert generated.baseline.metadata.path_source == "provided"
```

출력은 `lower_expenses: +25.0pp, final P50 +120.0 KRW`다. 이 +25pp는 **수동 경로의
비교**이고 뒤의 생성 실행도 같은 차이가 나온다고 가정하지 않는다. 기존 고정·몬테카를로
CLI의 계산은 유지하며, 5번에서 [What-if 전용 CLI](what-if-samples.md)를 추가했다.

## 6. 검증 기록·자원·남은 범위

3번 완료 시점인 2026-09-08, Python 3.13.15·Miniforge `retirewise-ai`에서 기존 219개에
[`test_what_if_engine.py`](../tests/test_what_if_engine.py)의 32개를 추가해
**전체 251개 테스트**가 일반 실행과 `-S` 실행 모두 통과했다.
전체 테스트의 역순 실행도 통과했고, 5절의 예시 코드를 그대로 실행해 기대 출력을 확인했다.

| 그룹 | 개수 | 기본 검증 |
|---|---:|---|
| `WhatIfEngineBasicTests` | 14 | A~C·공통 경로/기준 계산 횟수·변경 없음·출처·순서/재현성·원본 보존·1년/1경로·배치 상한·기존 엔진 대조 |
| `WhatIfEngineValidationTests` | 7 | 식별자·타입·배치 크기·중복·마지막 변경안/수익률까지 전체 선검증 |
| `WhatIfEngineResultTests` | 6 | 분위수 차이 의미·부호·유한성·불변성·파생값·메타데이터 일관성 |
| `WhatIfEngineErrorTests` | 5 | 생성·기준/중간 변경안·집계·차이 overflow 오류 위치 보존 및 전체 중단 |

```bash
# 저장소 루트
conda run -n retirewise-ai python -B -m unittest discover -s ai-pjt/retirement/tests -q
conda run -n retirewise-ai python -S -B -m unittest discover -s ai-pjt/retirement/tests -q
conda run -n retirewise-ai python -S -B -m unittest discover -s ai-pjt/retirement/tests -p test_what_if_engine.py -v
```

후속 4번에서 독립 Decimal 검산·은퇴/생활비·고갈/회복·순서/재현성·수치 오류·개별 상한
테스트 25개를 추가해 당시 **전체 276개 통과**를 확인했다. 상세 범위와 명령은
[What-if 검증 기록](what-if-validation.md)을 참고한다. 양쪽 계산은 유한하지만 차이가
overflow하는 실제 입력도 검증했다. 기존 수동 합성 요약 오류 검사는 유지하며 실제 고객
결과로 표시하지 않는다. 시장 적합성·통계적 신뢰구간·운영 성능 검증 완료를 의미하지 않는다.

5번 전용 샘플 CLI 테스트 24개를 더한 현재 전체는 **300개 통과**다. 각 사례의
실제 공통 경로 비교·입력/출력 표시·오류 종료·재현성·문서 예시는
[What-if 샘플 가이드](what-if-samples.md)에 정리한다. 이 작업에서 계산 엔진은 변경하지 않았다.

변경안 수 `S`, 경로 수 `N`, 연도 수 `T`일 때 대략 `O((S+1)×T×N log N)` 시간과
`O(N×T + S×T)` 공간을 사용한다. 공통 경로·현재 실행의 표본을 보관하고, 끝난 안은
집계만 남긴다. 호출마다 검증용 경로 복사도 있으므로 상수 비용은 남는다. 외부 API·DB·
BE/FE·Agent·컨텍스트·공용 의존성·서비스 설정은 이번 작업에서 변경하지 않았다.
