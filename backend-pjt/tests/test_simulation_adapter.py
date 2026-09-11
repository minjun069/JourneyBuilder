"""저장 스냅샷 → 모듈 입력 변환 검증 (S15P21B109-136).

Django 를 import 하지 않는 순수 함수라 평범한 unittest 로 전부 돈다.
CI(test:backend)가 backend-pjt/tests 를 discover 하므로 여기 두면 게이트에 걸린다.

가장 중요한 것은 마지막 AdapterMatchesModuleTests 다 — BE 사전 점검이 모듈 규칙보다
넓지 않은지(정상 입력을 막지 않는지) 실제 모듈을 돌려 대조한다.
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import sys
import unittest


BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from simulations.adapter import (  # noqa: E402
    ASSET_TYPES,
    build_simulation_input,
)


def snapshot(**overrides) -> dict:
    """reports.snapshots.build_calculation_input_snapshot 과 같은 모양의 유효한 스냅샷."""
    base = {
        "basic_info": {
            "age": 40,
            "monthly_income": 5_000_000,
            "retirement_age": 60,
            "end_age": 85,
            "dependents": {"count": 2},
        },
        "financial_assets": [
            {
                "asset_type": "예금/적금",
                "name": "주거래 예금",
                "principal": 100_000_000,
                "current_value": 120_000_000,
                "monthly_savings": 750_000,
                "purpose": "노후준비",
            }
        ],
        "pension_assets": [
            {
                "type": "연금저축",
                "name": "연금저축펀드",
                "current_value": 30_000_000,
                "monthly_savings": 300_000,
                "expected_return_rate": 4,
                "subscription_date": "2020-01-01",
                "payment_period_years": 20,
                "receive_method": "연금형",
                "pension_start_age": 65,
                "pension_period": "20년",
                "expected_amount": 1_000_000,
            }
        ],
        "real_estate_assets": [{"type": "아파트", "current_value": 500_000_000}],
        "liabilities": [{"type": "담보대출", "balance": 100_000_000}],
        "incomes": [{"type": "근로소득", "amount": 5_000_000}],
        "expenses": [{"name": "식비", "amount": 800_000}],
        "goal_milestones": [{"phase": "after", "age": 70, "amount": 10_000_000}],
        "goal_periods": [
            {
                "phase": "before",
                "from_age": 40,
                "to_age": 59,
                "items": [{"name": "생활비", "amount": 3_000_000, "is_fixed": True}],
            },
            {
                "phase": "after",
                "from_age": 60,
                "to_age": 85,
                "items": [{"name": "생활비", "amount": 2_000_000, "is_fixed": True}],
            },
        ],
    }
    base.update(overrides)
    return base


def codes(result) -> list[str]:
    return [issue.code for issue in result.issues]


class ValidSnapshotTests(unittest.TestCase):
    def test_valid_snapshot_produces_no_issues(self) -> None:
        self.assertEqual(build_simulation_input(snapshot()).issues, ())

    def test_only_module_readable_keys_are_passed_through(self) -> None:
        data = build_simulation_input(snapshot()).saved_data
        self.assertEqual(
            set(data),
            {
                "basic_info",
                "financial_assets",
                "pension_assets",
                "goal_periods",
                "real_estate_assets",
                "liabilities",
                "goal_milestones",
            },
        )
        # incomes·expenses 는 모듈이 읽지 않으므로 넘기지 않는다.
        self.assertNotIn("incomes", data)
        self.assertNotIn("expenses", data)
        self.assertEqual(set(data["basic_info"]), {"age", "retirement_age", "end_age"})
        self.assertEqual(
            set(data["financial_assets"][0]),
            {"asset_type", "current_value", "monthly_savings"},
        )
        self.assertEqual(
            set(data["pension_assets"][0]),
            {"receive_method", "pension_start_age", "pension_period", "expected_amount"},
        )
        self.assertEqual(set(data["goal_periods"][0]["items"][0]), {"amount"})

    def test_presence_only_sections_keep_row_count_but_drop_contents(self) -> None:
        data = build_simulation_input(snapshot()).saved_data
        for section in ("real_estate_assets", "liabilities", "goal_milestones"):
            self.assertEqual(data[section], [{}], section)

    def test_input_snapshot_is_never_mutated(self) -> None:
        source = snapshot()
        untouched = deepcopy(source)
        build_simulation_input(source)
        self.assertEqual(source, untouched)


class BasicInfoTests(unittest.TestCase):
    def test_missing_basic_info_is_reported_without_inventing_defaults(self) -> None:
        result = build_simulation_input(snapshot(basic_info=None))
        self.assertIn("basic_info_missing", codes(result))

    def test_null_ages_are_reported_per_field(self) -> None:
        result = build_simulation_input(
            snapshot(basic_info={"age": None, "retirement_age": None, "end_age": None})
        )
        self.assertEqual(codes(result).count("age_missing"), 3)

    def test_age_out_of_range_is_reported(self) -> None:
        result = build_simulation_input(
            snapshot(basic_info={"age": 17, "retirement_age": 60, "end_age": 85})
        )
        self.assertIn("age_out_of_range", codes(result))

    def test_end_age_below_retirement_age_is_reported(self) -> None:
        result = build_simulation_input(
            snapshot(basic_info={"age": 40, "retirement_age": 60, "end_age": 55})
        )
        self.assertIn("end_age_too_small", codes(result))


class FinancialAssetTests(unittest.TestCase):
    def _with_asset(self, **changes):
        row = {
            "asset_type": "예금/적금",
            "current_value": 1_000_000,
            "monthly_savings": 0,
        }
        row.update(changes)
        return build_simulation_input(snapshot(financial_assets=[row]))

    def test_all_five_supported_types_pass(self) -> None:
        for asset_type in ASSET_TYPES:
            with self.subTest(asset_type=asset_type):
                self.assertEqual(self._with_asset(asset_type=asset_type).issues, ())

    def test_blank_asset_type_is_missing_input_not_an_excluded_asset(self) -> None:
        # 프론트에서 '기타' 를 고르면 저장값이 비워지고 직접 입력 칸이 뜬다.
        # 아무것도 적지 않은 상태는 "5종이 아닌 자산" 이라는 판단조차 없는 미입력이다.
        for blank in (None, "", "   "):
            with self.subTest(value=blank):
                result = self._with_asset(asset_type=blank)
                self.assertIn("asset_type_missing", codes(result))

    def test_free_text_asset_type_is_blocked_for_now(self) -> None:
        result = self._with_asset(asset_type="전세보증금")
        self.assertIn("asset_type_unsupported", codes(result))
        issue = next(i for i in result.issues if i.code == "asset_type_unsupported")
        self.assertEqual(issue.hint, {"allowed": list(ASSET_TYPES)})

    def test_null_amounts_are_reported_and_not_coerced_to_zero(self) -> None:
        result = self._with_asset(current_value=None, monthly_savings=None)
        self.assertEqual(codes(result).count("amount_missing"), 2)
        # 월 적립액은 폼에서 선택 입력이라 가장 흔하다. 0 을 넣으라는 안내가 붙어야 한다.
        savings = next(
            i for i in result.issues if i.field.endswith(".monthly_savings")
        )
        self.assertIn("0을 입력", savings.message)

    def test_negative_and_non_integer_amounts_are_reported(self) -> None:
        self.assertIn("amount_invalid", codes(self._with_asset(current_value=-1)))
        self.assertIn("amount_invalid", codes(self._with_asset(current_value="100")))
        # bool 은 int 서브클래스지만 금액이 아니다 (모듈과 같은 판정).
        self.assertIn("amount_invalid", codes(self._with_asset(current_value=True)))

    def test_empty_section_without_confirmation_is_reported(self) -> None:
        result = build_simulation_input(snapshot(financial_assets=[]))
        self.assertIn("financial_assets_empty", codes(result))

    def test_confirmed_empty_section_is_accepted(self) -> None:
        result = build_simulation_input(
            snapshot(financial_assets=[]),
            confirmed_empty_sections=("financial_assets",),
        )
        self.assertNotIn("financial_assets_empty", codes(result))
        self.assertEqual(result.confirmed_empty_sections, ("financial_assets",))


class PensionAssetTests(unittest.TestCase):
    def _with_pension(self, **changes):
        row = {
            "receive_method": "연금형",
            "pension_start_age": 65,
            "pension_period": "20년",
            "expected_amount": 1_000_000,
        }
        row.update(changes)
        return build_simulation_input(snapshot(pension_assets=[row]))

    def test_lump_sum_is_returned_to_the_user_not_silently_dropped(self) -> None:
        result = self._with_pension(receive_method="일시금")
        self.assertIn("pension_lump_sum_unsupported", codes(result))

    def test_blank_receive_method_is_reported(self) -> None:
        self.assertIn(
            "pension_receive_method_missing", codes(self._with_pension(receive_method=None))
        )

    def test_pension_period_grammar_boundaries(self) -> None:
        # 모듈은 정규식 [1-9][0-9]{0,2}년 을 통과해도 값이 MAX_AGE(120)를 넘으면 거부한다.
        accepted = ("종신", "1년", "10년", "120년")
        rejected = ("기타", "10", "10 년", "0년", "01년", "121년", "999년", "1000년", "10years")
        for period in accepted:
            with self.subTest(period=period, expect="accept"):
                self.assertNotIn(
                    "pension_period_unsupported", codes(self._with_pension(pension_period=period))
                )
        for period in rejected:
            with self.subTest(period=period, expect="reject"):
                self.assertIn(
                    "pension_period_unsupported", codes(self._with_pension(pension_period=period))
                )

    def test_blank_pension_period_is_missing_not_unsupported(self) -> None:
        result = self._with_pension(pension_period=None)
        self.assertIn("pension_period_missing", codes(result))
        self.assertNotIn("pension_period_unsupported", codes(result))

    def test_out_of_horizon_pension_is_not_an_issue(self) -> None:
        # 모듈이 notice 로 처리하는 사안이다. BE 가 막으면 모듈보다 넓어진다.
        self.assertEqual(self._with_pension(pension_start_age=99).issues, ())


class ExpensePeriodTests(unittest.TestCase):
    def _with_periods(self, periods, **basic):
        info = {"age": 40, "retirement_age": 60, "end_age": 85}
        info.update(basic)
        return build_simulation_input(snapshot(basic_info=info, goal_periods=periods))

    @staticmethod
    def _after(from_age, to_age, amount=2_000_000):
        return {
            "phase": "after",
            "from_age": from_age,
            "to_age": to_age,
            "items": [{"name": "생활비", "amount": amount, "is_fixed": True}],
        }

    def test_missing_after_period_is_reported(self) -> None:
        before = {"phase": "before", "from_age": 40, "to_age": 59, "items": [{"amount": 1}]}
        self.assertIn("expense_periods_missing", codes(self._with_periods([before])))

    def test_gap_reports_the_exact_missing_years(self) -> None:
        result = self._with_periods([self._after(60, 74), self._after(80, 85)])
        self.assertIn("expense_periods_gap", codes(result))
        issue = next(i for i in result.issues if i.code == "expense_periods_gap")
        self.assertEqual(issue.hint, {"missing": [{"from_age": 75, "to_age": 79}]})

    def test_multiple_gaps_are_grouped_into_ranges(self) -> None:
        result = self._with_periods(
            [self._after(60, 64), self._after(70, 74), self._after(80, 85)]
        )
        issue = next(i for i in result.issues if i.code == "expense_periods_gap")
        self.assertEqual(
            issue.hint,
            {"missing": [{"from_age": 65, "to_age": 69}, {"from_age": 75, "to_age": 79}]},
        )

    def test_overlap_is_reported_with_the_other_row(self) -> None:
        result = self._with_periods([self._after(60, 75), self._after(75, 85)])
        self.assertIn("expense_periods_overlap", codes(result))
        issue = next(i for i in result.issues if i.code == "expense_periods_overlap")
        self.assertEqual(issue.hint, {"overlaps_with": 0})

    def test_reversed_range_is_reported(self) -> None:
        result = self._with_periods([self._after(85, 60)])
        self.assertIn("expense_period_invalid_range", codes(result))

    def test_empty_items_are_reported(self) -> None:
        period = {"phase": "after", "from_age": 60, "to_age": 85, "items": []}
        self.assertIn("expense_period_items_missing", codes(self._with_periods([period])))

    def test_null_item_amount_is_reported(self) -> None:
        period = {
            "phase": "after",
            "from_age": 60,
            "to_age": 85,
            "items": [{"amount": None}],
        }
        self.assertIn("amount_missing", codes(self._with_periods([period])))

    def test_coverage_starts_at_current_age_when_already_retired(self) -> None:
        # 은퇴 나이가 현재 나이보다 이르면 지금부터 채워야 한다 (모듈과 같은 규칙).
        result = self._with_periods(
            [self._after(65, 85)], age=62, retirement_age=60, end_age=85
        )
        issue = next(i for i in result.issues if i.code == "expense_periods_gap")
        self.assertEqual(issue.hint, {"missing": [{"from_age": 62, "to_age": 64}]})

    def test_broken_ranges_suppress_gap_analysis(self) -> None:
        # 구간 값 자체가 깨졌으면 공백 판정은 의미 없다. 먼저 고칠 것만 안내한다.
        result = self._with_periods([self._after(None, 85)])
        self.assertIn("age_missing", codes(result))
        self.assertNotIn("expense_periods_gap", codes(result))


class IssueCollectionTests(unittest.TestCase):
    def test_all_problems_are_collected_not_just_the_first(self) -> None:
        """모듈은 첫 오류에서 멈춘다. 여기서 전부 모으는 것이 이 어댑터의 존재 이유다."""
        result = build_simulation_input(
            snapshot(
                basic_info={"age": 40, "retirement_age": 60, "end_age": 85},
                financial_assets=[
                    {"asset_type": "전세보증금", "current_value": None, "monthly_savings": 1}
                ],
                pension_assets=[
                    {
                        "receive_method": "일시금",
                        "pension_start_age": 65,
                        "pension_period": "기타",
                        "expected_amount": 1,
                    }
                ],
            )
        )
        self.assertEqual(
            set(codes(result)),
            {
                "asset_type_unsupported",
                "amount_missing",
                "pension_lump_sum_unsupported",
                "pension_period_unsupported",
            },
        )

    def test_every_issue_carries_a_field_path_and_message(self) -> None:
        result = build_simulation_input(snapshot(basic_info=None, financial_assets=[]))
        self.assertTrue(result.issues)
        for issue in result.issues:
            self.assertTrue(issue.code)
            self.assertTrue(issue.field)
            self.assertTrue(issue.message)


class AdapterMatchesModuleTests(unittest.TestCase):
    """어댑터가 통과시킨 입력을 모듈이 실제로 받아들이는지 대조한다.

    BE 사전 점검이 모듈 규칙보다 **넓으면** 정상 사용자가 막힌다. **좁으면** 모듈이
    잡고 뷰가 ANALYSIS_REJECTED 를 낸다. 둘 다 이 테스트로 드러난다.
    """

    ASSUMPTIONS = {
        "annual_return_mean": 0.04,
        "annual_return_volatility": 0.1,
        "annual_inflation_rate": 0.02,
        "num_paths": 2,
        "seed": 42,
    }

    def _run_module(self, result):
        from retirement_simulation import (  # noqa: PLC0415
            analysis_to_dict,
            analyze_retirement,
        )

        return analysis_to_dict(
            analyze_retirement(
                result.saved_data,
                self.ASSUMPTIONS,
                confirmed_empty_sections=result.confirmed_empty_sections,
            )
        )

    def test_clean_snapshot_runs_through_the_module(self) -> None:
        result = build_simulation_input(snapshot())
        self.assertEqual(result.issues, ())
        payload = self._run_module(result)
        self.assertEqual(payload["contract_version"], "retirement-analysis-v1")

    def test_module_notices_survive_the_presence_only_substitution(self) -> None:
        """부동산·부채·마일스톤을 빈 dict 로 바꿔도 모듈 안내가 그대로 나와야 한다."""
        payload = self._run_module(build_simulation_input(snapshot()))
        notice_codes = {notice["code"] for notice in payload["notices"]}
        self.assertIn("real_estate_not_modeled", notice_codes)
        self.assertIn("liabilities_not_modeled", notice_codes)
        self.assertIn("milestones_not_modeled", notice_codes)

    def test_out_of_horizon_pension_reaches_the_module_as_a_notice(self) -> None:
        row = {
            "receive_method": "연금형",
            "pension_start_age": 99,
            "pension_period": "10년",
            "expected_amount": 1_000_000,
        }
        result = build_simulation_input(snapshot(pension_assets=[row]))
        self.assertEqual(result.issues, ())
        payload = self._run_module(result)
        self.assertIn(
            "pension_outside_horizon", {n["code"] for n in payload["notices"]}
        )

    def test_every_rejected_case_is_also_rejected_by_the_module(self) -> None:
        """어댑터가 막은 입력은 모듈도 막아야 한다. 아니면 우리가 더 엄격한 것이다."""
        from retirement_simulation import (  # noqa: PLC0415
            RetirementAnalysisError,
            analyze_retirement,
        )

        cases = {
            "free_text_asset_type": snapshot(
                financial_assets=[
                    {
                        "asset_type": "전세보증금",
                        "current_value": 1,
                        "monthly_savings": 0,
                    }
                ]
            ),
            "lump_sum_pension": snapshot(
                pension_assets=[
                    {
                        "receive_method": "일시금",
                        "pension_start_age": 65,
                        "pension_period": "20년",
                        "expected_amount": 1,
                    }
                ]
            ),
            "expense_gap": snapshot(
                goal_periods=[
                    {
                        "phase": "after",
                        "from_age": 60,
                        "to_age": 74,
                        "items": [{"amount": 1}],
                    }
                ]
            ),
            "null_amount": snapshot(
                financial_assets=[
                    {
                        "asset_type": "예금/적금",
                        "current_value": None,
                        "monthly_savings": 0,
                    }
                ]
            ),
        }
        for name, source in cases.items():
            with self.subTest(case=name):
                result = build_simulation_input(source)
                self.assertTrue(result.issues, f"{name}: 어댑터가 통과시켰다")
                with self.assertRaises(RetirementAnalysisError):
                    analyze_retirement(
                        result.saved_data,
                        self.ASSUMPTIONS,
                        confirmed_empty_sections=result.confirmed_empty_sections,
                    )


if __name__ == "__main__":
    unittest.main()
