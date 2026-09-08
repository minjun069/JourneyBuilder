# What-if 시나리오 생성 가이드

상태: **2번 시나리오 생성·입력 검증·기본 테스트 구현 완료.**
후속 3번에서 공통 시장 경로 비교·결과 모델, 4번에서 확대 경계 검증을 완료했다.
[비교 계산 가이드](what-if-engine.md)를 참고한다. 5번 [전용 CLI](what-if-samples.md)도 완료했으며
실제 데이터·서비스 연결은 남아 있다.

[`what_if.py`](../what_if.py)는 [계산·비교 규칙](what-if-rules.md)의 입력 변경 부분만
구현한다. 기존 `RetirementProfile` 검증을 재사용하며 계산·난수 생성·저장 작업을 하지 않는다.
외부 API나 Agent 도구가 아니라 `retirement` 패키지에서 import하는 내부 Python 입구다.

## 1. 공개 인터페이스

```python
from retirement import WhatIfChanges, create_what_if_profile
```

| 인터페이스 | 입력·반환 |
|---|---|
| `WhatIfChanges(*, monthly_savings=None, retirement_age=None, retirement_expenses=None)` | 불변 변경 조건. 모든 필드는 선택적 keyword 인자 |
| `create_what_if_profile(profile, changes) -> RetirementProfile` | 기준 프로필과 변경 조건을 받아 검증된 새 불변 프로필 반환. 두 인자 모두 필수 |

한 번 호출할 때 변경안 하나를 만든다. 여러 안은 **동일한 기준 프로필**에 각각 적용한다.
이 함수는 이전 호출의 결과·고객 상태를 기억하지 않는다. 복수 비교는 3번의
`WhatIfScenario`로 식별자를 붙여 1~10개씩 전달한다. `WhatIfChanges`나 이 생성 함수에
식별자·라벨·고객 ID를 추가한 것은 아니다. 구체적인 배치 규격은 비교 계산 가이드에 있다.

### 선택 필드

- `monthly_savings`: 변경 후 월 적립 **총액**, 0 이상 원 단위 정수. 증액분이 아니다.
  적립 0은 중단이고 생략/`None`은 기준값 유지다. bool·문자열·실수는 거부한다.
- `retirement_age`: 변경 후 은퇴 나이, 18~100 정수. 기준 프로필과 대조하는 추가
  검증은 아래 표를 따른다. 연금 일정은 은퇴 나이와 함께 바뀌지 않는다.
- `retirement_expenses`: `RetirementExpensePeriod`의 list/tuple로 **전체 일정 교체**.
  입력 금액은 현재가치 월 총액이며, list는 변경 모델 생성 시 즉시 tuple로 복사한다.
  dict·generator·생활비 증감률은 지원하지 않는다.

현재 나이·종료 나이·기초 금융자산·연금 목록은 항상 기준값을 보존한다. 시장 가정은
입력받지 않는다. 변경 모델과 결과 프로필은 frozen이며, 변경 모델의 기본 `repr`에는
입력 조건을 표시하지 않는다. 이는 고객 정보를 자동으로 로그에 남겨도 된다는 뜻은 아니다.

## 2. 두 단계 검증과 오류

| 시점 | 검증 범위 |
|---|---|
| `WhatIfChanges` 생성 | 선택적 금액·나이의 타입/범위, 생활비 컬렉션과 구간 모델 타입. list를 tuple로 복사 |
| `create_what_if_profile` 호출 | 인자 모델 타입·과거 은퇴 변경 제한. 지정된 세 변경을 한 번에 적용한 뒤 기존 프로필 생성자가 기간·생활비 중첩·필수 연도 보장을 검증 |

변경 모델만 생성됐다고 특정 기준안에 적용 가능한 것은 아니다. 생활비 구간은 변경
모델에서 입력 순서를 보존하며 **반환 프로필에서 나이순으로 정렬**한다. 중첩·누락
검증을 두 곳에 복제하지 않는다. 빈 일정도 변경 모델에서는 `()`로 보존하고 적용 시 거부한다.

- 은퇴를 앞당겨 생긴 생활비 누락은 오류다. 누락분을 채운 전체 일정을 은퇴 나이와
  함께 지정하면 새 조건 전체로 검증한다. 기존 일정이 이미 그 나이를 덮으면 생략해도 된다.
- 생활비 일부 구간만 전달하면 기준 구간과 합치지 않는다. 필수 연도가 빠지면 오류다.
  명시적 0원 구간은 허용하고, 빈 일정을 미지정으로 취급하지 않는다.
- 구간 양 끝은 포함한다. 비활성 구간의 중첩도 오류다. 반대로 비중복 구간은 계산
  기간 밖이어도 자동 삭제·이동하지 않는다. 은퇴 연기로 비활성화된 생활비도 보존한다.
- 기준 은퇴 나이가 현재 나이 이상이면 변경 나이는 현재 나이 이상이어야 한다.
  현재 나이에 은퇴하는 계획은 연기할 수 있지만 과거로 앞당길 수 없다.
- 기준 은퇴 나이가 현재보다 작으면 미지정·원래 은퇴 나이 재지정만 허용한다.
  생활비·적립액 변경은 가능하지만 기존 엔진은 은퇴 후 적립을 계속 0으로 계산한다.
- 변경 없음도 기준안과 **값은 같고 객체는 다른** 새 프로필을 반환한다.
- 금액은 기존 모델과 동일하게 비음수 정수로 검증한다. 매우 큰 정수가 프로필 생성에
  성공하더라도 부동소수점 계산까지 가능하다는 뜻은 아니다. 수치 실행 오류는 계산기의 책임이다.

잘못된 타입·값·구간은 `ModelValidationError(field_name, message)`다.
메시지에는 입력 원문이나 금액을 포함하지 않는다. 호출 인자 누락·미지원 keyword·
변경 모델의 positional 인자는 Python의 `TypeError`다.

| 대표 실패 | `field_name` |
|---|---|
| 기준값이 `RetirementProfile` 아님 | `profile` |
| 변경 조건이 `WhatIfChanges` 아님 | `changes` |
| 적립액 타입·범위 오류 | `monthly_savings` |
| 은퇴 나이 타입·범위·과거 변경 제한 | `retirement_age` |
| 종료 나이를 넘는 은퇴 나이 | `end_age` — 기존 프로필 검증의 필드명 유지 |
| 생활비 컬렉션 타입·중첩·필수 연도 누락 | `retirement_expenses` |

## 3. 실행 가능한 합성 예시

`ai-pjt/`에서 Miniforge `retirewise-ai`의 Python으로 실행한다. 아래 금액은 검산용이며
시장 전망이나 권장 조건이 아니다. 기존 현금흐름 엔진을 호출하는 예시이지 What-if
확률 비교 계산기가 구현됐다는 뜻은 아니다.

```python
from retirement import (
    ModelValidationError, PensionIncome, RetirementExpensePeriod,
    RetirementProfile, SimulationAssumptions, WhatIfChanges,
    calculate_cashflow, create_what_if_profile,
)

baseline = RetirementProfile(
    current_age=58, retirement_age=60, end_age=61,
    liquid_assets=1200, monthly_savings=100,
    retirement_expenses=(
        RetirementExpensePeriod(from_age=60, to_age=61, monthly_amount=100),
    ),
    pension_incomes=(PensionIncome(start_age=61, end_age=61, monthly_amount=50),),
)
saving_more = create_what_if_profile(baseline, WhatIfChanges(monthly_savings=200))
retiring_early = create_what_if_profile(baseline, WhatIfChanges(
    retirement_age=59,
    retirement_expenses=(
        RetirementExpensePeriod(from_age=59, to_age=61, monthly_amount=100),
    ),
))
assert baseline.monthly_savings == retiring_early.monthly_savings == 100
assert baseline.retirement_age == saving_more.retirement_age == 60
assert retiring_early.pension_incomes == baseline.pension_incomes
unchanged = create_what_if_profile(baseline, WhatIfChanges())
assert unchanged == baseline and unchanged is not baseline

try:
    create_what_if_profile(baseline, WhatIfChanges(retirement_age=59))
except ModelValidationError as error:
    assert error.field_name == "retirement_expenses"
else:
    raise AssertionError("missing expenses must be rejected")

config = SimulationAssumptions(
    annual_return_mean=0, annual_return_volatility=0, annual_inflation_rate=0,
)
expected_balances = (
    (2400, 3600, 2400, 1800),
    (3600, 6000, 4800, 4200),
    (2400, 1200, 0, -600),
)
for candidate, expected in zip((baseline, saving_more, retiring_early), expected_balances):
    result = calculate_cashflow(candidate, config)
    balances = tuple(year.closing_balance for year in result.years)
    assert balances == expected
    print(balances)
```

출력은 기준안·적립 증가안·조기 은퇴안 순서의 연말 잔액이다.
조기 은퇴안의 60세 잔액 0은 고갈이 아니며 61세의 -600원에서 처음 고갈한다.
여기서는 평균·변동성·물가가 모두 0인 고정 계산을 사용한다.
실제 몬테카를로 비교는 3번에서 경로를 한 번 생성해 모든 안에 동일하게 전달한다.

## 4. 검증 범위와 다음 작업

2026-09-08, Python 3.13.15·Miniforge `retirewise-ai`에서
[`test_what_if.py`](../tests/test_what_if.py)의 **38개 테스트**를 추가했다.

| 그룹 | 개수 | 확인 내용 |
|---|---:|---|
| `WhatIfChangesTests` | 9 | 선택 필드·0/미지정·타입·범위·list 복사·불변성·기본 repr·미지원 필드 |
| `WhatIfProfileTests` | 24 | 동시 변경·전체 일정 교체·구간·은퇴 제한·원본 보존·반복 독립성·오류·계산/RNG 미호출 |
| `WhatIfCashflowCompatibilityTests` | 5 | 규칙 A~C의 프로필 생성 후 기존 엔진 실행, 물가 기준 유지·은퇴 후 적립 없음 |

2번 완료 시 전체는 기존 181개 + 신규 38개 = **219개**였으며 일반·`-S` 실행 모두 통과했다.
3절의 예시 코드도 그대로 실행해 손계산 기대값과 일치함을 확인했다. 저장소 루트에서:

```bash
conda run -n retirewise-ai python -B -m unittest discover -s ai-pjt/retirement/tests -q
conda run -n retirewise-ai python -S -B -m unittest discover -s ai-pjt/retirement/tests -q
conda run -n retirewise-ai python -S -B -m unittest discover -s ai-pjt/retirement/tests -p test_what_if.py -v
```

기본 테스트는 공개 생성 함수를 실제 호출한다. 후속 3번에서 확률·분위수 차이·출처·
공통 경로 생성 횟수·비교 오류 검증 32개를 추가했고, 4번에서 독립 검산·일정·수치 경계
테스트 25개를 더해 당시 **276개 통과**를 확인했다. 상세 범위는
[What-if 검증 기록](what-if-validation.md)에 있다. 5번 [전용 샘플](what-if-samples.md)의
24개를 포함한 현재 전체는 **300개 통과**다. API·Agent·실제 입력 adapter·서비스 연결은
변경하지 않았다.
