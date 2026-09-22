"""적립형 연금의 '예상 월 수령액'을 적립액에서 환산한다 (순수 계산).

## 왜 필요한가

상담 화면은 퇴직연금(IRP)·연금저축에서 **적립액과 월 납입액**을 받는다. 안내 문구도
그렇게 적으라고 말한다. 그래서 이 행들에는 `expected_amount`(예상 월 수령액)가
비어 있고, 어댑터는 그 칸이 비었다는 이유로 시뮬레이션 전체를 막아 왔다.
사용자가 이미 적어 둔 값으로 계산할 수 있는 수치를 이유로 화면을 막지 않는다.

## 왜 어댑터가 아니라 별도 모듈인가

여기는 금리·기간 문법·단위(퍼센트/소수) 판정이 섞인 순수 산수다. 어댑터는 그와
성격이 다른 "문제 수집기"이고 이미 충분히 길다. 분리해 두면 스냅샷 한 벌을 만들지
않고도 환산 규칙만 단위 테스트할 수 있고, 반대로 어댑터 테스트는 '파생이 되었는지'
한 가지만 본다.

## 계산

- 개시까지 연 단위 복리로 적립액을 굴린다.
- 월 납입액은 같은 기간 동안 월리(연이율/12) 적립 연금(FV) 으로 더한다.
- **모듈이 실제로 지급하는 개월 수**로 나눈다(아래 '수령 창' 참고).

이자·물가·세금을 더 정교하게 다루지 않는다. 목적은 "적어 둔 값으로 대략 얼마가
나오는가" 한 줄이고, 그 뒤의 불확실성은 시뮬레이션 모듈이 경로로 다룬다.

## 수령 창 — 모듈이 지급하는 대로 계산한다

여기서 만든 금액은 모듈의 `PensionIncome.monthly_amount` 가 되고,
packages/retirement_simulation/cashflow.py 는 `start_age <= age <= end_age` 인 해마다
그 금액을 지급한다. 즉 **양끝을 포함하는** 창이다(models.PensionIncome 의
'inclusive start/end age range'). 그래서:

- 'N년' 의 마지막 지급 나이는 `개시나이 + N - 1` 이다. input_adapter._pension_incomes
  도 같은 식(`start + int(period[:-1]) - 1`)으로 창을 만든다.
- '종신' 의 마지막 지급 나이는 기대수명(end_age)이다.
- 지급은 `max(개시나이, 현재나이)` 부터 시작한다. cashflow 는 현재 나이부터만
  연도를 돌기 때문에, 이미 개시된 연금의 지난 회차는 계획에 들어오지 않는다.

이 창으로 나누지 않으면 적립액과 지급액이 어긋난다. 적게 나누면(종신을 N*12 로)
계획에 없는 돈이 생기고, 많이 나누면(이미 개시된 연금을 전체 기간으로) 사용자가
적어 둔 돈의 일부가 계획에 영영 도달하지 않는다. 지켜야 할 불변식은 하나다 —
**나눈 자금 == 모듈이 지급하는 총액**.

기간이 기대수명을 넘는 'N년'(예: 개시 80, 20년, 기대수명 90)은 여기서 자르지
않는다. 여기서 만드는 값은 "실제로 매달 받는 돈" 이고 그것은 시뮬레이션의
기대수명과 무관하다. 지평 밖 회차를 빼는 일은 모듈의 몫이며
(`pension_horizon_limited` 안내), 사용자가 직접 적은 금액에도 똑같이 적용된다.
여기서 미리 잘라 나누면 사용자가 받지도 않을 큰 월 수령액이 만들어진다.

## 저장하지 않는다

여기서 만든 값은 요청 안에서만 산다. 근거는 adapter._pension_assets 의 주석에 있다.
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from math import isfinite
import re


# 모듈 analysis_output._MAX_JSON_NUMBER. 이보다 큰 금액은 모듈이 직렬화 단계에서
# 거부한다(ANALYSIS_REJECTED). 그런 값을 만들어 넘기느니 파생을 포기하고 기존
# "예상 월 수령액을 입력해 주세요" 를 남긴다 — 사용자가 고칠 수 있는 쪽이다.
MAX_SAFE_AMOUNT = 9_007_199_254_740_991

# 연 수익률 상한(소수). 연 100% 를 넘는 값은 연금 상품의 기대수익률일 수 없다.
# expected_return_rate 는 DecimalField(max_digits=5) 라 999.99(%) 까지 들어갈 수
# 있는데, 그런 행으로 수십 년을 복리 계산하면 사용자가 고칠 수 없는
# ANALYSIS_REJECTED 나 천문학적 월 수령액이 나온다. 차라리 파생하지 않는다.
MAX_ANNUAL_RATE = 1.0

# 어댑터가 사용자에게 돌려주는 기간 문법과 같다. 여기서는 '계산할 수 있는가' 만
# 본다 — 문법 위반 자체는 어댑터가 별도 issue 로 안내한다.
_PERIOD_YEARS = re.compile(r"([1-9][0-9]{0,2})년")

LIFETIME_PERIOD = "종신"


def _is_int(value: object) -> bool:
    # bool 은 int 서브클래스지만 나이·금액으로는 유효하지 않다(어댑터와 같은 판정).
    return isinstance(value, int) and not isinstance(value, bool)


def _text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    return value.strip() or None


def _amount(value: object) -> float | None:
    """적립액·월 납입액. 비어 있으면 0, 숫자가 아니거나 음수면 None(파생 불가)."""
    if value is None:
        return 0.0
    if isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        value = float(value)
    if not isinstance(value, (int, float)) or not isfinite(value) or value < 0:
        return None
    return float(value)


def _finite_number(value: object) -> float | None:
    """읽을 수 있는 음이 아닌 실수면 float, 아니면 None."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        value = float(value)
    if not isinstance(value, (int, float)) or not isfinite(value) or value < 0:
        return None
    return float(value)


def rate_from_percent(value: object) -> float | None:
    """상담 행의 기대수익률(**퍼센트**)을 소수로 바꾼다. 읽을 수 없으면 None.

    consulting.PensionAsset.expected_return_rate 는 DecimalField(max_digits=5,
    decimal_places=2) 이고 입력 폼의 단위가 '%' 다. 즉 이 칸은 **언제나** 퍼센트다.
    그러므로 '1 보다 크면 퍼센트' 같은 추측을 하지 않는다. 추측하면 1.00(연 1%,
    연금보험·저축보험에서 평범한 값)이 연 100% 로 읽혀 1.01 과 600만 배가
    벌어진다. 3.50 -> 0.035, 1.00 -> 0.01, 0.50 -> 0.005.

    NOTE: reports/calculators._normalize_rate 는 아직 '1 보다 크면 퍼센트' 휴리스틱을
    쓴다(해당 파일은 이 과제의 범위 밖이라 그대로 둔다). 이 모듈은 의도적으로 더
    엄격하다 — 여기서는 칸의 단위가 스키마로 고정되어 있어 추측할 이유가 없다.
    보고서 쪽 휴리스틱은 별도 티켓으로 맞춰야 한다.
    """
    number = _finite_number(value)
    if number is None:
        return None
    rate = number / 100
    return rate if rate <= MAX_ANNUAL_RATE else None


def rate_from_fraction(value: object) -> float | None:
    """정책 기본 수익률(**소수**)을 그대로 읽는다. 읽을 수 없으면 None.

    policy.json 의 annual_return_mean 은 0.04 처럼 언제나 소수다(모듈
    SimulationAssumptions 도 소수를 받는다). 퍼센트 칸과 한 함수로 합치면 둘 중
    하나는 100배가 틀린다.
    """
    rate = _finite_number(value)
    if rate is None:
        return None
    return rate if rate <= MAX_ANNUAL_RATE else None


def payout_end_age(
    period: object, *, pension_start_age: int, end_age: int | None
) -> int | None:
    """마지막으로 지급받는 나이(포함). 기간을 읽을 수 없으면 None.

    'N년' -> 개시나이 + N - 1, '종신' -> 기대수명. input_adapter._pension_incomes
    가 PensionIncome.end_age 를 만드는 식과 같다.
    """
    text = _text(period)
    if text is None:
        return None
    if text == LIFETIME_PERIOD:
        # 종신은 기대수명까지로 본다. 기대수명을 모르면 나눌 수가 없다.
        return end_age
    matched = _PERIOD_YEARS.fullmatch(text)
    if matched is None:
        return None
    return pension_start_age + int(matched.group(1)) - 1


def payout_months(
    period: object,
    *,
    pension_start_age: int,
    current_age: int,
    end_age: int | None,
) -> int | None:
    """모듈이 실제로 지급하는 개월 수. 창이 비면 None.

    양끝 포함이고(cashflow 의 `start_age <= age <= end_age`), 현재 나이 이전은
    세지 않는다(cashflow 는 현재 나이부터만 연도를 돈다).
    """
    last_age = payout_end_age(
        period, pension_start_age=pension_start_age, end_age=end_age
    )
    if last_age is None:
        return None
    first_age = max(pension_start_age, current_age)
    months = (last_age - first_age + 1) * 12
    return months if months > 0 else None


def derive_expected_amount(
    row: Mapping,
    *,
    current_age: int | None,
    end_age: int | None,
    default_annual_rate: float | None,
) -> int | None:
    """적립액·월 납입액에서 예상 월 수령액을 만든다. 만들 수 없으면 None.

    None 을 돌려주는 경우(= 숫자를 지어내지 않는 경우):
    - 현재 나이나 개시 나이가 없다/유효하지 않다
    - 수령 기간이 없거나 해석할 수 없다 ('종신'인데 기대수명이 없는 경우 포함)
    - 남은 수령 창이 없다 (이미 다 받은 연금)
    - 적립액과 월 납입액이 둘 다 없거나 0 이다
    - 기대수익률이 적혀 있는데 읽을 수 없거나 상한을 넘는다
    - 기대수익률이 비어 있고 정책 기본값도 없다
    이때 어댑터의 기존 issue 가 그대로 남아 사용자가 직접 입력하게 된다.
    """
    if not _is_int(current_age):
        return None
    start_age = row.get("pension_start_age")
    if not _is_int(start_age):
        return None
    if end_age is not None and not _is_int(end_age):
        end_age = None

    months = payout_months(
        row.get("pension_period"),
        pension_start_age=start_age,
        current_age=current_age,
        end_age=end_age,
    )
    if months is None or months <= 0:
        return None

    balance = _amount(row.get("current_value"))
    monthly = _amount(row.get("monthly_savings"))
    if balance is None or monthly is None:
        return None
    if balance <= 0 and monthly <= 0:
        # 적어 둔 돈이 없다. 0원짜리 연금을 만들어 통과시키면 사용자는 그 연금이
        # 계산에 들어간 줄 안다.
        return None

    written = row.get("expected_return_rate")
    if written is None:
        # 안 적은 칸이다 — 정책 기본값으로 계산하고, 그 사실은 어댑터가
        # pension_expected_amount_estimated 로 알린다.
        rate = rate_from_fraction(default_annual_rate)
    else:
        # 적혀 있는데 읽을 수 없으면 정책값으로 슬쩍 바꾸지 않는다. 사용자가 적은
        # 것과 다른 수익률로 나온 금액을 자기 숫자로 믿게 된다.
        rate = rate_from_percent(written)
    if rate is None:
        return None

    years = max(0, start_age - current_age)
    grown = balance * (1 + rate) ** years
    monthly_rate = rate / 12
    contributions = monthly * (
        (((1 + monthly_rate) ** (years * 12)) - 1) / monthly_rate
        if monthly_rate
        else years * 12
    )

    total = grown + contributions
    if not isfinite(total):
        return None
    amount = int(total / months)
    if amount > MAX_SAFE_AMOUNT:
        return None
    return amount
