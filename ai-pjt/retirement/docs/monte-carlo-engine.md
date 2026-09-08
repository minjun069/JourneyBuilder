# 몬테카를로 계산·집계 엔진 가이드

상태: **계산·집계·결과 모델, 확대 경계 검증·전용 샘플 CLI 구현 완료.**

[`monte_carlo.py`](../monte_carlo.py)는 기존 수익률 생성기와 현금흐름 엔진을
호출해 [계산 규칙](monte-carlo-rules.md)에 따른 집계를 만든다. 현금흐름 공식을
복제하지 않는다. [전용 샘플 CLI](monte-carlo-samples.md)로도 실행할 수 있다.
What-if·실제 데이터 및 서비스 연결은 남아 있으며, 아래 예시는 시장 전망이 아닌 합성 검산 자료다.

## 1. 두 가지 실행 방식

두 함수 모두 `retirement` 패키지에서 import하며, 기존 `RetirementProfile`과
`SimulationAssumptions`를 입력받아 `MonteCarloResult`를 반환한다.

| 함수 | 수익률 입력 | 결과의 `metadata.path_source` |
|---|---|---|
| `calculate_monte_carlo(profile, assumptions)` | `generate_return_paths`를 한 번 호출해 전체 경로 생성 | `generated` |
| `calculate_monte_carlo_paths(profile, assumptions, annual_return_paths=...)` | 직접 전달한 경로 전체를 검증·복사한 뒤 계산. 추가 난수 생성 없음 | `provided` |

`annual_return_paths`는 keyword 인자다. 바깥·안쪽 모두 list 또는 tuple이며,
바깥 길이는 정확히 `assumptions.num_paths`, 안쪽 길이는 정확히
`end_age - current_age + 1`이어야 한다. 원소는 bool이 아닌 유한한 int/float이고
`-1`보다 커야 한다. 내부에서는 float로 검증·복사한다. 잘라내기·반복·채우기·
문자열 파싱·배열 라이브러리나 generator 입력은 지원하지 않는다.

직접 전달한 경로는 `mean`, `volatility`, `seed`를 대신한다. 세 값은 참고 가정으로
남지만 해당 경로를 만들었다는 증거가 아니다. 물가는 기존처럼 현금흐름에 적용된다.
수동 경로에서 나온 성공 비율을 곧바로 모델이 추정한 시장 확률이라고 표현하지 않는다.

## 2. 반환 모델과 파생값

결과와 내부 모델은 frozen dataclass이며 금액은 표시용 반올림 전 **명목 원 단위**다.
list로 만든 결과 컬렉션은 tuple로 복사하고, 나이는 계산 기간과 정확히 일치하는
오름차순이어야 한다. 원본 고객 프로필·전체 수익률·각 경로의 상세 현금흐름은
최종 결과에 보관하지 않는다.

| 결과 접근 | 의미 |
|---|---|
| `metadata` | 입력 가정·기간·경로 출처·모델 및 실행 환경 정보 |
| `num_paths` | 검증된 입력의 전체 경로 수 `N` |
| `first_depletion_counts` | 나이별 `FirstDepletionCount(age, count)` tuple. 0개인 나이도 포함 |
| `depletion_count` | 최초 고갈 히스토그램의 개수 합. 경로 하나는 최대 한 번 집계 |
| `success_count`, `never_depleted_count` | `N - depletion_count` |
| `success_probability` | `success_count / N`, 범위 0~1 |
| `depletion_probability` | `depletion_count / N`, 범위 0~1 |
| `asset_percentiles` | 나이별 `AssetPercentiles(age, balance)` tuple |
| `asset_percentiles[t].balance` | 해당 나이의 `AmountPercentiles(p10, p50, p90)` |
| `final_balance_percentiles` | 마지막 `asset_percentiles`의 `balance`를 그대로 참조 |
| `final_shortfall_percentiles` | 경로마다 최종 부족액을 먼저 구한 뒤 집계한 `AmountPercentiles` |

확률·개수·최종 잔액 분위수는 별도로 중복 저장하지 않고 위 원천에서 파생한다.
고갈 개수 합은 `N`을 넘을 수 없고, 부족액 분위수는 음수가 될 수 없다.
금액과 입력 가정은 기본 `repr`에서 숨긴다. 외부 JSON DTO·HTTP 응답·Agent 도구
계약이나 결과 로깅 정책을 확정한 것은 아니다.

### 고갈과 분위수의 해석

고갈 판정은 각 경로의 `CashflowResult.first_depletion_age`를 재사용한다.
중간에 음수였다가 회복해도 고갈 이력을 취소하지 않는다. 정확히 0은 고갈이 아니고,
미세한 음수도 epsilon으로 없애지 않는다. 이는 연말 기준이며 연중 지급 가능성의 보장이 아니다.

분위수에는 성공·고갈·회복 경로 모두를 넣는다. P10·P50·P90은
[규칙에 정한 선형 보간](monte-carlo-rules.md#7-결과-집계-규칙)이며, 특정 실제
경로 하나나 성공확률의 신뢰구간을 뜻하지 않는다. 동일한 표본값은 그대로 반환해
변동성 0의 잔액을 정확히 보존한다. 보간은 부호에 따라 동등한 식을 사용해 유한한
양 끝값의 차이를 계산하다 overflow하는 문제를 피하며, NumPy 의존성을 추가하지 않는다.

최종 부족액 분위수는 `max(-final_balance, 0)`인 **경로별 표본**에서 계산한다.
예를 들어 두 경로의 최종 잔액이 -60원·60원이면 잔액 P50은 0원, 부족액 P50은
30원이다. 잔액 P50의 부호를 바꾸고 0으로 자르면 이 결과를 얻을 수 없다.
부족액은 미충당 잔액이지 실제 대출이 아니며, 여러 연도의 부족액을 합산하지 않는다.

## 3. 메타데이터와 재현성

`MonteCarloMetadata`는 현재·종료 나이, 불변 `assumptions`, `path_source`,
`model_version="retirement-mc-v1"`, Python 버전·구현체를 보관한다.
`assumptions`에는 입력한 평균·변동성·물가·반복 수·시드를 그대로 남긴다.
유효한 RNG 정보는 실행 방식에서 파생한다.

| 실행 방식 | `return_model` | `rng_algorithm` | `metadata.seed` |
|---|---|---|---|
| 생성, 변동성 양수 | `lognormal_gross_return` | `python.random.Random.gauss` | 실제 입력 시드 |
| 생성, 변동성 0 | `constant` | `None` | `None` |
| 직접 지정한 경로 | `None` | `None` | `None` |

마지막 두 경우의 `None`은 **이 실행에서 RNG를 사용하지 않았다**는 뜻이다.
설정됐지만 사용하지 않은 시드는 `metadata.assumptions.seed`에서 확인할 수 있다.
외부에서 미리 생성한 경로를 전달해도 그 원래 생성 출처를 추측하지 않고 `provided`로
표시한다. 직접 전달한 경로의 재현에는 원래 경로 값이 필요하며 메타데이터만으로는 부족하다.

같은 입력·시드·코드·Python 버전·실행 환경에서는 생성 실행을 반복해도 결과가 같다.
Python 버전·플랫폼 간 비트 단위 동일성을 보장하지 않는다. 가정이나 시드가 다르면
메타데이터도 달라지므로, 변동성 0이나 동일한 명시적 경로를 비교할 때는 금융 요약과
메타데이터의 차이를 구분한다.

## 4. 실행 예시

`ai-pjt/`에서 Miniforge `retirewise-ai`의 Python으로 실행한다.
수동 입력은 [계산 규칙 예시 B](monte-carlo-rules.md#8-손계산회귀-테스트-기대값)와 같다.

```python
from math import isclose
from retirement import (
    PensionIncome, RetirementExpensePeriod, RetirementProfile, SimulationAssumptions,
    calculate_monte_carlo, calculate_monte_carlo_paths,
)

profile = RetirementProfile(
    current_age=60, retirement_age=60, end_age=61,
    liquid_assets=120, monthly_savings=0,
    retirement_expenses=(
        RetirementExpensePeriod(from_age=60, to_age=61, monthly_amount=10),
    ),
    pension_incomes=(PensionIncome(start_age=61, end_age=61, monthly_amount=20),),
)
assumptions = SimulationAssumptions(
    annual_return_mean=0.05, annual_return_volatility=0.10,
    annual_inflation_rate=0.0, num_paths=4, seed=42,
)
paths = ((-0.5, 0.0), (0.0, 0.0), (0.5, 0.0), (1.0, 0.0))
provided = calculate_monte_carlo_paths(profile, assumptions, annual_return_paths=paths)
assert provided.success_probability == 0.75
assert [item.count for item in provided.first_depletion_counts] == [1, 0]
assert isclose(provided.final_balance_percentiles.p10, 78.0)
assert isclose(provided.final_balance_percentiles.p50, 150.0)
assert isclose(provided.final_balance_percentiles.p90, 222.0)
assert provided.final_shortfall_percentiles.p90 == 0.0
assert provided.metadata.path_source == "provided" and provided.metadata.seed is None

# 아래는 같은 고객 조건의 실제 난수 생성 실행이다. 위 수동 경로와 같다고 가정하지 않는다.
generated = calculate_monte_carlo(profile, assumptions)
assert generated == calculate_monte_carlo(profile, assumptions)
assert generated.metadata.path_source == "generated"
assert generated.metadata.seed == 42
assert generated.success_count + generated.depletion_count == 4
print(f"provided_success={provided.success_probability:.2f}, generated_paths={generated.num_paths}")
```

예상 출력은 `provided_success=0.75, generated_paths=4`다. 수동 경로는 고갈 후 회복한
경로 1개와 미고갈 경로 3개이며 모든 최종 부족액이 0이어도 성공확률을 1로 만들지 않는다.
기존 `retirement.samples` CLI는 여전히 고정 현금흐름만 실행한다.

## 5. 오류와 작업 경계

잘못된 모델·직접 입력한 경로의 개수·기간·수익률은 `ModelValidationError`다.
명시적 경로는 전체 배치를 검증·복사한 뒤 첫 현금흐름 계산을 시작하므로, 마지막
경로에 잘못된 입력이 있어도 앞쪽 계산부터 수행하지 않는다.

수치 실행 오류는 공개 예외 `MonteCarloCalculationError`로 통일한다.

| 필드 | 의미 |
|---|---|
| `stage` | `return_generation`, `cashflow`, `aggregation` |
| `field_name` | 수익률·현금흐름 엔진의 오류 필드 또는 `asset_percentiles`, `final_shortfall_percentiles`, `num_paths` |
| `path_index` | 오류 경로의 0부터 시작하는 인덱스. 공통 모수 변환·전체 집계 오류이면 `None` |
| `age` | 오류 발생 나이. 최초 금액 변환·공통 모수 변환·경로 수 오류 등은 `None` |

기존 엔진의 수치 오류 위치를 보존하되 입력 원문·금액·전체 경로를 메시지에 넣지 않는다.
원래 예외 메시지의 연쇄 표시도 억제한다. traceback의 로컬 변수나 요청 본문까지
안전하게 로깅된다는 뜻은 아니다. 경로 하나라도 계산이 실패하면 전체 실행을 중단하고,
분모를 줄이거나 그 오류를 고갈로 처리하거나 부분 성공확률을 반환하지 않는다.

원본 프로필·컨텍스트·저장된 정보는 수정하지 않는다. 동일한 경로를 여러 프로필에
전달할 수 있지만, 이것만으로 What-if의 시나리오 변경·기간 정렬·입력 adapter가
구현된 것은 아니다. BE·FE·Agent·RAG·공용 의존성·실행 설정은 이번 작업에 없다.

## 6. 검증 기록과 남은 검증

2026-09-08, 3번 작업 완료 시점에 Miniforge `retirewise-ai`의 Python 3.13.15에서
**136개 통과: 기존 102개 + 신규 34개**를 확인했다.
아래는 [`test_monte_carlo.py`](../tests/test_monte_carlo.py)의 기본 검사다.
후속 4번에서 경계 테스트 26개를 추가해 **162개 통과**를 확인했으며,
세부 범위는 [몬테카를로 검증 기록](monte-carlo-validation.md)에 정리한다.
5번 샘플 CLI 테스트 19개를 추가한 현재 전체는 **181개 통과**다.
샘플별 검증 내용은 [샘플 가이드](monte-carlo-samples.md)에 정리한다.

| 그룹 | 개수 | 주요 검증 |
|---|---:|---|
| `MonteCarloBasicTests` | 13 | 손계산 예시 B·고갈 이력, 고정 엔진 동등성, 반복 실행·경로 재사용, 호출 순서, 독립 실행 |
| `MonteCarloModelTests` | 9 | 출처별 메타데이터, 기간·개수·분위수 모델 검증, 불변성·파생값·기본 repr |
| `MonteCarloQuantileTests` | 4 | 포함형 선형 보간 대조, 같은 값 정확 보존, 극단 유한 값의 Decimal 대조, 비유한 표본 거부 |
| `MonteCarloValidationTests` | 8 | 전체 입력 선검증, 단계별 수치 오류 위치·중단, 잘못된 경로 수의 분모 오염 방지 |

기본 테스트에서 정상·전부 고갈·회복·정확히 0·미세 음수·1개 경로·1년 기간과
분위수의 음수·중복·홀짝·양 끝을 확인한다. 작은 표본은 별도 표준 라이브러리
`statistics.quantiles(method="inclusive")`와, 극단 금액은 80자리 Decimal과 대조한다.
표시용 반올림이나 테스트 허용오차는 고갈 판정에 적용하지 않는다.

추가 일회성 점검으로 합성 배치 64개의 5년 현금흐름·최초 고갈·분위수를 **별도
60자리 Decimal 계산**과 대조했다. 평균·경로 분포를 추정하는 검증이 아니라 집계
계산의 대조이며, 자동 테스트 개수에 포함하지 않는다. 기존 합성 고객 3종에 각
200개 경로를 적용한 실행·반복 일치도 확인했다. 최대 자원 사용량의 성능 검증은 아니다.

저장소 루트에서 다음 명령을 사용한다.

```bash
conda run -n retirewise-ai python -S -B -m unittest discover -s ai-pjt/retirement/tests -q
conda run -n retirewise-ai python -S -B -m unittest discover -s ai-pjt/retirement/tests -p test_monte_carlo.py -v
```

4번에서 다양한 재무 일정·기간·반복 수·수치 경계 조합의 독립 대조와 회귀 검증을
확대했고 5번 전용 샘플 CLI도 추가했다. 이후 별도 What-if 브랜치에서 공통 경로 비교와
[전용 샘플](what-if-samples.md)도 구현했다. 시장 가정 보정·신뢰구간·실제 입력 연결·
서비스 통합·전체 AI/BE/FE 테스트는 아직 완료한 것이 아니다.

생성 경로와 나이별 잔액 표본은 `O(N×T)` 공간을 사용한다. 각 나이를 정렬하므로
분위수 집계 시간은 `O(T×N log N)`이며, 전체 경로의 상세 현금흐름 객체를 결과에
저장하지 않는다. 응답 시간·메모리 상한·timeout·취소·병렬화는 별도 검증 대상이다.
