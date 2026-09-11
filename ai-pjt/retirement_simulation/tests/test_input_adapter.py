"""Basic saved-input mapping contracts; all fixtures are synthetic and local."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import FrozenInstanceError
from decimal import Decimal
from itertools import product
from pathlib import Path
import random
import sys
from types import MappingProxyType
import unittest


AI_PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(AI_PROJECT_ROOT))

from retirement_simulation import (  # noqa: E402
    InputMappingError,
    InputMappingNotice,
    ModelValidationError,
    PensionIncome,
    ProfileMappingResult,
    RetirementExpensePeriod,
    SimulationAssumptions,
    WhatIfChanges,
    WhatIfScenario,
    calculate_cashflow,
    calculate_monte_carlo,
    calculate_what_if,
    map_retirement_profile,
    map_simulation_assumptions,
)


def saved_data() -> dict:
    """Fresh minimal saved representation of the documented 40/60/90 fixture."""
    return {
        "basic_info": {"age": 40, "retirement_age": 60, "end_age": 90},
        "financial_assets": [
            {"id": 1, "asset_type": "예금/적금", "principal": 90_000_000,
             "current_value": 100_000_000, "monthly_savings": 500_000},
            {"id": 2, "asset_type": "펀드/ETF", "principal": 18_000_000,
             "current_value": 20_000_000, "monthly_savings": 250_000},
        ],
        "pension_assets": [
            {"id": 1, "receive_method": "연금형", "pension_start_age": 65,
             "pension_period": "종신", "expected_amount": 1_000_000},
        ],
        "goal_periods": [
            {"id": 1, "phase": "after", "from_age": 60, "to_age": 74,
             "items": [{"id": 1, "amount": 1_500_000}, {"id": 2, "amount": 500_000}]},
            {"id": 2, "phase": "after", "from_age": 75, "to_age": 90,
             "items": [{"id": 3, "amount": 1_500_000}]},
        ],
        "real_estate_assets": [], "liabilities": [], "goal_milestones": [],
    }


def assumption_data(**changes) -> dict:
    return {
        "annual_return_mean": 0, "annual_return_volatility": 0,
        "annual_inflation_rate": 0, "num_paths": 2, "seed": 7,
    } | changes


class ProfileMappingTests(unittest.TestCase):
    def assert_mapping_error(self, data, code, path, **kwargs):
        original = deepcopy(data)
        with self.assertRaises(InputMappingError) as error:
            map_retirement_profile(data, **kwargs)
        self.assertEqual((error.exception.code, error.exception.field_name), (code, path))
        self.assertEqual(data, original)
        return error.exception

    def test_documented_profile_totals_and_schedules(self):
        result = map_retirement_profile(saved_data())
        profile = result.profile
        self.assertEqual((profile.current_age, profile.retirement_age, profile.end_age), (40, 60, 90))
        self.assertEqual((profile.liquid_assets, profile.monthly_savings), (120_000_000, 750_000))
        self.assertEqual(profile.retirement_expenses, (
            RetirementExpensePeriod(from_age=60, to_age=74, monthly_amount=2_000_000),
            RetirementExpensePeriod(from_age=75, to_age=90, monthly_amount=1_500_000),
        ))
        self.assertEqual(profile.pension_incomes, (
            PensionIncome(start_age=65, end_age=90, monthly_amount=1_000_000),
        ))
        self.assertEqual(result.mapping_version, "retirement-input-v1")

    def test_scope_notices_have_stable_codes_and_order(self):
        result = map_retirement_profile(saved_data())
        self.assertEqual([(n.code, n.field_name) for n in result.notices], [
            ("partial_financial_scope", "saved_data"),
            ("single_portfolio_assumption", "financial_assets"),
            ("pre_retirement_cashflow_not_recalculated", "financial_assets"),
            ("pension_account_not_modeled", "pension_assets"),
            ("pension_horizon_limited", "pension_assets[0]"),
        ])

    def test_explicit_zero_is_not_missing(self):
        data = saved_data()
        for row in data["financial_assets"]:
            row.update(current_value=0, monthly_savings=0)
        for period in data["goal_periods"]:
            for item in period["items"]:
                item["amount"] = 0
        data["pension_assets"][0]["expected_amount"] = 0
        profile = map_retirement_profile(data).profile
        self.assertEqual((profile.liquid_assets, profile.monthly_savings), (0, 0))
        self.assertTrue(all(p.monthly_amount == 0 for p in profile.retirement_expenses))
        self.assertEqual(profile.pension_incomes[0].monthly_amount, 0)

    def test_mapping_accepts_python_mapping_and_tuple_arrays(self):
        data = saved_data()
        data["basic_info"] = MappingProxyType(data["basic_info"])
        data["financial_assets"] = tuple(MappingProxyType(row) for row in data["financial_assets"])
        self.assertEqual(map_retirement_profile(MappingProxyType(data)), map_retirement_profile(saved_data()))

    def test_saved_data_is_not_mutated_or_retained(self):
        data = saved_data()
        data["goal_periods"].reverse()
        original = deepcopy(data)
        result = map_retirement_profile(data)
        self.assertEqual(data, original)
        data["financial_assets"][0]["current_value"] = 0
        data["goal_periods"][0]["items"].clear()
        data["pension_assets"][0]["expected_amount"] = 0
        self.assertEqual(result, map_retirement_profile(original))

    def test_extra_identity_fields_and_current_form_are_not_merged(self):
        data = saved_data()
        data["current_form"] = {"basic_info": {"age": 99}}
        data["customer_name"] = "PRIVATE_SYNTHETIC_NAME"
        data["basic_info"]["monthly_income"] = 100_000_000
        data["incomes"] = [{"amount": 100_000_000}]
        data["expenses"] = [{"amount": 0}]
        data["pension_assets"][0].update(current_value=100_000_000, monthly_savings=99_999_999,
                                        expected_return_rate="PRIVATE_UNUSED_RATE")
        result = map_retirement_profile(data)
        self.assertEqual(result, map_retirement_profile(saved_data()))
        self.assertNotIn("PRIVATE", repr(result))
        self.assertNotIn("120000000", repr(result))

    def test_complete_customer_info_wrapper_is_not_a_saved_snapshot(self):
        self.assert_mapping_error({"saved_data": saved_data(), "current_form": {}},
                                  "missing_field", "basic_info")

    def test_required_sections_are_not_defaulted(self):
        for name in saved_data():
            with self.subTest(section=name):
                data = saved_data()
                del data[name]
                self.assert_mapping_error(data, "missing_field", name)
                data[name] = None
                self.assert_mapping_error(data, "invalid_type", name)

    def test_root_arrays_and_rows_require_supported_structure(self):
        for value in (None, [], True, "PRIVATE_RAW"):
            self.assert_mapping_error(value, "invalid_type", "saved_data")
        for value in ({}, "PRIVATE_RAW", 0):
            data = saved_data()
            data["financial_assets"] = value
            self.assert_mapping_error(data, "invalid_type", "financial_assets")
        data = saved_data()
        data["financial_assets"] = [None]
        self.assert_mapping_error(data, "invalid_type", "financial_assets[0]")

    def test_financial_amounts_reject_null_bool_string_fraction_and_negative(self):
        for key in ("current_value", "monthly_savings"):
            for value in (None, True, "100만", 1.5, -1):
                with self.subTest(field=key, kind=type(value).__name__):
                    data = saved_data()
                    data["financial_assets"][0][key] = value
                    self.assert_mapping_error(data, "invalid_value", f"financial_assets[0].{key}")

    def test_missing_savings_does_not_become_zero(self):
        data = saved_data()
        del data["financial_assets"][0]["monthly_savings"]
        self.assert_mapping_error(data, "missing_field", "financial_assets[0].monthly_savings")

    def test_known_asset_types_and_outer_whitespace(self):
        expected = map_retirement_profile(saved_data())
        for kind in ("예금/적금", "주식", "펀드/ETF", "채권", "가상자산"):
            data = saved_data()
            data["financial_assets"][0]["asset_type"] = f" {kind} "
            self.assertEqual(map_retirement_profile(data), expected)

    def test_unmodeled_asset_classification_is_excluded_without_guessing(self):
        kinds = ("금", "기 타", "기타자산", "기타(직접입력)", "PRIVATE_ASSET", "전세보증금",
                 "보험 해약환급금", "회원권", "예금적금", "예금/ 적금", "주식형보험", "123")
        for kind in kinds:
            with self.subTest(kind=kind):
                data = saved_data()
                data["financial_assets"][0]["asset_type"] = kind
                original = deepcopy(data)
                result = map_retirement_profile(data)
                self.assertEqual((result.profile.liquid_assets, result.profile.monthly_savings), (20_000_000, 250_000))
                self.assertEqual([n for n in result.notices if n.code == "other_financial_asset_not_modeled"], [
                    InputMappingNotice(code="other_financial_asset_not_modeled", field_name="financial_assets[0]"),
                ])
                self.assertEqual(data, original)
                self.assertNotIn(kind, repr(result.notices))

    def test_blank_asset_classification_is_missing_not_unmodeled(self):
        for kind in (None, "", " ", "\t\n", "\u3000\u00a0"):
            with self.subTest(kind=kind):
                data = saved_data()
                data["financial_assets"][0]["asset_type"] = kind
                self.assert_mapping_error(data, "missing_value", "financial_assets[0].asset_type")

    def test_non_string_asset_classification_is_invalid_type_not_unmodeled(self):
        for kind in (True, False, 0, 1.5, Decimal("1"), [], {}, ["PRIVATE_ASSET"], {"name": "PRIVATE_ASSET"}):
            with self.subTest(kind=kind):
                data = saved_data()
                data["financial_assets"][0]["asset_type"] = kind
                error = self.assert_mapping_error(data, "invalid_type", "financial_assets[0].asset_type")
                self.assertNotIn("PRIVATE_ASSET", str(error))

    def test_other_assets_are_omitted_with_original_row_notices(self):
        data = saved_data()
        expected = map_retirement_profile(data)
        data["financial_assets"].insert(0, {
            "id": 91, "asset_type": "전세보증금", "current_value": 900_000_000,
            "monthly_savings": 99_000_000, "name": "PRIVATE_OTHER_ASSET",
        })
        data["financial_assets"].append({
            "id": 73, "asset_type": "기타", "current_value": 500_000_000, "monthly_savings": 50_000_000,
        })
        original = deepcopy(data)
        result = map_retirement_profile(data)
        self.assertEqual(result.profile, expected.profile)
        self.assertEqual(result.notices, (
            *expected.notices[:-1],
            InputMappingNotice(code="other_financial_asset_not_modeled", field_name="financial_assets[0]"),
            InputMappingNotice(code="other_financial_asset_not_modeled", field_name="financial_assets[3]"),
            expected.notices[-1],
        ))
        self.assertEqual(data, original)
        self.assertNotIn("PRIVATE_OTHER_ASSET", repr(result.notices))

    def test_other_asset_classification_accepts_outer_whitespace(self):
        for kind in ("기타", " 기타 ", "\t기타\n", " 전세보증금 ", "\u3000보험 해약환급금\n", " 예금적금 "):
            with self.subTest(kind=kind):
                data = saved_data()
                data["financial_assets"][0]["asset_type"] = kind
                original = deepcopy(data)
                result = map_retirement_profile(data)
                self.assertEqual((result.profile.liquid_assets, result.profile.monthly_savings), (20_000_000, 250_000))
                self.assertIn(InputMappingNotice(code="other_financial_asset_not_modeled",
                                                field_name="financial_assets[0]"), result.notices)
                self.assertEqual(data, original)

    def test_other_assets_do_not_require_or_interpret_unused_amounts(self):
        unused_values = (
            {},
            {"current_value": None, "monthly_savings": None},
            {"current_value": "PRIVATE_VALUE", "monthly_savings": "PRIVATE_SAVINGS"},
            {"current_value": -1, "monthly_savings": True},
            {"current_value": 0, "monthly_savings": 0},
        )
        expected = map_retirement_profile(saved_data()).profile
        for kind, unused in product(("기타", "전세보증금", "예금적금"), unused_values):
            with self.subTest(kind=kind, unused=unused):
                data = saved_data()
                data["financial_assets"].append({"asset_type": kind, "principal": 9_000_000_000} | unused)
                original = deepcopy(data)
                result = map_retirement_profile(data)
                self.assertEqual(result.profile, expected)
                self.assertIn(InputMappingNotice(code="other_financial_asset_not_modeled",
                                                field_name="financial_assets[2]"), result.notices)
                self.assertEqual(data, original)

    def test_unmodeled_amount_fields_are_not_read(self):
        class RejectAmountReads(dict):
            def __getitem__(self, key):
                if key in ("current_value", "monthly_savings", "principal"):
                    raise AssertionError("unused amount was accessed")
                return super().__getitem__(key)

        data = saved_data()
        expected = map_retirement_profile(data).profile
        data["financial_assets"].append(RejectAmountReads(
            asset_type="전세보증금", current_value=None, monthly_savings=None, principal=1,
        ))
        self.assertEqual(map_retirement_profile(data).profile, expected)

    def test_only_other_assets_are_not_confirmed_empty(self):
        data = saved_data()
        data["financial_assets"][0]["asset_type"] = "기타"
        data["financial_assets"][1]["asset_type"] = "전세보증금"
        original = deepcopy(data)
        result = map_retirement_profile(data)
        self.assertEqual((result.profile.liquid_assets, result.profile.monthly_savings), (0, 0))
        self.assertEqual(result.profile.pension_incomes, map_retirement_profile(saved_data()).profile.pension_incomes)
        self.assertEqual([notice.field_name for notice in result.notices
                          if notice.code == "other_financial_asset_not_modeled"],
                         ["financial_assets[0]", "financial_assets[1]"])
        self.assertNotIn("confirmed_empty_financial_assets", [notice.code for notice in result.notices])
        self.assertEqual(data, original)
        self.assert_mapping_error(data, "invalid_confirmation", "confirmed_empty_sections[0]",
                                  confirmed_empty_sections=["financial_assets"])

    def test_other_assets_still_require_valid_unique_ids(self):
        for kind, identifier in product(("기타", "전세보증금"), (None, True, 0, -1, "PRIVATE_ID")):
            with self.subTest(kind=kind, identifier=identifier):
                data = saved_data()
                data["financial_assets"][0].update(asset_type=kind, id=identifier)
                self.assert_mapping_error(data, "invalid_value", "financial_assets[0].id")
        for first_kind, second_kind in product(("기타", "전세보증금"), ("펀드/ETF", "기타", "회원권")):
            with self.subTest(first_kind=first_kind, second_kind=second_kind):
                data = saved_data()
                data["financial_assets"][0]["asset_type"] = first_kind
                data["financial_assets"][1].update(asset_type=second_kind, id=1)
                self.assert_mapping_error(data, "duplicate_id", "financial_assets[1].id")

    def test_other_assets_do_not_hide_later_invalid_rows(self):
        cases = (
            ({}, "missing_field", "financial_assets[1].asset_type"),
            ({"asset_type": None}, "missing_value", "financial_assets[1].asset_type"),
            ({"asset_type": " \t"}, "missing_value", "financial_assets[1].asset_type"),
            ({"asset_type": ["PRIVATE_ASSET"]}, "invalid_type", "financial_assets[1].asset_type"),
            ({"asset_type": "주식", "current_value": None, "monthly_savings": 0},
             "invalid_value", "financial_assets[1].current_value"),
            ({"asset_type": "주식", "current_value": 0}, "missing_field", "financial_assets[1].monthly_savings"),
            (None, "invalid_type", "financial_assets[1]"),
        )
        for row, code, path in cases:
            with self.subTest(code=code, path=path):
                data = saved_data()
                data["financial_assets"] = [{"asset_type": "전세보증금"}, row]
                self.assert_mapping_error(data, code, path)

    def test_other_asset_results_support_read_only_input_without_retaining_rows(self):
        data = saved_data()
        data["financial_assets"].append({"asset_type": "PRIVATE_ASSET", "name": "PRIVATE_OTHER_ASSET"})
        expected = map_retirement_profile(data)
        read_only = MappingProxyType(data | {
            "financial_assets": tuple(MappingProxyType(row) for row in data["financial_assets"]),
        })
        self.assertEqual(map_retirement_profile(read_only), expected)
        data["financial_assets"][2].update(asset_type="주식", current_value=900_000_000, monthly_savings=0)
        self.assertEqual((expected.profile.liquid_assets, expected.profile.monthly_savings), (120_000_000, 750_000))
        self.assertIn(InputMappingNotice(code="other_financial_asset_not_modeled",
                                        field_name="financial_assets[2]"), expected.notices)

    def test_other_asset_notice_requires_a_safe_financial_row_path(self):
        code = "other_financial_asset_not_modeled"
        for path in ("financial_assets[0]", "financial_assets[123]"):
            self.assertEqual(InputMappingNotice(code=code, field_name=path).field_name, path)
        for path in ("financial_assets", "financial_assets[-1]", "financial_assets[0].asset_type",
                     "financial_assets[PRIVATE_ID]", "pension_assets[0]", None):
            with self.subTest(path=path), self.assertRaises(ModelValidationError):
                InputMappingNotice(code=code, field_name=path)

    def test_missing_asset_type_is_not_inferred_as_other(self):
        data = saved_data()
        del data["financial_assets"][0]["asset_type"]
        data["financial_assets"][0]["name"] = "기타"
        self.assert_mapping_error(data, "missing_field", "financial_assets[0].asset_type")

    def test_empty_assets_and_pensions_require_explicit_confirmation(self):
        for name in ("financial_assets", "pension_assets"):
            data = saved_data()
            data[name] = []
            self.assert_mapping_error(data, "empty_confirmation_required", name)
        data = saved_data()
        data["financial_assets"] = []
        data["pension_assets"] = []
        result = map_retirement_profile(data, confirmed_empty_sections=["pension_assets", "financial_assets"])
        self.assertEqual((result.profile.liquid_assets, result.profile.monthly_savings), (0, 0))
        self.assertEqual(result.profile.pension_incomes, ())
        self.assertEqual([n.code for n in result.notices][-2:],
                         ["confirmed_empty_financial_assets", "confirmed_empty_pensions"])

    def test_confirmation_rejects_unknown_duplicate_and_contradictory_sections(self):
        data = saved_data()
        self.assert_mapping_error(data, "invalid_confirmation", "confirmed_empty_sections[0]",
                                  confirmed_empty_sections=["financial_assets"])
        self.assert_mapping_error(data, "invalid_type", "confirmed_empty_sections",
                                  confirmed_empty_sections="pension_assets")
        self.assert_mapping_error(data, "invalid_confirmation", "confirmed_empty_sections[0]",
                                  confirmed_empty_sections=["PRIVATE_SECTION"])
        data["pension_assets"] = []
        self.assert_mapping_error(data, "invalid_confirmation", "confirmed_empty_sections[1]",
                                  confirmed_empty_sections=["pension_assets", "pension_assets"])

    def test_duplicate_row_ids_and_nested_item_ids_are_rejected(self):
        data = saved_data()
        data["financial_assets"][1]["id"] = 1
        self.assert_mapping_error(data, "duplicate_id", "financial_assets[1].id")
        data = saved_data()
        data["goal_periods"][1]["items"][0]["id"] = 1
        self.assert_mapping_error(data, "duplicate_id", "goal_periods[1].items[0].id")

    def test_ids_are_optional_but_not_nullable_when_present(self):
        data = saved_data()
        for row in data["financial_assets"]:
            del row["id"]
        self.assertEqual(map_retirement_profile(data).profile.liquid_assets, 120_000_000)
        data["financial_assets"][0]["id"] = None
        self.assert_mapping_error(data, "invalid_value", "financial_assets[0].id")

    def test_ages_are_required_and_not_defaulted(self):
        for key in ("age", "retirement_age", "end_age"):
            data = saved_data()
            del data["basic_info"][key]
            self.assert_mapping_error(data, "missing_field", f"basic_info.{key}")
        for key, value in (("age", 17), ("retirement_age", 101), ("end_age", 121), ("age", True), ("end_age", 59)):
            data = saved_data()
            data["basic_info"][key] = value
            self.assert_mapping_error(data, "invalid_value", f"basic_info.{key}")

    def test_past_retirement_and_single_year_horizon_follow_internal_model(self):
        data = saved_data()
        data["basic_info"].update(age=75, retirement_age=60, end_age=75)
        profile = map_retirement_profile(data).profile
        self.assertEqual((profile.current_age, profile.retirement_age, profile.end_age), (75, 60, 75))
        self.assertEqual(len(calculate_cashflow(profile, map_simulation_assumptions(assumption_data())).years), 1)

    def test_expenses_are_sorted_without_mutating_or_clipping_other_periods(self):
        data = saved_data()
        data["goal_periods"].append({"phase": "after", "from_age": 55, "to_age": 59,
                                    "items": [{"amount": 100}]})
        data["goal_periods"].reverse()
        result = map_retirement_profile(data)
        self.assertEqual([p.from_age for p in result.profile.retirement_expenses], [55, 60, 75])
        self.assertEqual([p["from_age"] for p in data["goal_periods"]], [55, 75, 60])

    def test_before_goals_do_not_change_savings_or_retirement_spending(self):
        data = saved_data()
        data["goal_periods"].append({"phase": "before", "from_age": 40, "to_age": 59,
                                    "items": [{"amount": None}]})
        self.assertEqual(map_retirement_profile(data), map_retirement_profile(saved_data()))

    def test_overlaps_and_missing_retirement_years_are_not_repaired(self):
        for changed_start in (74, 76):
            data = saved_data()
            data["goal_periods"][1]["from_age"] = changed_start
            self.assert_mapping_error(data, "invalid_profile", "goal_periods")
        data = saved_data()
        data["goal_periods"] = []
        self.assert_mapping_error(data, "invalid_profile", "goal_periods")

    def test_empty_and_unknown_expense_amounts_are_not_zero(self):
        data = saved_data()
        data["goal_periods"][0]["items"] = []
        self.assert_mapping_error(data, "missing_value", "goal_periods[0].items")
        data = saved_data()
        data["goal_periods"][0]["items"][0]["amount"] = None
        self.assert_mapping_error(data, "invalid_value", "goal_periods[0].items[0].amount")

    def test_finite_pension_end_is_inclusive_and_not_contribution_duration(self):
        data = saved_data()
        data["pension_assets"][0].update(receive_method=" 연금형 ", pension_period=" 10년 ",
                                        payment_period_years=30)
        result = map_retirement_profile(data)
        self.assertEqual(result.profile.pension_incomes, (
            PensionIncome(start_age=65, end_age=74, monthly_amount=1_000_000),
        ))
        self.assertNotIn("pension_horizon_limited", [n.code for n in result.notices])

    def test_pension_end_is_limited_to_planning_horizon(self):
        data = saved_data()
        data["pension_assets"][0]["pension_period"] = "30년"
        result = map_retirement_profile(data)
        self.assertEqual(result.profile.pension_incomes[0].end_age, 90)
        self.assertEqual(result.notices[-1].code, "pension_horizon_limited")

    def test_outside_pensions_are_validated_then_omitted_with_notices(self):
        for start, duration in ((20, "10년"), (95, "종신")):
            data = saved_data()
            data["pension_assets"][0].update(pension_start_age=start, pension_period=duration)
            result = map_retirement_profile(data)
            self.assertEqual(result.profile.pension_incomes, ())
            self.assertEqual(result.notices[-1], InputMappingNotice(
                code="pension_outside_horizon", field_name="pension_assets[0]"))
            data["pension_assets"][0]["expected_amount"] = None
            self.assert_mapping_error(data, "invalid_value", "pension_assets[0].expected_amount")

    def test_different_pensions_with_identical_schedules_are_retained(self):
        data = saved_data()
        data["pension_assets"].append(data["pension_assets"][0] | {"id": 2})
        pensions = map_retirement_profile(data).profile.pension_incomes
        self.assertEqual(len(pensions), 2)
        self.assertEqual(pensions[0], pensions[1])

    def test_lump_sum_and_ambiguous_pension_periods_are_not_guessed(self):
        data = saved_data()
        data["pension_assets"][0]["receive_method"] = "일시금"
        self.assert_mapping_error(data, "unsupported_value", "pension_assets[0].receive_method")
        for period in ("기타", "10", "10~20년", "0년", "01년", "10 년", "121년"):
            data = saved_data()
            data["pension_assets"][0]["pension_period"] = period
            self.assert_mapping_error(data, "unsupported_value", "pension_assets[0].pension_period")
        data["pension_assets"][0]["pension_period"] = None
        self.assert_mapping_error(data, "missing_value", "pension_assets[0].pension_period")

    def test_excluded_finances_produce_notices_not_added_assets_or_payments(self):
        data = saved_data()
        for name in ("real_estate_assets", "liabilities", "goal_milestones"):
            data[name] = [{"id": 1, "amount": 900_000_000, "name": "PRIVATE_EXCLUDED"}]
        result = map_retirement_profile(data)
        self.assertEqual(result.profile, map_retirement_profile(saved_data()).profile)
        self.assertTrue({"real_estate_not_modeled", "liabilities_not_modeled", "milestones_not_modeled"}
                        <= {n.code for n in result.notices})
        self.assertNotIn("PRIVATE_EXCLUDED", repr(result.notices))

    def test_results_notices_and_nested_models_are_immutable(self):
        result = map_retirement_profile(saved_data())
        for obj, name, value in ((result, "profile", None), (result.notices[0], "code", "x"),
                                 (result.profile, "monthly_savings", 0)):
            with self.assertRaises(FrozenInstanceError):
                setattr(obj, name, value)
        notices = list(result.notices)
        copied = ProfileMappingResult(profile=result.profile, notices=notices)
        notices.clear()
        self.assertEqual(copied, result)

    def test_notice_constructor_rejects_raw_customer_paths(self):
        with self.assertRaises(ModelValidationError):
            InputMappingNotice(code="partial_financial_scope", field_name="PRIVATE_FIELD")
        with self.assertRaises(ModelValidationError):
            InputMappingNotice(code="PRIVATE_CODE", field_name="saved_data")


class AssumptionMappingTests(unittest.TestCase):
    def test_explicit_settings_map_to_existing_model(self):
        data = assumption_data(annual_return_mean=-0.1, annual_return_volatility=0.2,
                               annual_inflation_rate=0.03)
        original = deepcopy(data)
        self.assertEqual(map_simulation_assumptions(data), SimulationAssumptions(**data))
        self.assertEqual(data, original)

    def test_every_setting_is_required_including_paths_and_seed(self):
        for name in assumption_data():
            data = assumption_data()
            del data[name]
            with self.assertRaises(InputMappingError) as error:
                map_simulation_assumptions(data)
            self.assertEqual((error.exception.code, error.exception.field_name),
                             ("missing_field", f"assumptions.{name}"))

    def test_unknown_field_name_is_not_echoed(self):
        with self.assertRaises(InputMappingError) as error:
            map_simulation_assumptions(assumption_data(PRIVATE_FIELD="PRIVATE_VALUE"))
        self.assertEqual((error.exception.code, error.exception.field_name), ("unknown_field", "assumptions"))
        self.assertNotIn("PRIVATE", repr(error.exception))

    def test_rates_are_not_coerced_and_nonfinite_values_fail(self):
        for name in ("annual_return_mean", "annual_return_volatility", "annual_inflation_rate"):
            for value in (None, True, "0.05", Decimal("0.05"), float("nan"), float("inf")):
                with self.subTest(field=name, kind=type(value).__name__):
                    with self.assertRaises(InputMappingError) as error:
                        map_simulation_assumptions(assumption_data(**{name: value}))
                    self.assertEqual(error.exception.field_name, f"assumptions.{name}")

    def test_fraction_is_not_guessed_from_magnitude(self):
        self.assertEqual(map_simulation_assumptions(assumption_data(annual_return_mean=5)).annual_return_mean, 5)
        self.assertEqual(map_simulation_assumptions(assumption_data(annual_return_mean=0.05)).annual_return_mean, 0.05)

    def test_invalid_ranges_and_counts_use_safe_source_paths(self):
        for name, value in (("annual_return_mean", -1), ("annual_return_volatility", -0.1),
                            ("annual_inflation_rate", -1), ("num_paths", 0), ("num_paths", 100_001),
                            ("num_paths", True), ("seed", -1), ("seed", 1.0)):
            with self.subTest(field=name, value=value):
                with self.assertRaises(InputMappingError) as error:
                    map_simulation_assumptions(assumption_data(**{name: value}))
                self.assertEqual((error.exception.code, error.exception.field_name),
                                 ("invalid_value", f"assumptions.{name}"))


class AdapterEngineIntegrationTests(unittest.TestCase):
    def test_documented_hand_calculation_through_adapter_and_existing_engines(self):
        mapped = map_retirement_profile(saved_data())
        market = map_simulation_assumptions(assumption_data())
        cashflow = calculate_cashflow(mapped.profile, market)
        self.assertEqual((cashflow.final_balance, cashflow.first_depletion_age), (-36_000_000, 85))
        self.assertEqual(next(y.closing_balance for y in cashflow.years if y.age == 84), 0)
        baseline = calculate_monte_carlo(mapped.profile, market)
        self.assertEqual(baseline.success_probability, 0)
        self.assertEqual(baseline.final_balance_percentiles.p50, -36_000_000)
        comparison = calculate_what_if(mapped.profile, market, (
            WhatIfScenario(scenario_id="increase_savings", changes=WhatIfChanges(monthly_savings=1_000_000)),
        ))
        scenario = comparison.scenarios[0]
        self.assertEqual(scenario.result.final_balance_percentiles.p50, 24_000_000)
        self.assertEqual(scenario.result.depletion_count, 0)
        self.assertEqual(scenario.success_probability_delta_pp, 100)
        self.assertEqual(mapped.profile.monthly_savings, 750_000)

    def test_mapping_is_repeatable_and_does_not_consume_global_random_state(self):
        state = random.getstate()
        first = map_retirement_profile(saved_data())
        second = map_retirement_profile(saved_data())
        self.assertEqual(first, second)
        market = map_simulation_assumptions(assumption_data(annual_return_volatility=0.1))
        self.assertEqual(random.getstate(), state)
        self.assertEqual(calculate_monte_carlo(first.profile, market),
                         calculate_monte_carlo(second.profile, market))
        self.assertEqual(random.getstate(), state)


if __name__ == "__main__":
    unittest.main()
