"""적립액 → 예상 월 수령액 환산 검증.

순수 산수는 Django 없이 평범한 unittest 로 돈다. 마지막
PayoutConservationTests 만 어댑터와 설치된 모듈을 함께 부른다(메서드 안에서
import 한다 — test_simulation_adapter.py 와 같은 방식).
CI(test:backend)가 backend-pjt/tests 를 discover 하므로 여기 두면 게이트에 걸린다.

여기서 지키는 것은 세 가지다.
1. 계산이 맞는가 (손계산과 대조).
2. **계산할 수 없을 때 숫자를 지어내지 않는가.** 지어내면 사용자는 자기가 적지
   않은 금액으로 나온 성공확률을 자기 계획으로 믿는다.
3. **나눈 자금이 모듈이 지급하는 총액과 같은가** (PayoutConservationTests).
   이 셋째 항목이 없어서 세 가지 산수 결함이 한꺼번에 통과했었다 —
   퍼센트 1.00 을 연 100% 로 읽던 휴리스틱, 종신을 1년 적게 나누던 창,
   이미 개시된 연금을 전체 기간으로 나누던 분모.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
import sys
import unittest


BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from simulations.pension import (  # noqa: E402
    MAX_ANNUAL_RATE,
    derive_expected_amount,
    payout_end_age,
    payout_months,
    rate_from_fraction,
    rate_from_percent,
)

POLICY_RATE = 0.04  # contract-fixtures/simulation/v1/policy.json 의 annual_return_mean


def row(**changes) -> dict:
    """실제 IRP 행의 모양. 배포 세션(38세/개시 60/20년)과 같은 값이다."""
    base = {
        "current_value": 45_000_000,
        "monthly_savings": 300_000,
        "expected_return_rate": Decimal("3.50"),
        "pension_start_age": 60,
        "pension_period": "20년",
    }
    base.update(changes)
    return base


def derive(source: dict, *, current_age=38, end_age=90, rate=POLICY_RATE):
    return derive_expected_amount(
        source, current_age=current_age, end_age=end_age, default_annual_rate=rate
    )


class RateUnitTests(unittest.TestCase):
    """행의 칸은 **언제나 퍼센트**(3.50), 정책값은 **언제나 소수**(0.04)다.

    한 함수에 '1 보다 크면 퍼센트' 휴리스틱으로 둘을 섞으면 1.00 이 연 100% 가 된다.
    그래서 두 단위는 함수부터 나눠져 있다.
    """

    def test_row_percentages_divide_by_one_hundred(self) -> None:
        self.assertAlmostEqual(rate_from_percent(Decimal("3.50")), 0.035)
        self.assertAlmostEqual(rate_from_percent(3.5), 0.035)
        self.assertEqual(rate_from_percent(0), 0.0)

    def test_the_one_percent_boundary_is_not_a_cliff(self) -> None:
        """1.00 은 연 1% 다. 100% 로 읽으면 1.01 과 600만 배가 벌어졌었다."""
        self.assertAlmostEqual(rate_from_percent(Decimal("1.00")), 0.01)
        self.assertAlmostEqual(rate_from_percent(Decimal("0.50")), 0.005)
        self.assertAlmostEqual(rate_from_percent(Decimal("1.01")), 0.0101)
        self.assertAlmostEqual(rate_from_percent(Decimal("0.99")), 0.0099)

    def test_policy_fractions_are_read_as_written(self) -> None:
        self.assertAlmostEqual(rate_from_fraction(0.04), 0.04)
        self.assertEqual(rate_from_fraction(0), 0.0)

    def test_unreadable_rates_are_none_not_zero(self) -> None:
        # 0 으로 떨어뜨리면 수익률 없이 계산한 값이 '수익률 반영' 인 척 나온다.
        for value in (None, "4%", True, float("nan"), -1):
            with self.subTest(value=value):
                self.assertIsNone(rate_from_percent(value))
                self.assertIsNone(rate_from_fraction(value))

    def test_absurd_rates_are_rejected_by_the_sanity_bound(self) -> None:
        """DecimalField(max_digits=5) 는 999.99(%) 를 담을 수 있다. 연 1000% 는 없다."""
        self.assertIsNone(rate_from_percent(Decimal("999.99")))
        self.assertIsNone(rate_from_fraction(1.5))
        self.assertAlmostEqual(rate_from_percent(100), MAX_ANNUAL_RATE)
        self.assertAlmostEqual(rate_from_fraction(MAX_ANNUAL_RATE), MAX_ANNUAL_RATE)

    def test_the_row_column_is_percent_even_below_one(self) -> None:
        """0.035 가 적힌 행은 '3.5%' 가 아니라 '0.035%' 다 — 칸의 단위가 %다."""
        percent = derive(row(expected_return_rate=Decimal("3.50")))
        tiny = derive(row(expected_return_rate=Decimal("0.035")))
        no_growth = derive(row(expected_return_rate=Decimal("0.00")))
        self.assertGreater(percent, tiny)
        # 0.035% 는 사실상 무이자에 가깝다 — 3.5% 와 같은 값일 수 없다.
        self.assertLess(tiny - no_growth, (percent - no_growth) / 50)

    def test_row_rate_wins_over_the_policy_default(self) -> None:
        own = derive(row(expected_return_rate=Decimal("3.50")), rate=0.10)
        fallback = derive(row(expected_return_rate=None), rate=0.10)
        self.assertNotEqual(own, fallback)
        self.assertEqual(own, derive(row(expected_return_rate=Decimal("3.50"))))

    def test_policy_default_is_used_when_the_row_has_no_rate(self) -> None:
        self.assertEqual(
            derive(row(expected_return_rate=None), rate=0.0),
            # 수익률 0 이면 적립액 + 납입 원금뿐이다.
            (45_000_000 + 300_000 * 22 * 12) // (20 * 12),
        )

    def test_without_any_rate_nothing_is_derived(self) -> None:
        """정책값을 넘기지 않은 호출(어댑터 직접 사용)은 추측하지 않는다."""
        self.assertIsNone(derive(row(expected_return_rate=None), rate=None))

    def test_an_unreadable_written_rate_does_not_fall_back_to_policy(self) -> None:
        """비어 있는 칸과 못 읽는 칸은 다르다. 후자는 사용자가 적은 값이 있다.

        정책값으로 슬쩍 갈아끼우면 사용자는 자기가 적은 수익률로 나온 금액이라고
        믿는다. 비어 있을 때만 정책값을 쓴다.
        """
        for written in ("4%", -1, Decimal("999.99"), float("inf")):
            with self.subTest(written=written):
                self.assertIsNone(derive(row(expected_return_rate=written)))


class PayoutWindowTests(unittest.TestCase):
    """창은 **양끝 포함**이고 현재 나이 이전은 세지 않는다.

    근거는 packages/retirement_simulation 쪽이다 — cashflow 는
    `start_age <= age <= end_age` 인 해마다 지급하고 현재 나이부터만 연도를 돌며,
    input_adapter 는 'N년' 을 `start + N - 1` 로 끝낸다.
    """

    def test_n_year_period_ends_on_the_inclusive_last_age(self) -> None:
        self.assertEqual(
            payout_end_age("20년", pension_start_age=60, end_age=90), 79
        )

    def test_lifetime_ends_at_life_expectancy(self) -> None:
        self.assertEqual(
            payout_end_age("종신", pension_start_age=65, end_age=90), 90
        )

    def test_n_year_period_does_not_depend_on_life_expectancy(self) -> None:
        self.assertEqual(
            payout_months(
                "20년", pension_start_age=60, current_age=38, end_age=None
            ),
            240,
        )

    def test_lifetime_counts_both_end_years(self) -> None:
        """개시 65, 기대수명 90 은 65..90 = 26년이다. 25년이 아니다."""
        self.assertEqual(
            payout_months("종신", pension_start_age=65, current_age=45, end_age=90),
            26 * 12,
        )

    def test_an_already_started_pension_only_counts_remaining_years(self) -> None:
        """70세에 보는 '개시 65 / 20년' 은 70..84 만 남았다."""
        self.assertEqual(
            payout_months("20년", pension_start_age=65, current_age=70, end_age=90),
            15 * 12,
        )

    def test_the_first_payout_year_is_counted_when_it_is_this_year(self) -> None:
        self.assertEqual(
            payout_months("10년", pension_start_age=60, current_age=60, end_age=90),
            120,
        )

    def test_lifetime_without_life_expectancy_is_unknown(self) -> None:
        self.assertIsNone(
            payout_months("종신", pension_start_age=65, current_age=45, end_age=None)
        )

    def test_lifetime_starting_after_life_expectancy_is_unknown(self) -> None:
        self.assertIsNone(
            payout_months("종신", pension_start_age=95, current_age=45, end_age=90)
        )

    def test_a_fully_paid_out_pension_has_no_window_left(self) -> None:
        """개시 65 / 10년 을 80세에 보면 남은 회차가 없다 — 숫자를 만들지 않는다."""
        self.assertIsNone(
            payout_months("10년", pension_start_age=65, current_age=80, end_age=90)
        )

    def test_unparseable_periods_are_unknown(self) -> None:
        for period in (None, "", "기타", "0년", "10", "10 년", "종신형"):
            with self.subTest(period=period):
                self.assertIsNone(
                    payout_months(
                        period, pension_start_age=60, current_age=38, end_age=90
                    )
                )


class DerivedAmountTests(unittest.TestCase):
    def test_matches_the_hand_calculation_for_the_real_irp_row(self) -> None:
        """38세/개시 60/20년/4,500만/월 30만/3.5% → 손계산 약 89만원."""
        amount = derive(row())
        self.assertGreater(amount, 880_000)
        self.assertLess(amount, 910_000)

    def test_matches_the_hand_calculation_for_the_real_savings_row(self) -> None:
        """38세/개시 60/20년/3,200만/월 40만/4.0% → 손계산 약 102만원."""
        amount = derive(
            row(
                current_value=32_000_000,
                monthly_savings=400_000,
                expected_return_rate=Decimal("4.00"),
            )
        )
        self.assertGreater(amount, 1_005_000)
        self.assertLess(amount, 1_035_000)

    def test_zero_rate_is_plain_division(self) -> None:
        # 이미 개시 나이라 적립 기간이 없다. 1,200만 / 120개월(60..69) = 10만.
        self.assertEqual(
            derive(
                row(
                    current_value=12_000_000,
                    monthly_savings=0,
                    expected_return_rate=0,
                    pension_start_age=60,
                    pension_period="10년",
                ),
                current_age=60,
            ),
            100_000,
        )

    def test_lifetime_window_includes_the_life_expectancy_year(self) -> None:
        """개시 60, 기대수명 90 은 60..90 = 31년이다. '30년' 이 아니라 '31년' 과 같다."""
        lifetime = derive(
            row(expected_return_rate=0, pension_period="종신"), end_age=90
        )
        self.assertEqual(
            lifetime, derive(row(expected_return_rate=0, pension_period="31년"))
        )
        self.assertNotEqual(
            lifetime, derive(row(expected_return_rate=0, pension_period="30년"))
        )

    def test_longer_life_expectancy_lowers_a_lifetime_payout(self) -> None:
        self.assertLess(
            derive(row(pension_period="종신"), end_age=100),
            derive(row(pension_period="종신"), end_age=85),
        )

    def test_already_started_pension_divides_by_the_remaining_window(self) -> None:
        """개시 나이가 지났으면 적립 기간은 0이고, 분모도 **남은** 회차뿐이다.

        70세에 보는 '개시 60 / 20년' 은 70..79 만 지급된다. 240개월로 나누면
        적어 둔 돈의 절반이 계획에 영영 도달하지 않는다.
        """
        self.assertEqual(
            derive(row(expected_return_rate=0), current_age=70),
            45_000_000 // 120,
        )

    def test_a_pension_whose_payments_are_over_is_not_derived(self) -> None:
        self.assertIsNone(derive(row(expected_return_rate=0), current_age=85))

    def test_only_a_balance_or_only_a_contribution_is_enough(self) -> None:
        self.assertIsNotNone(derive(row(monthly_savings=None)))
        self.assertIsNotNone(derive(row(current_value=None)))

    def test_a_period_past_the_horizon_is_not_shortened_here(self) -> None:
        """개시 80 / 20년 / 기대수명 90 은 80..99 로 나눈다 — 실제로 받는 돈이다.

        지평(90) 밖 회차를 빼는 일은 모듈의 몫이고(pension_horizon_limited),
        사용자가 직접 적은 금액에도 똑같이 적용된다. 여기서 11년으로 나누면
        받지도 않을 큰 월 수령액이 만들어진다.
        """
        self.assertEqual(
            derive(
                row(
                    current_value=24_000_000,
                    monthly_savings=0,
                    expected_return_rate=0,
                    pension_start_age=80,
                    pension_period="20년",
                ),
                current_age=80,
                end_age=90,
            ),
            24_000_000 // 240,
        )


class NotDerivableTests(unittest.TestCase):
    """계산 근거가 없으면 None 이어야 한다. 어댑터는 그때 기존 안내를 남긴다."""

    def test_missing_start_age(self) -> None:
        self.assertIsNone(derive(row(pension_start_age=None)))

    def test_invalid_start_age_type(self) -> None:
        self.assertIsNone(derive(row(pension_start_age="60")))

    def test_missing_current_age(self) -> None:
        self.assertIsNone(derive(row(), current_age=None))

    def test_missing_or_unparseable_period(self) -> None:
        for period in (None, "", "기타"):
            with self.subTest(period=period):
                self.assertIsNone(derive(row(pension_period=period)))

    def test_lifetime_without_life_expectancy(self) -> None:
        self.assertIsNone(derive(row(pension_period="종신"), end_age=None))

    def test_no_money_recorded_at_all(self) -> None:
        for balance, monthly in ((None, None), (0, 0), (0, None), (None, 0)):
            with self.subTest(balance=balance, monthly=monthly):
                self.assertIsNone(
                    derive(row(current_value=balance, monthly_savings=monthly))
                )

    def test_unreadable_amounts(self) -> None:
        for changes in (
            {"current_value": "4500만"},
            {"monthly_savings": -1},
            {"current_value": -45_000_000},
        ):
            with self.subTest(**changes):
                self.assertIsNone(derive(row(**changes)))

    def test_amounts_too_large_for_the_module_are_not_derived(self) -> None:
        """모듈은 9,007조를 넘는 수를 직렬화 단계에서 거부한다(unsafe_json_number).

        그런 값을 만들어 넘기면 고칠 수 있는 422 가 ANALYSIS_REJECTED 로 바뀐다.
        """
        self.assertIsNone(derive(row(current_value=10**21)))


# 환산이 실제로 지급되는지 — 어댑터와 설치된 모듈을 함께 돌린다.
_MATRIX = [
    # (이름, 현재나이, 은퇴나이, 기대수명, 개시나이, 기간, 적립액, 월납, 행 수익률)
    ("N년 · 개시가 아직 멀다 · 행 수익률", 38, 60, 90, 60, "20년",
     45_000_000, 300_000, Decimal("3.50")),
    ("N년 · 올해 개시", 60, 60, 90, 60, "20년", 45_000_000, 0, Decimal("3.50")),
    ("N년 · 이미 개시됨", 70, 60, 90, 65, "20년", 120_000_000, 0, Decimal("0.00")),
    ("N년 · 정책 기본 수익률", 38, 60, 90, 60, "20년", 32_000_000, 400_000, None),
    ("N년 · 수익률 0", 45, 60, 95, 65, "25년", 50_000_000, 300_000, Decimal("0.00")),
    ("N년 · 1.00% 경계", 45, 60, 90, 65, "20년", 50_000_000, 300_000, Decimal("1.00")),
    ("종신 · 개시가 아직 멀다", 45, 60, 90, 65, "종신",
     50_000_000, 300_000, Decimal("4.00")),
    ("종신 · 올해 개시", 65, 60, 90, 65, "종신", 200_000_000, 0, Decimal("2.00")),
    ("종신 · 이미 개시됨", 70, 60, 90, 65, "종신", 150_000_000, 0, Decimal("0.00")),
    ("종신 · 정책 기본 수익률", 50, 60, 88, 62, "종신", 60_000_000, 200_000, None),
]


def _fund(*, current_age, start_age, balance, monthly, rate) -> float:
    """환산이 나눠야 하는 자금. 모듈 코드를 부르지 않고 여기서 따로 계산한다."""
    years = max(0, start_age - current_age)
    grown = balance * (1 + rate) ** years
    monthly_rate = rate / 12
    contributions = monthly * (
        (((1 + monthly_rate) ** (years * 12)) - 1) / monthly_rate
        if monthly_rate
        else years * 12
    )
    return grown + contributions


class PayoutConservationTests(unittest.TestCase):
    """**나눈 자금 == 모듈이 지급하는 총액.**

    한 행씩 어댑터에 태워 모듈 입력을 만들고, 그 입력으로 실제 현금흐름을 돌려
    연금으로 나간 총액을 센다. 지급 개월 수는 내 공식이 아니라 모듈이 만든
    PensionIncome 과 profile.current_age 에서 읽는다 — 공식이 틀렸다면 여기서
    갈라져야 한다.

    이 한 성질이 세 결함(퍼센트 100배, 종신 1년 초과 지급, 이미 개시된 연금의
    분모)을 모두 잡는다.
    """

    ASSUMPTIONS = {
        "annual_return_mean": POLICY_RATE,
        "annual_return_volatility": 0.1,
        "annual_inflation_rate": 0.02,
        "num_paths": 2,
        "seed": 42,
    }

    @staticmethod
    def _snapshot(*, current_age, retirement_age, end_age, pension) -> dict:
        return {
            "basic_info": {
                "age": current_age,
                "monthly_income": 5_000_000,
                "retirement_age": retirement_age,
                "end_age": end_age,
                "dependents": {"count": 0},
            },
            "financial_assets": [
                {
                    "asset_type": "예금/적금",
                    "name": "주거래 예금",
                    "principal": 100_000_000,
                    "current_value": 100_000_000,
                    "monthly_savings": 0,
                    "purpose": "노후준비",
                }
            ],
            "pension_assets": [pension],
            "real_estate_assets": [],
            "liabilities": [],
            "goal_milestones": [],
            "goal_periods": [
                {
                    "phase": "after",
                    "from_age": max(retirement_age, current_age),
                    "to_age": end_age,
                    "items": [
                        {"name": "생활비", "amount": 2_000_000, "is_fixed": True}
                    ],
                }
            ],
        }

    def test_every_derived_won_is_actually_paid_out(self) -> None:
        from simulations.adapter import build_simulation_input  # noqa: PLC0415
        from retirement_simulation import (  # noqa: PLC0415
            calculate_cashflow,
            map_retirement_profile,
            map_simulation_assumptions,
        )

        for (
            name, current_age, retirement_age, end_age,
            start_age, period, balance, monthly, written_rate,
        ) in _MATRIX:
            with self.subTest(name=name):
                snapshot = self._snapshot(
                    current_age=current_age,
                    retirement_age=retirement_age,
                    end_age=end_age,
                    pension={
                        "type": "IRP",
                        "name": "적립형 연금",
                        "current_value": balance,
                        "monthly_savings": monthly,
                        "expected_return_rate": written_rate,
                        "subscription_date": "2020-01-01",
                        "payment_period_years": 20,
                        "receive_method": "연금형",
                        "pension_start_age": start_age,
                        "pension_period": period,
                        "expected_amount": None,
                    },
                )
                result = build_simulation_input(
                    snapshot, default_annual_rate=POLICY_RATE
                )
                self.assertEqual(
                    [(issue.code, issue.field) for issue in result.issues], []
                )
                amount = result.saved_data["pension_assets"][0]["expected_amount"]
                self.assertIsNotNone(amount)

                profile = map_retirement_profile(
                    result.saved_data,
                    confirmed_empty_sections=result.confirmed_empty_sections,
                ).profile
                (income,) = profile.pension_incomes
                # 모듈이 실제로 지급하는 개월 수. cashflow 는 현재 나이부터만 돌고
                # start_age <= age <= end_age 인 해마다 지급한다.
                engine_months = 12 * (
                    income.end_age - max(income.start_age, profile.current_age) + 1
                )

                flow = calculate_cashflow(
                    profile, map_simulation_assumptions(self.ASSUMPTIONS)
                )
                paid = sum(year.pension_income for year in flow.years)
                # 내가 센 개월 수가 현금흐름과 같은지 먼저 확인한다.
                self.assertEqual(paid, amount * engine_months)

                fund = _fund(
                    current_age=current_age,
                    start_age=start_age,
                    balance=balance,
                    monthly=monthly,
                    rate=(
                        POLICY_RATE if written_rate is None
                        else float(written_rate) / 100
                    ),
                )
                # 정수로 내림한 만큼(회차당 1원 미만)만 벌어져야 한다.
                self.assertLessEqual(
                    fund - paid, engine_months,
                    f"{name}: 자금 {fund:,.0f} 중 {fund - paid:,.0f} 원이 사라졌다",
                )
                self.assertGreaterEqual(
                    fund - paid, 0,
                    f"{name}: 자금 {fund:,.0f} 보다 {paid - fund:,.0f} 원을 더 지급했다",
                )


if __name__ == "__main__":
    unittest.main()
