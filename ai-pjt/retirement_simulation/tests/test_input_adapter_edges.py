"""Synthetic mapping boundaries; not DB/API, market, or load certification."""

from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
from dataclasses import FrozenInstanceError
from decimal import Decimal
from fractions import Fraction
from io import StringIO
from itertools import combinations, permutations, product
from math import nextafter
from pathlib import Path
import random
import subprocess
import sys
import unittest
from unittest.mock import patch


AI_PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(AI_PROJECT_ROOT))

from retirement_simulation import (  # noqa: E402
    CashflowCalculationError, InputMappingError, InputMappingNotice, ModelValidationError,
    PensionIncome, ProfileMappingResult, RetirementExpensePeriod, RetirementProfile,
    WhatIfChanges, WhatIfScenario, calculate_cashflow, calculate_what_if_paths,
    create_what_if_profile, map_retirement_profile, map_simulation_assumptions,
)
from retirement_simulation.tests.test_input_adapter import (  # noqa: E402
    assumption_data, saved_data,
)


def period(start, end, amount=5):
    return {"phase": "after", "from_age": start, "to_age": end,
            "items": [{"amount": amount}]}


def compact_saved(current=60, retirement=60, end=63):
    return {
        "basic_info": {"age": current, "retirement_age": retirement, "end_age": end},
        "financial_assets": [
            {"asset_type": "채권", "current_value": 1000, "monthly_savings": 2},
        ],
        "pension_assets": [
            {"receive_method": "연금형", "pension_start_age": 62,
             "pension_period": "2년", "expected_amount": 3},
        ],
        "goal_periods": [period(max(current, retirement), end)],
        "real_estate_assets": [], "liabilities": [], "goal_milestones": [],
    }


class _Checks(unittest.TestCase):
    def assert_mapping_error(self, data, code, path, **kwargs):
        original = deepcopy(data)
        with self.assertRaises(InputMappingError) as error:
            map_retirement_profile(data, **kwargs)
        self.assertEqual((error.exception.code, error.exception.field_name), (code, path))
        self.assertEqual(data, original)
        return error.exception


class MappingSchemaEdges(_Checks):
    def test_all_consumed_money_fields_reject_noninteger_values(self):
        for location, value in product(
            ("current_value", "monthly_savings", "expense", "expected_amount"),
            (None, True, False, -1, 0.0, "0", "100만", Decimal(0), Fraction(0),
             float("nan"), float("inf"), float("-inf"), [], {}),
        ):
            data = compact_saved()
            if location == "expense":
                row, key, path = data["goal_periods"][0]["items"][0], "amount", "goal_periods[0].items[0].amount"
            elif location == "expected_amount":
                row, key, path = data["pension_assets"][0], location, f"pension_assets[0].{location}"
            else:
                row, key, path = data["financial_assets"][0], location, f"financial_assets[0].{location}"
            row[key] = value
            with self.subTest(field=location, kind=type(value).__name__):
                self.assert_mapping_error(data, "invalid_value", path)

    def test_consumed_age_fields_reject_bool_fraction_and_missing_values(self):
        for (section, key), value in product(
            (("basic_info", "age"), ("basic_info", "retirement_age"), ("basic_info", "end_age"),
             ("goal_periods", "from_age"), ("goal_periods", "to_age"),
             ("pension_assets", "pension_start_age")),
            (None, False, "60", 60.0, Fraction(60), Decimal(60)),
        ):
            data = compact_saved()
            row = data[section] if section == "basic_info" else data[section][0]
            path = f"{section}.{key}" if section == "basic_info" else f"{section}[0].{key}"
            row[key] = value
            with self.subTest(field=path, kind=type(value).__name__):
                self.assert_mapping_error(data, "invalid_value", path)

    def test_missing_nested_keys_never_receive_fallback_values(self):
        cases = (
            ("financial_assets", "asset_type"), ("financial_assets", "current_value"),
            ("pension_assets", "receive_method"), ("pension_assets", "pension_start_age"),
            ("pension_assets", "pension_period"), ("pension_assets", "expected_amount"),
            ("goal_periods", "phase"), ("goal_periods", "from_age"),
            ("goal_periods", "to_age"), ("goal_periods", "items"),
        )
        for section, key in cases:
            data = compact_saved()
            del data[section][0][key]
            self.assert_mapping_error(data, "missing_field", f"{section}[0].{key}")
        data = compact_saved()
        del data["goal_periods"][0]["items"][0]["amount"]
        self.assert_mapping_error(data, "missing_field", "goal_periods[0].items[0].amount")

    def test_all_required_arrays_validate_row_structure_and_id_types(self):
        for section, value in product(
            ("financial_assets", "pension_assets", "goal_periods", "real_estate_assets", "liabilities", "goal_milestones"),
            (None, False, 0, 1.0, "1", Decimal(1), []),
        ):
            data = compact_saved()
            if not data[section]:
                data[section] = [{}]
            data[section][0]["id"] = value
            self.assert_mapping_error(data, "invalid_value", f"{section}[0].id")
        for section in ("real_estate_assets", "liabilities", "goal_milestones"):
            data = compact_saved()
            data[section] = [None]
            self.assert_mapping_error(data, "invalid_type", f"{section}[0]")
            data[section] = [{"id": 1}, {"id": 1}]
            self.assert_mapping_error(data, "duplicate_id", f"{section}[1].id")

    def test_generators_are_rejected_without_consuming_them(self):
        visited = []

        def rows():
            visited.append(True)
            yield {"amount": 0}

        data = compact_saved()
        data["financial_assets"] = rows()
        with self.assertRaises(InputMappingError) as error:
            map_retirement_profile(data)
        self.assertEqual(error.exception.field_name, "financial_assets")
        self.assertEqual(visited, [])

    def test_empty_confirmation_types_names_and_order(self):
        for value in (None, False, {}, {"pension_assets"}):
            self.assert_mapping_error(compact_saved(), "invalid_type", "confirmed_empty_sections",
                                      confirmed_empty_sections=value)
        for value in (None, True, [], "goal_periods", " pension_assets "):
            self.assert_mapping_error(compact_saved(), "invalid_confirmation", "confirmed_empty_sections[0]",
                                      confirmed_empty_sections=[value])
        data = compact_saved()
        data["financial_assets"], data["pension_assets"] = [], []
        results = [map_retirement_profile(data, confirmed_empty_sections=names)
                   for names in permutations(("financial_assets", "pension_assets"))]
        self.assertEqual(results[0], results[1])

    def test_approval_for_one_empty_section_does_not_cover_another(self):
        data = compact_saved()
        data["financial_assets"], data["pension_assets"] = [], []
        for confirmed, missing in (("pension_assets", "financial_assets"), ("financial_assets", "pension_assets")):
            self.assert_mapping_error(data, "empty_confirmation_required", missing,
                                      confirmed_empty_sections=[confirmed])

    def test_invalid_later_row_aborts_and_does_not_poison_next_call(self):
        for section, changes, code, path in (
            ("financial_assets", {"monthly_savings": None}, "invalid_value", "financial_assets[1].monthly_savings"),
            ("pension_assets", {"receive_method": "일시금"}, "unsupported_value", "pension_assets[1].receive_method"),
        ):
            data = compact_saved()
            data[section].append(data[section][0] | changes)
            self.assert_mapping_error(data, code, path)
            data[section].pop()
            self.assertEqual(map_retirement_profile(data), map_retirement_profile(compact_saved()))


class MappingAmountsAndExpensesEdges(_Checks):
    def test_totals_preserve_integer_precision_and_zero_balance_contributions(self):
        for value in (0, 1, 2**53 + 1, 10**400):
            data = compact_saved()
            data["financial_assets"] = [
                {"asset_type": "주식", "current_value": value, "monthly_savings": value},
                {"asset_type": "채권", "current_value": 0, "monthly_savings": 3},
            ]
            profile = map_retirement_profile(data).profile
            self.assertEqual((profile.liquid_assets, profile.monthly_savings), (value, value + 3))
            self.assertIs(type(profile.liquid_assets), int)
            self.assertIs(type(profile.monthly_savings), int)

    def test_numeric_mapping_success_does_not_claim_float_calculation_success(self):
        data = compact_saved()
        data["financial_assets"][0]["current_value"] = 10**400
        mapped = map_retirement_profile(data)
        with self.assertRaises(CashflowCalculationError):
            calculate_cashflow(mapped.profile, map_simulation_assumptions(assumption_data()))
        self.assertEqual(mapped.profile.liquid_assets, 10**400)

    def test_age_endpoints_and_single_year_retirement_horizons(self):
        for current, retirement, end in ((18, 18, 18), (18, 100, 120), (100, 100, 100), (100, 18, 120)):
            profile = map_retirement_profile(compact_saved(current, retirement, end)).profile
            self.assertEqual((profile.current_age, profile.retirement_age, profile.end_age),
                             (current, retirement, end))
        for current, retirement, end in ((17, 60, 63), (101, 60, 120), (60, 17, 63),
                                         (60, 101, 120), (60, 60, 121), (60, 60, 59)):
            with self.assertRaises(InputMappingError):
                map_retirement_profile(compact_saved(current, retirement, end))

    def test_interval_grid_matches_independent_per_age_coverage_counts(self):
        candidates = ((59, 59), (59, 60), (60, 60), (60, 61), (60, 63),
                      (61, 62), (62, 63), (63, 63), (64, 64))
        accepted = rejected = 0
        for size in range(4):
            for ranges in combinations(candidates, size):
                data = compact_saved()
                data["goal_periods"] = [period(start, end) for start, end in reversed(ranges)]
                # Count memberships directly; do not call model/mapping validation as an oracle.
                coverage = Counter(age for start, end in ranges for age in range(start, end + 1))
                valid = all(count == 1 for count in coverage.values()) and all(coverage[age] == 1 for age in range(60, 64))
                with self.subTest(ranges=ranges):
                    if valid:
                        result = map_retirement_profile(data)
                        self.assertEqual([(p.from_age, p.to_age) for p in result.profile.retirement_expenses], sorted(ranges))
                        self.assertTrue(all(p.monthly_amount == 5 for p in result.profile.retirement_expenses))
                        accepted += 1
                    else:
                        self.assert_mapping_error(data, "invalid_profile", "goal_periods")
                        rejected += 1
        self.assertEqual(accepted + rejected, 130)
        self.assertGreater(accepted, 0)
        self.assertGreater(rejected, 0)

    def test_inactive_overlaps_are_rejected_but_inactive_gaps_are_not_filled(self):
        data = compact_saved()
        data["goal_periods"] += [period(20, 25), period(25, 30)]
        self.assert_mapping_error(data, "invalid_profile", "goal_periods")
        data["goal_periods"][2]["from_age"] = 27
        result = map_retirement_profile(data)
        self.assertEqual([(p.from_age, p.to_age) for p in result.profile.retirement_expenses],
                         [(20, 25), (27, 30), (60, 63)])

    def test_period_global_bounds_and_reversed_interval(self):
        for start, end, key in ((0, 63, "from_age"), (60, 121, "to_age"), (63, 60, "to_age")):
            data = compact_saved()
            data["goal_periods"] = [period(start, end)]
            self.assert_mapping_error(data, "invalid_value", f"goal_periods[0].{key}")

    def test_nested_item_structure_and_phase_are_validated_even_before_retirement(self):
        for phase in (None, True, [], "AFTER", "after "):
            data = compact_saved()
            data["goal_periods"][0]["phase"] = phase
            self.assert_mapping_error(data, "unsupported_value", "goal_periods[0].phase")
        for phase, items in product(("before", "after"), (None, {}, [None])):
            data = compact_saved()
            data["goal_periods"][0].update(phase=phase, items=items)
            suffix = "[0]" if isinstance(items, list) else ""
            self.assert_mapping_error(data, "invalid_type", f"goal_periods[0].items{suffix}")

    def test_item_ids_are_global_across_before_and_after_periods(self):
        data = compact_saved()
        data["goal_periods"][0]["items"][0]["id"] = 9
        data["goal_periods"].append({"phase": "before", "items": [{"id": 9, "amount": None}]})
        self.assert_mapping_error(data, "duplicate_id", "goal_periods[1].items[0].id")

    def test_item_names_fixed_flags_and_permutations_do_not_filter_spending(self):
        items = [{"amount": 2, "name": "same", "is_fixed": True},
                 {"amount": 3, "name": "same", "is_fixed": False},
                 {"amount": 0, "name": None, "is_fixed": None}]
        for ordering in permutations(items):
            data = compact_saved()
            data["goal_periods"][0]["items"] = list(ordering)
            self.assertEqual(map_retirement_profile(data).profile.retirement_expenses[0].monthly_amount, 5)

    def test_money_is_monthly_and_inflation_is_applied_only_once_by_engine(self):
        data = compact_saved(retirement=61)
        mapped = map_retirement_profile(data)
        market = map_simulation_assumptions(assumption_data(annual_inflation_rate=0.5))
        years = calculate_cashflow(mapped.profile, market).years
        self.assertEqual([y.savings for y in years], [24, 0, 0, 0])
        self.assertEqual([y.expenses for y in years], [0, 90, 135, 202.5])
        self.assertEqual([y.pension_income for y in years], [0, 0, 36, 36])
        self.assertEqual(years[-1].closing_balance, 668.5)


class MappingPensionEdges(_Checks):
    def test_finite_pension_grid_matches_independent_age_set_intersection(self):
        checked = 0
        for (current, end), start, duration in product(
            ((18, 18), (40, 45), (80, 100), (100, 120)),
            (1, 18, 39, 40, 41, 65, 100, 120), (1, 2, 10, 120),
        ):
            data = compact_saved(current, current, end)
            data["pension_assets"][0].update(pension_start_age=start, pension_period=f"{duration}년")
            result = map_retirement_profile(data)
            expected = set(range(start, start + duration)) & set(range(current, end + 1))
            with self.subTest(current=current, end=end, start=start, duration=duration):
                for age in range(current, end + 1):
                    payment = sum(p.monthly_amount for p in result.profile.pension_incomes if p.start_age <= age <= p.end_age)
                    self.assertEqual(payment, 3 if age in expected else 0)
                if expected:
                    self.assertEqual(len(result.profile.pension_incomes), 1)
                    self.assertEqual(result.profile.pension_incomes[0].start_age, start)
                    self.assertEqual(result.profile.pension_incomes[0].end_age, max(expected))
                else:
                    self.assertEqual(result.profile.pension_incomes, ())
                    self.assertEqual(result.notices[-1].code, "pension_outside_horizon")
                limited = bool(expected) and start + duration - 1 > end
                self.assertEqual(any(n.code == "pension_horizon_limited" for n in result.notices), limited)
                checked += 1
        self.assertEqual(checked, 128)

    def test_lifetime_grid_and_end_equal_start(self):
        for (current, end), start in product(
            ((18, 18), (40, 45), (80, 100), (100, 120)),
            (1, 18, 39, 40, 41, 65, 100, 120),
        ):
            data = compact_saved(current, current, end)
            data["pension_assets"][0].update(pension_start_age=start, pension_period="종신")
            result = map_retirement_profile(data)
            for age in range(current, end + 1):
                payment = sum(p.monthly_amount for p in result.profile.pension_incomes if p.start_age <= age <= p.end_age)
                self.assertEqual(payment, 3 if age >= start else 0)
            expected_code = "pension_horizon_limited" if start <= end else "pension_outside_horizon"
            self.assertEqual(result.notices[-1].code, expected_code)

    def test_duration_grammar_rejects_unicode_digits_and_noncanonical_text(self):
        for value in ("１年", "１０년", "١٠년", "+10년", "-1년", "1.0년", "1e1년", "1\n년", "1200년", "10년 이후"):
            data = compact_saved()
            data["pension_assets"][0]["pension_period"] = value
            self.assert_mapping_error(data, "unsupported_value", "pension_assets[0].pension_period")

    def test_text_missing_types_and_outer_whitespace(self):
        for key, value in product(("receive_method", "pension_period"), (None, "", " \t\n", False, 10, [], {})):
            data = compact_saved()
            data["pension_assets"][0][key] = value
            code = "missing_value" if value is None or isinstance(value, str) else "invalid_type"
            self.assert_mapping_error(data, code, f"pension_assets[0].{key}")
        data = compact_saved()
        data["pension_assets"][0].update(receive_method="\t연금형\n", pension_period="\t2년\n")
        self.assertEqual(map_retirement_profile(data), map_retirement_profile(compact_saved()))

    def test_outside_pensions_cannot_bypass_type_or_duration_validation(self):
        for changes, code, key in (
            ({"pension_start_age": 121}, "invalid_value", "pension_start_age"),
            ({"pension_start_age": 0}, "invalid_value", "pension_start_age"),
            ({"pension_start_age": 120, "pension_period": "기타"}, "unsupported_value", "pension_period"),
            ({"pension_start_age": 120, "receive_method": "일시금"}, "unsupported_value", "receive_method"),
        ):
            data = compact_saved()
            data["pension_assets"][0].update(changes)
            self.assert_mapping_error(data, code, f"pension_assets[0].{key}")

    def test_mixed_pensions_preserve_source_order_and_notice_indices(self):
        data = compact_saved()
        row = data["pension_assets"][0]
        data["pension_assets"] = [
            row | {"pension_start_age": 20, "pension_period": "1년"},
            row | {"pension_start_age": 63, "pension_period": "종신"},
            row | {"pension_start_age": 60, "pension_period": "1년"},
            row | {"pension_start_age": 120, "pension_period": "1년"},
        ]
        result = map_retirement_profile(data)
        self.assertEqual([(p.start_age, p.end_age) for p in result.profile.pension_incomes], [(63, 63), (60, 60)])
        self.assertEqual([(n.code, n.field_name) for n in result.notices[-3:]], [
            ("pension_outside_horizon", "pension_assets[0]"),
            ("pension_horizon_limited", "pension_assets[1]"),
            ("pension_outside_horizon", "pension_assets[3]"),
        ])

    def test_zero_receipt_is_not_inferred_from_unknown_expected_amount(self):
        data = compact_saved()
        data["pension_assets"][0].update(expected_amount=0, current_value=10**30,
                                        monthly_savings=10**20, expected_return_rate="20")
        result = map_retirement_profile(data)
        self.assertEqual(result.profile.pension_incomes[0].monthly_amount, 0)
        self.assertEqual(result.profile.liquid_assets, 1000)
        del data["pension_assets"][0]["expected_amount"]
        self.assert_mapping_error(data, "missing_field", "pension_assets[0].expected_amount")


class MappingAssumptionEdges(unittest.TestCase):
    def test_invalid_root_and_unknown_key_types_are_safe(self):
        for value in (None, [], False, "PRIVATE_INPUT"):
            with self.assertRaises(InputMappingError) as error:
                map_simulation_assumptions(value)
            self.assertEqual((error.exception.code, error.exception.field_name), ("invalid_type", "assumptions"))
        for key in (None, 7, ("PRIVATE_KEY",), "PRIVATE_KEY"):
            with self.assertRaises(InputMappingError) as error:
                map_simulation_assumptions(assumption_data() | {key: "PRIVATE_VALUE"})
            self.assertEqual(error.exception.code, "unknown_field")
            self.assertNotIn("PRIVATE", repr(error.exception))

    def test_nextafter_boundaries_are_not_rounded_or_clamped(self):
        for key in ("annual_return_mean", "annual_inflation_rate"):
            inside = nextafter(-1.0, 0.0)
            self.assertEqual(getattr(map_simulation_assumptions(assumption_data(**{key: inside})), key), inside)
            for outside in (-1, nextafter(-1.0, float("-inf"))):
                with self.assertRaises(InputMappingError):
                    map_simulation_assumptions(assumption_data(**{key: outside}))
        for inside in (-0.0, nextafter(0.0, 1.0), sys.float_info.max):
            self.assertEqual(map_simulation_assumptions(assumption_data(annual_return_volatility=inside)).annual_return_volatility, inside)
        with self.assertRaises(InputMappingError):
            map_simulation_assumptions(assumption_data(annual_return_volatility=nextafter(0.0, -1.0)))

    def test_huge_integer_rates_fail_without_raw_overflow_or_fallback(self):
        for key in ("annual_return_mean", "annual_return_volatility", "annual_inflation_rate"):
            with self.assertRaises(InputMappingError) as error:
                map_simulation_assumptions(assumption_data(**{key: 10**400}))
            self.assertEqual((error.exception.code, error.exception.field_name), ("invalid_value", f"assumptions.{key}"))
            self.assertTrue(error.exception.__suppress_context__)
        self.assertEqual(map_simulation_assumptions(assumption_data()).annual_return_mean, 0)

    def test_path_and_seed_limits_validate_without_allocating_paths(self):
        with patch("retirement_simulation.return_paths.generate_return_paths", side_effect=AssertionError("no sampling")):
            for count, seed in product((1, 100_000), (0, 10**400)):
                mapped = map_simulation_assumptions(assumption_data(num_paths=count, seed=seed))
                self.assertEqual((mapped.num_paths, mapped.seed), (count, seed))
        for key in ("num_paths", "seed"):
            for value in (None, False, "1", 1.0, Decimal(1), Fraction(1), float("inf")):
                with self.assertRaises(InputMappingError) as error:
                    map_simulation_assumptions(assumption_data(**{key: value}))
                self.assertEqual(error.exception.field_name, f"assumptions.{key}")

    def test_fixed_error_precedence_is_independent_of_mapping_key_order(self):
        data = assumption_data(annual_return_mean=-1, num_paths=0)
        for mapping in (data, dict(reversed(tuple(data.items())))):
            with self.assertRaises(InputMappingError) as error:
                map_simulation_assumptions(mapping)
            self.assertEqual(error.exception.field_name, "assumptions.annual_return_mean")


class MappingIntegrationAndIsolationEdges(_Checks):
    def test_mapped_inputs_match_direct_models_on_identical_what_if_paths(self):
        raw = compact_saved(retirement=61)
        actual = map_retirement_profile(raw).profile
        direct = RetirementProfile(
            current_age=60, retirement_age=61, end_age=63, liquid_assets=1000, monthly_savings=2,
            retirement_expenses=(RetirementExpensePeriod(from_age=61, to_age=63, monthly_amount=5),),
            pension_incomes=(PensionIncome(start_age=62, end_age=63, monthly_amount=3),),
        )
        self.assertEqual(actual, direct)
        scenarios = (WhatIfScenario(scenario_id="savings", changes=WhatIfChanges(monthly_savings=0)),
                     WhatIfScenario(scenario_id="later", changes=WhatIfChanges(retirement_age=62)))
        paths = ((0, 0.25, -0.5, 0), (-0.5, 0, 0.25, 0))
        market = map_simulation_assumptions(assumption_data())
        self.assertEqual(calculate_what_if_paths(actual, market, scenarios, annual_return_paths=paths),
                         calculate_what_if_paths(direct, market, scenarios, annual_return_paths=paths))

    def test_early_retirement_requires_complete_expense_replacement(self):
        raw = compact_saved(retirement=61)
        original = deepcopy(raw)
        mapped = map_retirement_profile(raw)
        with self.assertRaises(ModelValidationError):
            create_what_if_profile(mapped.profile, WhatIfChanges(retirement_age=60))
        changed = create_what_if_profile(mapped.profile, WhatIfChanges(retirement_age=60,
            retirement_expenses=(RetirementExpensePeriod(from_age=60, to_age=63, monthly_amount=4),)))
        self.assertEqual(changed.pension_incomes, mapped.profile.pension_incomes)
        self.assertEqual(changed.pension_incomes[0].start_age, 62)
        self.assertEqual(raw, original)
        self.assertEqual(mapped.profile.retirement_age, 61)

    def test_already_retired_mapping_keeps_past_age_and_savings_have_no_effect(self):
        mapped = map_retirement_profile(compact_saved(70, 60, 72))
        with self.assertRaises(ModelValidationError):
            create_what_if_profile(mapped.profile, WhatIfChanges(retirement_age=65))
        changed = create_what_if_profile(mapped.profile, WhatIfChanges(monthly_savings=10**6))
        market = map_simulation_assumptions(assumption_data())
        self.assertEqual(calculate_cashflow(mapped.profile, market), calculate_cashflow(changed, market))

    def test_excluded_finances_change_notices_but_not_calculation(self):
        original = compact_saved()
        data = deepcopy(original)
        for section in ("real_estate_assets", "liabilities", "goal_milestones"):
            data[section] = [{"id": 91, "current_value": 10**20, "secured_loan": 10**20,
                              "amount": 10**20, "name": "PRIVATE_EXCLUDED"}]
        baseline, partial = map_retirement_profile(original), map_retirement_profile(data)
        self.assertEqual(baseline.profile, partial.profile)
        self.assertNotEqual(baseline.notices, partial.notices)
        self.assertEqual(len(partial.notices) - len(baseline.notices), 3)
        self.assertNotIn("PRIVATE", repr(partial.notices))

    def test_repeated_and_concurrent_mapping_has_no_shared_mutable_state(self):
        raw = saved_data()
        original = deepcopy(raw)
        state = random.getstate()
        expected = map_retirement_profile(raw)

        def run(index):
            if index % 4 == 0:
                invalid = deepcopy(raw)
                invalid["pension_assets"][0]["expected_amount"] = None
                self.assert_mapping_error(invalid, "invalid_value", "pension_assets[0].expected_amount")
            return map_retirement_profile(raw), map_simulation_assumptions(assumption_data(seed=index))

        with ThreadPoolExecutor(max_workers=3) as executor:
            results = list(executor.map(run, range(24)))
        for index, (mapped, market) in enumerate(results):
            self.assertEqual(mapped, expected)
            self.assertIsNot(mapped.profile, expected.profile)
            self.assertEqual(market.seed, index)
        self.assertEqual(raw, original)
        self.assertEqual(random.getstate(), state)

    def test_errors_and_notices_do_not_write_raw_content_to_streams(self):
        data = compact_saved()
        data["customer_name"] = "PRIVATE_CUSTOMER"
        data["financial_assets"][0]["asset_type"] = "PRIVATE_ASSET"
        output, errors = StringIO(), StringIO()
        with redirect_stdout(output), redirect_stderr(errors):
            error = self.assert_mapping_error(data, "unsupported_value", "financial_assets[0].asset_type")
            result = map_retirement_profile(compact_saved())
        self.assertEqual((output.getvalue(), errors.getvalue()), ("", ""))
        for rendered in (str(error), repr(error), repr(error.__dict__), repr(result), repr(result.notices)):
            self.assertNotIn("PRIVATE", rendered)

    def test_public_result_types_reject_wrong_values_and_snapshot_notices(self):
        mapped = map_retirement_profile(compact_saved())
        with self.assertRaises(ModelValidationError):
            ProfileMappingResult(profile={}, notices=mapped.notices)
        for notices in (None, {}, "PRIVATE", [None]):
            with self.assertRaises(ModelValidationError):
                ProfileMappingResult(profile=mapped.profile, notices=notices)
        for code in (None, [], False):
            with self.assertRaises(ModelValidationError):
                InputMappingNotice(code=code, field_name="saved_data")
        for path in (None, False, "pension_assets[PRIVATE]", "pension_assets[0]\n"):
            with self.assertRaises(ModelValidationError):
                InputMappingNotice(code="pension_outside_horizon", field_name=path)
        copied = list(mapped.notices)
        result = ProfileMappingResult(profile=mapped.profile, notices=copied)
        copied.clear()
        self.assertEqual(result.notices, mapped.notices)
        with self.assertRaises(FrozenInstanceError):
            result.mapping_version = "changed"
        with self.assertRaises(TypeError):
            ProfileMappingResult(profile=mapped.profile, notices=mapped.notices, mapping_version="changed")

    def test_mapping_without_site_packages_network_files_or_environment_lookup(self):
        script = '''
import sys
class RejectApplicationImports:
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'django', 'fastapi', 'httpx', 'requests', 'numpy', 'pandas', 'langgraph', 'llama_index'}:
            raise AssertionError('application dependency imported')
sys.meta_path.insert(0, RejectApplicationImports())
from unittest.mock import patch
from retirement_simulation import map_retirement_profile, map_simulation_assumptions
from retirement_simulation.tests.test_input_adapter import saved_data, assumption_data
data, settings = saved_data(), assumption_data()
with patch('builtins.open', side_effect=AssertionError('file access')), \\
     patch('socket.socket', side_effect=AssertionError('network access')), \\
     patch('os.getenv', side_effect=AssertionError('environment lookup')), \\
     patch('random.Random', side_effect=AssertionError('random allocation')):
    mapped = map_retirement_profile(data)
    market = map_simulation_assumptions(settings)
assert mapped.profile.monthly_savings == 750000
assert market.num_paths == 2
print('isolated mapping OK')
'''
        result = subprocess.run([sys.executable, "-S", "-B", "-c", script], cwd=AI_PROJECT_ROOT,
                                capture_output=True, text=True, timeout=10, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "isolated mapping OK\n")
        self.assertEqual(result.stderr, "")


if __name__ == "__main__":
    unittest.main()
