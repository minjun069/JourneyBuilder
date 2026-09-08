# 수익률 경로 생성기 사용·검증 가이드

상태: **수익률 경로 생성기 구현. 성공확률·분위수는 별도 몬테카를로 엔진에서 집계.**

[계산 규칙](monte-carlo-rules.md)의 수익률 생성 부분을
[`return_paths.py`](../return_paths.py)에 구현했다. 기존 현금흐름 계산이나
BE·Agent·컨텍스트·서비스 계약은 변경하지 않았다. 아래 가정·입력은 합성 예시이며
시장 전망이나 실제 고객 정보가 아니다.

## 1. 호출과 반환 규격

공개 진입점은 `retirement.generate_return_paths(profile, assumptions)`다.
검증된 `RetirementProfile`과 `SimulationAssumptions`를 받는다.

| 항목 | 규격 |
|---|---|
| 반환 타입 | `tuple[tuple[float, ...], ...]` |
| 바깥 인덱스 `k` | 경로 인덱스 `0..num_paths-1` |
| 안쪽 인덱스 `t` | `current_age + t` 나이 연도의 단순 수익률 |
| 경로 길이 | `end_age - current_age + 1`. 첫해·마지막 해를 모두 포함 |
| 수익률 | 유한한 연간 소수 비율, 항상 `-1` 초과 |
| 실제 사용하는 입력 | 현재·종료 나이, 수익률 평균·변동성, 경로 수, 시드 |
| 사용하지 않는 입력 | 자산·적립·생활비·연금·은퇴 나이·물가. 기존 모델 검증은 유지 |

전체 경로를 생성한 후 반환한다. 생성 도중 실패하면 예외를 발생시키며 앞서 생성한
경로를 부분 결과로 반환하지 않는다. 원본 모델과 반환 경로는 변경하지 않으므로
같은 경로를 여러 현금흐름 계산에 재사용할 수 있다.

`v=0`인 경로들은 같은 불변 tuple을 공유할 수 있다. 경로별 객체 식별자가 서로
달라야 한다는 계약은 없으며, 수익률을 바꾸려면 원본 수정이 아닌 새 경로를 만들어야 한다.
경로 tuple에는 고객 정보·버전 메타데이터·성공확률을 붙이지 않는다. 집계 결과와
실행 메타데이터는 [몬테카를로 엔진](monte-carlo-engine.md)에서 제공한다.

## 2. 기존 현금흐름 엔진에 전달하기

`ai-pjt/`에서 `retirewise-ai` 환경의 Python으로 실행할 수 있는 예시다.
기존 합성 샘플을 복사해 가정만 변경하며, `samples.py` 자체는 바꾸지 않는다.

```python
from dataclasses import replace
from math import isfinite

from retirement import calculate_cashflow_path, generate_return_paths
from retirement.samples import build_sample_cases

case = build_sample_cases()[0]
assumptions = replace(
    case.assumptions,
    annual_return_mean=0.05,       # 합성 검산 가정, 추천 수익률이 아님
    annual_return_volatility=0.10,
    annual_inflation_rate=0.02,
    num_paths=3,
    seed=42,
)
paths = generate_return_paths(case.profile, assumptions)

assert len(paths) == 3
assert all(len(path) == 51 for path in paths)  # 40~90세 양 끝 포함
assert all(isfinite(rate) and rate > -1 for path in paths for rate in path)
assert paths == generate_return_paths(case.profile, assumptions)

first = calculate_cashflow_path(case.profile, assumptions, annual_returns=paths[0])
assert tuple(year.annual_return_rate for year in first.years) == paths[0]
assert (first.years[0].age, first.years[-1].age) == (40, 90)
print(f"generated {len(paths)} paths x {len(paths[0])} years")
```

예상 출력은 `generated 3 paths x 51 years`다. 여기서는 생성과 단일 경로 전달만
보여준다. 첫 경로 결과가 전체 표본의 평균·중앙값·성공확률을 뜻하지 않는다.
기존 `python -m retirement.samples` 명령도 계속 고정 가정 현금흐름만 실행한다.
몬테카를로 집계 CLI는 5번 작업에서 추가한다.

## 3. 구현상 수치 처리

입력 `m`, `v`는 단순 연수익률의 산술 평균·표준편차다. 로그수익률 모수로의
변환 수학식은 [계산 규칙 3절](monte-carlo-rules.md#3-수익률-분포-총수익-배수의-로그정규분포)을
따른다. `_lognormal_parameters`는 그 변환을 담당하는 내부 함수이며 호출용 API가 아니다.

- `v=0`은 모수 변환·RNG 생성·난수 추출 없이 입력 `m`을 그대로 반복한다.
- `c=v/(1+m) <= 1`인 영역에서는 `log1p(c²)`를 사용한다. 표준편차는
  `c × sqrt(log1p(c²)/c²)`로 계산하고, `c²`가 수치적으로 0이면 해당 비율의
  극한값 1을 사용한다. 제곱이 0으로 반올림돼도 표현 가능한 작은 표준편차를 보존한다.
- 큰 비율에서는 `L=ln(v)-ln(1+m)`로 두고 로그분산을
  `2L + log1p(exp(-2L))`로 계산한다. 비율 자체나 그 제곱의 overflow를 피한다.
- 마지막 수익률 변환은 `expm1(mu + sigma × Z)`다. 작은 값에서 사용하는
  `log1p`·`expm1`의 동작은 [Python 공식 문서](https://docs.python.org/3.13/library/math.html#math.expm1)를
  참고한다. 부동소수점으로 모든 극단 입력을 정확히 표현할 수 있다는 뜻은 아니다.

작은 양의 변동성이어도 변환된 표준편차 자체가 0이 되면 계산 오류로 처리한다.
생성된 수익률이 `-1`로 반올림되거나 지수 변환이 overflow하는 경우도 오류다.
경계값으로 잘라내기, 재추출, 표본 평균·표준편차 강제 보정은 하지 않는다.

## 4. 오류와 재현성

잘못된 입력 모델 타입은 기존 `ModelValidationError`로 거부한다.
모수 변환·수익률 생성의 수치 실패는 공개 예외 `ReturnPathGenerationError`다.

| 예외 필드 | 의미 |
|---|---|
| `field_name` | `log_return_mean`, `log_return_volatility`, `standard_normal`, `log_return`, `annual_returns` 중 오류가 발생한 계산 항목 |
| `path_index` | 0부터 시작하는 경로 인덱스. 공통 모수 변환 오류이면 `None` |
| `age` | 오류가 발생한 연도의 나이. 공통 모수 변환 오류이면 `None` |

오류에 원본 금액·가정값·전체 경로를 포함하지 않는다. 이 예외는 재무적 고갈이 아니며
경로 1개의 실패로 집계하면 안 된다. 서비스 연결 시 traceback의 로컬 변수나 요청
본문까지 안전하게 로깅된다는 의미는 아니므로 로깅 정책은 별도로 검토해야 한다.

호출마다 독립된 `Random(seed)` 하나를 만들고 경로순·나이순으로 `gauss(0, 1)`을
호출한다. 전역 난수 상태는 읽거나 변경하지 않는다. 고객 조건이나 물가를 바꿔도
동일한 기간·시장 평균·변동성·시드·경로 수라면 같은 경로가 나온다.
경로 수만 늘리면 기존 앞부분을 유지하지만 기간을 바꾸면 같은 대응을 보장하지 않는다.
재현성은 같은 코드·Python 버전·실행 환경 안의 보장이며, 향후 What-if에서도 기간과
경로 인덱스를 명시적으로 맞춰야 한다.

## 5. 검증 기록

2026-09-08, 2번 작업 완료 시점에 Miniforge `retirewise-ai`의 Python 3.13.15에서 전용 테스트
**102개 통과: 기존 73개 + 신규 29개**를 확인했다. 신규 테스트는
[`test_return_paths.py`](../tests/test_return_paths.py)에 있으며, subtest 조합은
테스트 메서드 개수에 더하지 않는다.

| 테스트 그룹 | 개수 | 주요 검증 |
|---|---:|---|
| `ReturnParameterTests` | 5 | 문서 수치·이론 모멘트, 80자리 Decimal 모수 대조, 큰 비율·작은 변동성·표현 불가 처리 |
| `ReturnPathBasicTests` | 17 | 경로 모양·순서, 시드·전역 RNG 분리, 0 변동성, 현금흐름 전달, 불변성, 입력 타입, 표본 점검 |
| `ReturnPathNumericTests` | 7 | 중간 경로 오류와 재추출 없음, 지수 overflow, 비유한 값, 극단 입력, 최소 양수 변동성 |

- 모수의 일반 입력 30조합을 독립적인 80자리 Decimal 수식과 비교한다.
  비교 허용오차는 상대 `5e-13`, 절대 `1e-16`이며 별도의 극단 입력도 대조한다.
- 변동성 0의 경로를 기존 현금흐름 예시 A/C에 적용해 결과 전체의 정확한 일치,
  중간 고갈 후 회복 이력 보존을 확인한다. 성공확률을 집계하는 테스트는 아직 아니다.
- 정규 충격을 제어한 테스트와 실제 고정 시드 테스트를 함께 사용해 추출 순서와
  경로별 난수 진행을 검증한다. 오류를 주입한 테스트는 해당 경로·나이에 중단하고
  추가 추출이나 부분 결과 반환이 없는지 확인한다.
- 비영 변동성에서 1년·103년, 변동성 0에서 100,000개×1년 경로 수를 확인한다.
  최대 경로 수와 최대 기간을 동시에 실행한 성능 검증은 아니다.
- 합성 가정 `m=0.05`, `v=0.10`, 시드 `20260908`의 20,000개 수익률 표본에서
  평균·모표준편차를 각각 절대 오차 `0.004` 이내로 점검한다. 표본 보정이나 시장
  적합성 검증이 아니며 경로 간·연도 간 독립성을 통계적으로 입증한 것도 아니다.
- 추가 수식 점검으로 고정 시드의 합성 모수 5,000조합을 변환·역변환해 확인했다.
  이는 별도 일회성 점검이며 102개 자동 테스트 수에는 포함하지 않는다.
- site-packages 없는 새 Python 프로세스에서도 생성기를 실행하는 테스트를 포함한다.

저장소 루트에서 전체 전용 테스트와 생성기 테스트만 각각 실행할 수 있다.

```bash
conda run -n retirewise-ai python -S -B -m unittest discover -s ai-pjt/retirement/tests -q
conda run -n retirewise-ai python -S -B -m unittest discover -s ai-pjt/retirement/tests -p test_return_paths.py -v
```

## 6. 남은 작업과 자원 한계

3번의 기존 현금흐름 반복 호출·성공확률·고갈·분위수 집계와 결과 메타데이터는
[집계 엔진 가이드](monte-carlo-engine.md)에 정리한다. 4번에서는 그 집계까지 포함한
경계 조건 검증을 확대하고, 5번에서 실제
몬테카를로 샘플 실행을 제공한다. What-if·실제 snapshot adapter·서비스 통합은
여전히 별도 기능이며, 전체 AI/BE/FE 테스트 통과를 주장하지 않는다.

현재 생성 API는 전체 경로를 보관한다. 비영 변동성의 시간·수익률 저장 공간은
`O(N×T)`이며, 현금흐름의 연간 상세 객체까지 모두 보관하는 구조는 아니다.
입력 상한 100,000개는 메모리·응답 시간 보장이 아니다. 대량 실행 성능·서비스
timeout·취소·병렬화는 미검증이며, 스트리밍 등 개선이 필요하면 별도로 설계·검증한다.
