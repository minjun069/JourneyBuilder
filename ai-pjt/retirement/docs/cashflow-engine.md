# 은퇴 현금흐름 엔진 개발·사용 가이드

상태: 엔진·기본 테스트 구현. 경계 검증과 샘플 실행 확장은 후속 작업이다.
이 문서는 팀원이 독립 모듈의 호출 위치, 결과 의미, 향후 연결 경계를 확인하기 위한
개발 문서다. 현재 서비스에서 이 엔진을 호출하는 것은 아니다.

## 1. 파일과 책임

| 파일 | 책임 |
|---|---|
| [models.py](../models.py) | 기존 내부 입력 모델·검증, `AssetPoint` |
| [cashflow.py](../cashflow.py) | 단일 경로 계산, 결과 모델, 계산 오류 |
| [test_cashflow.py](../tests/test_cashflow.py) | 손계산 A/B, 수익률 경로 대체·동등성, 기말 자산 경로 |
| [samples.py](../samples.py) | 현재 합성 입력 생성·검증; 실제 계산 출력은 후속 작업 |
| [cashflow-rules.md](cashflow-rules.md) | 연간 순서·물가·부족액 이월의 계산 명세 |

## 2. 호출 방법: 내부 Python 인터페이스

`ai-pjt/`가 import 경로인 환경에서 아래 예시를 실행한다. 추가 의존성이나 외부
API key가 필요 없다. 모든 입력은 합성 값이고 수익률은 권장 투자 가정이 아니다.

```python
from retirement import (
    RetirementExpensePeriod,
    RetirementProfile,
    SimulationAssumptions,
    calculate_cashflow,
    calculate_cashflow_path,
)

profile = RetirementProfile(
    current_age=60, retirement_age=60, end_age=61,
    liquid_assets=3_000_000, monthly_savings=0,
    retirement_expenses=(
        RetirementExpensePeriod(from_age=60, to_age=61, monthly_amount=100_000),
    ),
    pension_incomes=(),
)
assumptions = SimulationAssumptions(
    annual_return_mean=0.0,
    annual_return_volatility=0.0,
    annual_inflation_rate=0.0,
)

result = calculate_cashflow(profile, assumptions)
assert result.final_balance == 600_000.0
assert result.first_depletion_age is None
assert [point.age for point in result.asset_path] == [60, 61]

# 연도별 경로는 평균에 더하지 않고 완전히 대체한다.
same = calculate_cashflow_path(profile, assumptions, annual_returns=[0.0, 0.0])
assert same == result
```

- `calculate_cashflow(profile, assumptions) -> CashflowResult`: 모든 연도에
  `annual_return_mean` 적용. 변동성·반복 수·시드는 검증만 하고 사용하지 않는다.
- `calculate_cashflow_path(profile, assumptions, *, annual_returns) -> CashflowResult`:
  list/tuple의 수익률을 나이 순서대로 소비한다. 길이는 `end_age-current_age+1`,
  원소는 유한한 int/float이며 bool 제외, 각 값은 `-1` 초과다.
- 경로 실행에서는 평균·변동성·반복 수·시드가 결과를 바꾸지 않는다. 물가는 두 실행
  모두 `annual_inflation_rate`로 고정한다. generator나 NumPy 배열은 직접 받지 않는다.
- 두 함수 모두 검증된 입력 모델이 필요하다. dict·금액 문자열·실제 snapshot을
  대신 파싱하지 않는다. 전달한 모델과 수익률 경로를 수정하지 않는다.

## 3. 결과 읽기

`CashflowResult.years`는 현재 나이부터 종료 나이까지의 `CashflowYear` tuple이다.
입력 자산은 첫해 연초 잔액이며 별도 연말 점으로 중복 삽입하지 않는다.

| 연도 결과 필드/속성 | 의미 |
|---|---|
| `age` | 해당 온전한 12개월의 나이 |
| `opening_balance` | 연초 잔액; 전년도 부족액도 음수로 이월 |
| `annual_return_rate` | 해당 연도에 실제 적용한 소수 비율 |
| `investment_return` | 양수 연초 잔액에만 붙이는 투자손익 |
| `savings` | 은퇴 전 금융자산 연 적립 총액 |
| `pension_income` | 수령기간에 해당하는 연금 연 합계 |
| `expenses` | 현재 나이 기준 누적 물가를 반영한 연 생활비 |
| `closing_balance` | 수익 → 적립 → 연금 → 지출 순서 반영 후 연말 잔액 |
| `shortfall` | `max(-closing_balance, 0)`; 그 시점의 미충당 부족액 |
| `asset_point` | 해당 연말의 `AssetPoint` |

전체 결과에는 `asset_path`, `final_balance`, `final_shortfall`,
`first_depletion_age` 속성이 있다. 요약은 연도별 결과에서 파생되므로 별도 저장된
최종값·고갈 이력과 서로 어긋나지 않는다. `asset_path` 접근 시 연말 점 tuple을
생성한다. 전체 결과·연도 결과·연말 점 모두 불변이며 금액은 기본 `repr`에 노출하지 않는다.

양수 잔액은 남은 금융자산이고 음수는 계산상 누적 미충당 부족액이다. 실제 대출을
자동 생성하거나 생활비 지급 완료를 뜻하지 않는다. 부족액에는 수익·이자를 적용하지
않고 여러 해의 `shortfall`을 더하지 않는다. 회복 후 최종 잔액이 양수여도 과거
`first_depletion_age`는 남는다. 고갈이 없으면 `None`, 잔액이 정확히 0이면 고갈이 아니다.

모든 계산 금액은 원 단위 float이며 내부 반올림은 하지 않는다. 표시용 반올림이
고갈 판정을 바꿔서는 안 된다. 이 결과는 확률적 기대값·성공확률·현재 BE 준비율이 아니다.

## 4. 오류와 수치 범위

- 입력 모델·수익률 경로 오류: `ModelValidationError` (`ValueError` 하위),
  `field_name`, `message` 제공. 필수 인자 누락은 Python의 `TypeError`다.
- 유한한 입력으로 계산 중 표현 범위를 벗어남: `CashflowCalculationError`
  (`ArithmeticError` 하위), `field_name`, `age` 제공. 최초 자산 변환 오류의 나이는
  `None`이다. 메시지에는 입력 금액이나 경로 원문을 포함하지 않는다.
- 정수 입력을 float로 바꾸는 단계, 투자손익·연 현금흐름, 물가 거듭제곱,
  잔액의 각 누적 연산을 검사한다. 오류 시 부분 경로를 반환하지 않는다.
- 은퇴 전 생활비와 명시적 0원 생활비는 0이며 불필요한 물가 거듭제곱을 계산하지
  않는다. 이미 은퇴한 경우의 미사용 적립액도 연 금액으로 변환하지 않는다.
- 결과 생성자는 유한한 필드·비음수 수입/지출·연속 나이·잔액 이월을 검증한다.
  생성자를 직접 호출한다고 전체 재무 계산식을 재실행하는 것은 아니므로 실제
  계산 결과는 엔진 함수로 생성한다.

## 5. 검증과 이후 연결

저장소 루트에서:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s ai-pjt/retirement/tests -v
```

기본 테스트는 계산 규칙의 A/B를 독립적인 숫자 기대값으로 검증한다. 경로의 연도
순서, 평균 대체, 고정 경로 동등성, 연말 점 개수도 확인한다. 고갈·경계·오류·
반복 호출 검증과 실제 샘플 실행은 다음 작업에서 추가한다.

다음 기능은 이 브랜치에서 구현하지 않는다.

1. 몬테카를로: 난수 경로 생성과 분포·시드 정책, 경로별 실행 및 확률·분위수 집계.
   경로별 수익률을 공통 계산부에 전달할 수 있지만 대량 실행 성능은 아직 보장하지 않는다.
2. What-if: 원본을 보존한 적립액·은퇴 나이·생활비 변경, 같은 시장 경로로 비교.
3. Adapter: 기존 저장 snapshot을 내부 입력으로 변환하고 미지원 조건·단위를 명시.
4. 서비스 연결: 기존 Agent 소유 호출부·BE/FE 계약과 연결하고 오류·인증·timeout 검증.

기존 Agent 라우팅·컨텍스트·상담사 매뉴얼, BE 준비율·보고서·DB, FE·공용 설정은
수정하지 않는다. 독립 계산부 완성이 서비스 통합 완료를 뜻하지 않는다.
