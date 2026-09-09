# 은퇴 현금흐름 엔진 개발·사용 가이드

상태: 엔진·기본·경계 조건 테스트와 합성 샘플 실행 구현 완료.
이 문서는 팀원이 독립 모듈의 호출 위치, 결과 의미, 향후 연결 경계를 확인하기 위한
개발 문서다. 현재 서비스에서 이 엔진을 호출하는 것은 아니다.

## 1. 파일과 책임

| 파일 | 책임 |
|---|---|
| [models.py](../models.py) | 기존 내부 입력 모델·검증, `AssetPoint` |
| [cashflow.py](../cashflow.py) | 단일 경로 계산, 결과 모델, 계산 오류 |
| [test_cashflow.py](../tests/test_cashflow.py) | 손계산 A/B, 수익률 경로 대체·동등성, 기말 자산 경로 |
| [test_cashflow_edges.py](../tests/test_cashflow_edges.py) | 손계산 C, 경계·오류·불변성, Decimal 참조 검산 |
| [samples.py](../samples.py) | 합성 입력 생성, 실제 계산 요약·연도별 상세 실행 |
| [test_samples.py](../tests/test_samples.py) | 샘플 검산, 엔진 호출, CLI와 상세·반올림 표시 검증 |
| [cashflow-rules.md](cashflow-rules.md) | 연간 순서·물가·부족액 이월의 계산 명세 |

## 2. 호출 방법: 내부 Python 인터페이스

`ai-pjt/`가 import 경로인 환경에서 아래 예시를 실행한다. 추가 의존성이나 외부
API key가 필요 없다. 모든 입력은 합성 값이고 수익률은 권장 투자 가정이 아니다.

```python
from retirement_simulation import (
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
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s ai-pjt/retirement_simulation/tests -v
```

기본 테스트는 계산 규칙의 A/B를 독립적인 숫자 기대값으로 검증한다. 경계 테스트는
C의 고갈·회복, 입력·출력 불변성, 연금 일정·물가, 잘못된 경로, 비유한 계산을
검증한다. Decimal 별도 참조 계산과 243가지 경로·물가 조합도 비교한다.
상세 매핑과 명령은 [검증 기록](cashflow-validation.md)에 있다. 샘플은 실제 엔진을
호출하며 결과를 별도 연간 합계로 검산한다. 출력 상수를 계산 결과로 대신 쓰지 않는다.

다음 기능은 이 브랜치에서 구현하지 않는다.

1. 몬테카를로: 난수 경로 생성과 분포·시드 정책, 경로별 실행 및 확률·분위수 집계.
   경로별 수익률을 공통 계산부에 전달할 수 있지만 대량 실행 성능은 아직 보장하지 않는다.
2. What-if: 원본을 보존한 적립액·은퇴 나이·생활비 변경, 같은 시장 경로로 비교.
3. Adapter: 기존 저장 snapshot을 내부 입력으로 변환하고 미지원 조건·단위를 명시.
4. 서비스 연결: 기존 Agent 소유 호출부·BE/FE 계약과 연결하고 오류·인증·timeout 검증.

기존 Agent 라우팅·컨텍스트·상담사 매뉴얼, BE 준비율·보고서·DB, FE·공용 설정은
수정하지 않는다. 독립 계산부 완성이 서비스 통합 완료를 뜻하지 않는다.

## 6. 합성 샘플 실행

아래 명령은 모두 `ai-pjt/`에서 실행한다. 기본 실행은 세 사례의 요약,
`--details`는 요약과 연도별 상세 내역, `--help`는 옵션 설명을 출력한다.

```bash
python3 -S -B -m retirement_simulation.samples
python3 -S -B -m retirement_simulation.samples --details
python3 -S -B -m retirement_simulation.samples --help
```

현재 샘플의 수익률·변동성·물가는 모두 0이다. 비교하기 쉬운 테스트 설정이며
시장 전망이나 권장 가정이 아니다. 고정 실행에서 `num_paths=100`, `seed=42`는
사용하지 않으며 100개 확률 경로를 계산한 것이 아니다.

| 사례 이름 | 기간/연도 수 | 최종 계산 잔액(원) | 최종 미충당 부족액(원) | 최초 고갈 나이 |
|---|---|---:|---:|---:|
| `working_with_phased_expenses` | 40~90세 / 51년 | -154,400,000 | 154,400,000 | 73 |
| `already_retired_without_pension` | 68~90세 / 23년 | -361,600,000 | 361,600,000 | 72 |
| `pension_start_gap` | 59~90세 / 32년 | -480,000,000 | 480,000,000 | 62 |

검산 기준은 다음과 같다. 각각 원 단위이며 0 수익률·0 물가 설정에만 해당한다.

```text
사례 1 = 100,000,000 + 20×6,000,000 + 26×12,000,000
         - 15×24,000,000 - 16×20,400,000 = -154,400,000
사례 2 = 80,000,000 - 23×19,200,000 = -361,600,000
사례 3 = 60,000,000 + 6,000,000 + 15×13,200,000
         - 31×24,000,000 = -480,000,000
```

예를 들어 사례 1의 실제 요약 행은 다음과 같다.

```text
working_with_phased_expenses: years=51, final_balance=-154,400,000.00, final_shortfall=154,400,000.00, first_depletion_age=73
```

`--details`의 열 순서는 나이, 기초 잔액, 소수 수익률, 투자손익, 적립, 연금,
생활비, 기말 잔액, 기말 부족액이다. 금액은 소수점 2자리, 수익률은 6자리로
**표시만** 반올림한다. 실제 입력을 받는 CLI가 아니라 합성 사례 전용 실행기다.

첫 고갈 나이가 `none`이면 기간 내 음수 기말 잔액이 없다는 의미다. 세 샘플은
모두 부족액이 생기지만 이것은 테스트 사례의 결과일 뿐 실제 고객의 상태가 아니다.
특히 `final_balance` 음수는 실제 보유 금융자산·부채로 그대로 저장하면 안 된다.
미세한 음수가 `-0.00`으로 보여도 최초 고갈 나이는 반올림 전 값으로 표시한다.
몬테카를로 실행은 별도 `python -m retirement_simulation.monte_carlo_samples` 명령을 사용하며
[샘플 가이드](monte-carlo-samples.md)를 참고한다. 고정 샘플의 입력·가정·계산 결과는 유지된다.

## 7. 팀원 인수인계와 다음 작업

아래 순서는 현금흐름 브랜치 완료 시점의 계획이다. 이후 몬테카를로의 계산·검증·
전용 샘플까지 완료했으며 현재 진행 상태는 [README](../README.md)를 기준으로 본다.

완료 범위는 계산 규칙, 단일 경로 엔진·불변 결과 모델, 손계산·경계 검증,
합성 샘플·내부 사용 문서다. 사용자는 입력 모델을 생성해 함수를 직접 호출할 수
있으며 기존 서비스에서는 아직 호출하지 않는다. 변경은 `ai-pjt/retirement_simulation/` 안에만 있다.

1. 현재 브랜치 검토 후 사용자 요청에 따라 푸시·MR·병합한다. 자동 수행하지 않는다.
2. `feat/ai-retirement-monte-carlo`: 수익률 분포·완전 손실 경계 처리, 시드·경로
   재현성, 성공·고갈·분위수 정의를 정하고 공통 계산부로 여러 경로를 계산한다.
   현재 지원 경계는 연수익률 `>-1`이며 생성 경로를 조용히 잘라 보정하면 안 된다.
3. `feat/ai-retirement-what-if`: 적립액·은퇴 나이·생활비 변경 시나리오를 원본
   복사로 만들고 같은 시장 경로로 비교한다. 은퇴 나이 변경 시 생활비 구간의
   완전한 재구성과 계산 기간이 다른 경로의 정렬 정책을 함께 정한다.
4. 독립 MVP 재현성·결과 해석·대량 실행 성능을 확인하고 필요하면 최적화한다.
5. `feat/ai-retirement-adapter`: 기존 입력 매핑·누락·단위·미지원 조건 처리와
   외부 요청·응답 규격을 정한다. 금융자산·연금 계좌, 적립·연금 중복 반영을 구분한다.
6. `feat/ai-retirement-integration`: 실제 호출부와 표시를 연결한다. 호출 주체,
   결과 위치, 오류 전달·timeout 등 공유 영역의 변경 범위는 이때 명확히 해야 한다.

다른 AI 담당자의 Agent 라우팅·컨텍스트·상담사 매뉴얼을 이 모듈에 재구현하지
않는다. 공유 영역 변경 없이 계산부를 개발할 수 있지만 실제 서비스 연결까지
아무 연결 작업 없이 자동 완성되는 구조는 아니다. 후속 담당자는 위 내부 함수를
어디에서 호출할지 결정하고 통합 테스트를 별도로 수행해야 한다.
