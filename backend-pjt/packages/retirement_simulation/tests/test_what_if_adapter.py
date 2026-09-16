"""Raw change-mapping contracts and real-engine checks with synthetic inputs."""

from __future__ import annotations

from collections import UserDict
from contextlib import ExitStack, redirect_stderr, redirect_stdout
from copy import deepcopy
from dataclasses import FrozenInstanceError
from decimal import Decimal
from io import StringIO
from pathlib import Path
import random
import sys
import traceback
from types import MappingProxyType
import unittest
from unittest.mock import patch


AI_PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(AI_PROJECT_ROOT))

from retirement_simulation import (  # noqa: E402
    MAX_WHAT_IF_SCENARIOS, InputMappingError, ModelValidationError,
    PensionIncome, RetirementExpensePeriod, RetirementProfile,
    WhatIfChanges, WhatIfScenario, calculate_what_if, create_what_if_profile,
    map_retirement_profile, map_simulation_assumptions, map_what_if_scenarios,
)
from retirement_simulation.tests.test_input_adapter import (  # noqa: E402
    assumption_data, saved_data,
)


def expense(start=60, end=61, amount=100):
    return {"from_age": start, "to_age": end, "monthly_amount": amount}


def scenario(identifier="changed", **changes):
    return {"scenario_id": identifier, "changes": changes}


def profile(**changes):
    return RetirementProfile(**({
        "current_age": 58, "retirement_age": 60, "end_age": 61,
        "liquid_assets": 1200, "monthly_savings": 100,
        "retirement_expenses": (RetirementExpensePeriod(**expense()),),
        "pension_incomes": (PensionIncome(start_age=61, end_age=61, monthly_amount=50),),
    } | changes))


class WhatIfAdapterTests(unittest.TestCase):
    def assert_mapping_error(self, values, code, path):
        with self.assertRaises(InputMappingError) as raised:
            map_what_if_scenarios(values)
        self.assertEqual((raised.exception.code, raised.exception.field_name), (code, path))
        return raised.exception

    def test_empty_list_and_tuple_return_empty_tuple(self):
        for values in ([], ()):
            self.assertEqual(map_what_if_scenarios(values), ())

    def test_one_through_ten_scenarios_keep_order(self):
        self.assertEqual(MAX_WHAT_IF_SCENARIOS, 10)
        for count in range(1, 11):
            with self.subTest(count=count):
                values = [scenario(f"case_{i}", monthly_savings=i) for i in range(count)]
                result = map_what_if_scenarios(values)
                self.assertIsInstance(result, tuple)
                self.assertEqual([row.scenario_id for row in result],
                                 [row["scenario_id"] for row in values])
                self.assertEqual([row.changes.monthly_savings for row in result], list(range(count)))

    def test_count_guard_runs_before_row_validation(self):
        for values in ([None] * 11, (None,) * 11):
            self.assert_mapping_error(values, "invalid_value", "scenarios")

    def test_unsupported_root_structures_are_not_coerced(self):
        for values in (None, {}, "private", b"[]", 0, True, set(), iter(())):
            with self.subTest(kind=type(values).__name__):
                self.assert_mapping_error(values, "invalid_type", "scenarios")

    def test_rows_require_mapping_not_prebuilt_models(self):
        model = WhatIfScenario(scenario_id="changed", changes=WhatIfChanges())
        for value in (None, [], "private", 0, True, model):
            self.assert_mapping_error([value], "invalid_type", "scenarios[0]")

    def test_both_scenario_fields_are_required(self):
        for key in ("scenario_id", "changes"):
            values = [scenario()]
            del values[0][key]
            self.assert_mapping_error(values, "missing_field", f"scenarios[0].{key}")

    def test_changes_requires_mapping_even_for_noop(self):
        for value in (None, [], (), "private", 0, True, WhatIfChanges()):
            self.assert_mapping_error([{"scenario_id": "changed", "changes": value}],
                                      "invalid_type", "scenarios[0].changes")

    def test_all_nested_mappings_and_tuple_arrays_are_supported(self):
        expected = map_what_if_scenarios([scenario(retirement_expenses=[expense()])])
        for mapping in (MappingProxyType, UserDict):
            values = (mapping({"scenario_id": "changed", "changes": mapping({
                "retirement_expenses": (mapping(expense()),),
            })}),)
            self.assertEqual(map_what_if_scenarios(values), expected)

    def test_unknown_fields_rejected_at_containing_object_path(self):
        for key in ("PRIVATE_UNKNOWN_KEY", 938475, ("PRIVATE_KEY",)):
            values = [scenario(retirement_expenses=[expense()])]
            targets = (
                (values[0], "scenarios[0]"),
                (values[0]["changes"], "scenarios[0].changes"),
                (values[0]["changes"]["retirement_expenses"][0],
                 "scenarios[0].changes.retirement_expenses[0]"),
            )
            for target, path in targets:
                target[key] = "PRIVATE_VALUE"
                error = self.assert_mapping_error(values, "unknown_field", path)
                self.assertNotIn("PRIVATE", str(error))
                self.assertNotIn("938475", repr(error))
                del target[key]

    def test_market_pension_and_baseline_fields_are_not_change_inputs(self):
        for key in ("annual_return_mean", "seed", "pension_incomes", "liquid_assets",
                    "end_age", "current_age", "monthly_savings_delta", "current_form"):
            self.assert_mapping_error([scenario(**{key: 0})], "unknown_field",
                                      "scenarios[0].changes")

    def test_identifier_grammar_matches_existing_model(self):
        identifiers = ("a", "a" * 64, "a0_b-c", "baseline_", "z9", "baseline", "",
                       "a" * 65, "A", "aA", "1a", "_a", "-a", " a", "a ",
                       "a\n", "a.b", "은퇴", "aé", None, True, 1, [], {})
        for identifier in identifiers:
            with self.subTest(identifier=identifier):
                try:
                    expected = WhatIfScenario(scenario_id=identifier, changes=WhatIfChanges())
                except ModelValidationError:
                    self.assert_mapping_error([scenario(identifier)], "invalid_value",
                                              "scenarios[0].scenario_id")
                else:
                    self.assertEqual(map_what_if_scenarios([scenario(identifier)]), (expected,))

    def test_duplicate_identifier_reports_second_index_not_value(self):
        values = [scenario("private_identifier"), scenario("other"), scenario("private_identifier")]
        error = self.assert_mapping_error(values, "duplicate_id", "scenarios[2].scenario_id")
        self.assertNotIn("private_identifier", str(error))

    def test_missing_and_null_changes_normalize_to_noop(self):
        expected = (WhatIfScenario(scenario_id="changed", changes=WhatIfChanges()),)
        self.assertEqual(map_what_if_scenarios([scenario()]), expected)
        for key in ("monthly_savings", "retirement_age", "retirement_expenses"):
            self.assertEqual(map_what_if_scenarios([scenario(**{key: None})]), expected)
        self.assertEqual(map_what_if_scenarios([scenario(
            monthly_savings=None, retirement_age=None, retirement_expenses=None,
        )]), expected)

    def test_zero_and_arbitrarily_large_integer_savings_are_preserved(self):
        for amount in (0, 1, 10**400):
            change = map_what_if_scenarios([scenario(monthly_savings=amount)])[0].changes
            self.assertEqual(change.monthly_savings, amount)
            self.assertIsInstance(change.monthly_savings, int)

    def test_savings_invalid_values_are_not_converted(self):
        for value in (-1, True, False, 0.0, 100.5, "100", Decimal(100), float("nan"),
                      float("inf"), [], {}):
            self.assert_mapping_error([scenario(monthly_savings=value)], "invalid_value",
                                      "scenarios[0].changes.monthly_savings")

    def test_retirement_age_inclusive_boundaries(self):
        for age in (18, 60, 100):
            change = map_what_if_scenarios([scenario(retirement_age=age)])[0].changes
            self.assertEqual(change.retirement_age, age)

    def test_retirement_age_invalid_values(self):
        for age in (0, 17, 101, True, False, 60.0, "60", Decimal(60), float("nan"), [], {}):
            self.assert_mapping_error([scenario(retirement_age=age)], "invalid_value",
                                      "scenarios[0].changes.retirement_age")

    def test_expense_collections_and_rows_require_supported_structure(self):
        path = "scenarios[0].changes.retirement_expenses"
        for value in ({}, "private", True, 0, set(), iter(())):
            self.assert_mapping_error([scenario(retirement_expenses=value)], "invalid_type", path)
        for row in (None, [], "private", True, 0, RetirementExpensePeriod(**expense())):
            self.assert_mapping_error([scenario(retirement_expenses=[row])],
                                      "invalid_type", f"{path}[0]")

    def test_explicit_empty_expenses_are_not_a_noop_or_zero_schedule(self):
        for periods in ([], ()):
            self.assert_mapping_error([scenario(retirement_expenses=periods)], "missing_value",
                                      "scenarios[0].changes.retirement_expenses")

    def test_each_expense_field_is_required(self):
        for key in expense():
            row = expense()
            del row[key]
            self.assert_mapping_error([scenario(retirement_expenses=[row])], "missing_field",
                                      f"scenarios[0].changes.retirement_expenses[0].{key}")

    def test_expense_ages_are_inclusive_without_restricting_to_retirement_age(self):
        periods = [expense(1, 1, 0), expense(2, 119, 1), expense(120, 120, 2)]
        result = map_what_if_scenarios([scenario(retirement_expenses=periods)])
        self.assertEqual(result[0].changes.retirement_expenses,
                         tuple(RetirementExpensePeriod(**row) for row in periods))

    def test_expense_ages_reject_invalid_types_and_ranges(self):
        for key in ("from_age", "to_age"):
            for value in (None, -1, 0, 121, True, False, 60.0, "60", Decimal(60), float("inf")):
                row = expense() | {key: value}
                self.assert_mapping_error([scenario(retirement_expenses=[row])], "invalid_value",
                                          f"scenarios[0].changes.retirement_expenses[0].{key}")

    def test_reversed_expense_interval_reports_end_age(self):
        self.assert_mapping_error([scenario(retirement_expenses=[expense(61, 60)])],
                                  "invalid_value", "scenarios[0].changes.retirement_expenses[0].to_age")

    def test_expense_amount_rejects_null_and_non_integer_values(self):
        for value in (None, -1, True, False, 0.0, "0", Decimal(0), float("nan"), [], {}):
            self.assert_mapping_error([scenario(retirement_expenses=[expense(amount=value)])],
                                      "invalid_value",
                                      "scenarios[0].changes.retirement_expenses[0].monthly_amount")

    def test_zero_and_large_expense_amounts_are_preserved(self):
        for amount in (0, 10**400):
            result = map_what_if_scenarios([scenario(retirement_expenses=[expense(amount=amount)])])
            self.assertEqual(result[0].changes.retirement_expenses[0].monthly_amount, amount)

    def test_nested_error_paths_use_both_array_indices(self):
        values = [scenario("valid"), scenario(retirement_expenses=[expense(), expense(62, 63)])]
        values[1]["changes"]["retirement_expenses"][1]["monthly_amount"] = -1
        self.assert_mapping_error(values, "invalid_value",
                                  "scenarios[1].changes.retirement_expenses[1].monthly_amount")

    def test_expense_source_order_is_preserved_until_profile_application(self):
        periods = [expense(61, 61, 50), expense(60, 60, 100)]
        changes = map_what_if_scenarios([scenario(retirement_expenses=periods)])[0].changes
        self.assertEqual([p.from_age for p in changes.retirement_expenses], [61, 60])
        applied = create_what_if_profile(profile(), changes)
        self.assertEqual([p.from_age for p in applied.retirement_expenses], [60, 61])
        self.assertEqual([p.from_age for p in changes.retirement_expenses], [61, 60])

    def test_overlap_and_missing_coverage_are_deferred_to_application(self):
        for periods in ([expense(), expense(61, 62)], [expense(60, 60)], [expense(62, 63)]):
            changes = map_what_if_scenarios([scenario(retirement_expenses=periods)])[0].changes
            with self.assertRaises(ModelValidationError) as raised:
                create_what_if_profile(profile(), changes)
            self.assertEqual(raised.exception.field_name, "retirement_expenses")

    def test_baseline_dependent_age_checks_are_deferred(self):
        for baseline, age, field in (
            (profile(), 57, "retirement_age"),
            (profile(), 62, "end_age"),
            (profile(current_age=61), 61, "retirement_age"),
        ):
            changes = map_what_if_scenarios([scenario(retirement_age=age)])[0].changes
            with self.assertRaises(ModelValidationError) as raised:
                create_what_if_profile(baseline, changes)
            self.assertEqual(raised.exception.field_name, field)

    def test_same_past_retirement_age_remains_a_valid_noop(self):
        baseline = profile(current_age=61)
        changes = map_what_if_scenarios([scenario(retirement_age=60)])[0].changes
        self.assertEqual(create_what_if_profile(baseline, changes), baseline)

    def test_early_retirement_and_expense_replacement_apply_together_without_pension_shift(self):
        baseline = profile()
        changes = map_what_if_scenarios([scenario(
            retirement_age=59, retirement_expenses=[expense(59, 61, 200)],
        )])[0].changes
        applied = create_what_if_profile(baseline, changes)
        self.assertEqual(applied.retirement_age, 59)
        self.assertEqual(applied.retirement_expenses, (RetirementExpensePeriod(**expense(59, 61, 200)),))
        self.assertEqual(applied.pension_incomes, baseline.pension_incomes)
        self.assertEqual(baseline, profile())

    def test_savings_are_absolute_and_scenarios_are_not_cumulative(self):
        baseline = profile()
        definitions = map_what_if_scenarios([
            scenario("save_more", monthly_savings=200),
            scenario("stop_saving", monthly_savings=0),
            scenario("keep"),
        ])
        applied = [create_what_if_profile(baseline, row.changes) for row in definitions]
        self.assertEqual([p.monthly_savings for p in applied], [200, 0, 100])
        self.assertEqual(baseline, profile())

    def test_input_is_unchanged_and_later_mutations_do_not_change_models(self):
        values = [scenario(retirement_expenses=[expense(61, 61), expense(60, 60)])]
        original = deepcopy(values)
        result = map_what_if_scenarios(values)
        self.assertEqual(values, original)
        values[0]["scenario_id"] = "different"
        values[0]["changes"]["retirement_expenses"][0]["monthly_amount"] = 999
        values[0]["changes"]["retirement_expenses"].clear()
        values.clear()
        self.assertEqual(result, map_what_if_scenarios(original))

    def test_result_models_are_immutable_detached_and_hide_financial_repr(self):
        values = [scenario("private_identifier", monthly_savings=123456789,
                           retirement_expenses=[expense(amount=987654321)])]
        first, second = map_what_if_scenarios(values), map_what_if_scenarios(values)
        self.assertEqual(first, second)
        for left, right in ((first, second), (first[0], second[0]),
                            (first[0].changes, second[0].changes),
                            (first[0].changes.retirement_expenses, second[0].changes.retirement_expenses)):
            self.assertIsNot(left, right)
        row = first[0]
        for obj, key, value in ((row, "scenario_id", "different"),
                                (row.changes, "monthly_savings", 0),
                                (row.changes.retirement_expenses[0], "monthly_amount", 0)):
            with self.assertRaises(FrozenInstanceError):
                setattr(obj, key, value)
            self.assertNotIn("private_identifier", repr(obj))
            self.assertNotIn("123456789", repr(obj))
            self.assertNotIn("987654321", repr(obj))

    def test_later_failure_does_not_mutate_input_or_poison_next_call(self):
        values = [scenario("first", retirement_expenses=[expense()]), scenario("last", monthly_savings=-1)]
        original = deepcopy(values)
        self.assert_mapping_error(values, "invalid_value", "scenarios[1].changes.monthly_savings")
        self.assertEqual(values, original)
        self.assertEqual(map_what_if_scenarios(values[:1])[0].scenario_id, "first")

    def test_scenario_validation_precedence_is_stable(self):
        cases = (
            ([{"PRIVATE_KEY": 1}], "unknown_field", "scenarios[0]"),
            ([{}], "missing_field", "scenarios[0].scenario_id"),
            ([{"scenario_id": None}], "missing_field", "scenarios[0].changes"),
            ([{"scenario_id": None, "changes": None}], "invalid_value", "scenarios[0].scenario_id"),
            ([scenario("same"), {"scenario_id": "same", "changes": None}],
             "duplicate_id", "scenarios[1].scenario_id"),
            ([scenario(monthly_savings=-1), scenario("baseline")],
             "invalid_value", "scenarios[0].changes.monthly_savings"),
        )
        for values, code, path in cases:
            self.assert_mapping_error(values, code, path)

    def test_change_validation_precedence_ignores_key_insertion_order(self):
        values = [scenario(retirement_expenses=[], retirement_age=17, monthly_savings=-1)]
        self.assert_mapping_error(values, "invalid_value", "scenarios[0].changes.monthly_savings")
        values[0]["changes"]["monthly_savings"] = None
        self.assert_mapping_error(values, "invalid_value", "scenarios[0].changes.retirement_age")
        values[0]["changes"]["retirement_age"] = None
        self.assert_mapping_error(values, "missing_value", "scenarios[0].changes.retirement_expenses")

    def test_expense_validation_precedence_is_stable(self):
        path = "scenarios[0].changes.retirement_expenses[0]"
        for row, code, suffix in (
            ({"PRIVATE_KEY": 1}, "unknown_field", ""),
            ({}, "missing_field", ".from_age"),
            ({"from_age": 0}, "invalid_value", ".from_age"),
            ({"from_age": 60}, "missing_field", ".to_age"),
            ({"from_age": 60, "to_age": 59}, "invalid_value", ".to_age"),
            ({"from_age": 60, "to_age": 60}, "missing_field", ".monthly_amount"),
        ):
            self.assert_mapping_error([scenario(retirement_expenses=[row])], code, path + suffix)

    def test_errors_do_not_echo_values_or_display_model_exception_chaining(self):
        for values, path in (
            ([scenario("PRIVATE_RAW_ID")], "scenarios[0].scenario_id"),
            ([scenario(monthly_savings="PRIVATE_RAW_VALUE")], "scenarios[0].changes.monthly_savings"),
        ):
            error = self.assert_mapping_error(values, "invalid_value", path)
            self.assertIsNone(error.__cause__)
            self.assertTrue(error.__suppress_context__)
            self.assertNotIn("PRIVATE_RAW", str(error))
            self.assertNotIn("PRIVATE_RAW", repr(error))
            self.assertNotIn("ModelValidationError", "".join(traceback.format_exception(error)))

    def test_mapping_does_not_use_io_clock_rng_application_or_engines(self):
        state, stdout, stderr = random.getstate(), StringIO(), StringIO()
        blocked = (
            "builtins.open", "os.getenv", "socket.socket", "subprocess.run", "time.time",
            "random.Random", "random.random",
            "retirement_simulation.what_if.create_what_if_profile",
            "retirement_simulation.what_if_engine.create_what_if_profile",
            "retirement_simulation.what_if_engine.calculate_what_if",
            "retirement_simulation.what_if_engine.calculate_monte_carlo_paths",
            "retirement_simulation.what_if_engine.generate_return_paths",
            "retirement_simulation.monte_carlo.calculate_monte_carlo",
            "retirement_simulation.return_paths.generate_return_paths",
        )
        with ExitStack() as stack, redirect_stdout(stdout), redirect_stderr(stderr):
            guards = [stack.enter_context(patch(name, side_effect=AssertionError(name))) for name in blocked]
            result = map_what_if_scenarios([scenario(monthly_savings=0, retirement_expenses=[expense()])])
            self.assert_mapping_error([scenario("baseline")], "invalid_value", "scenarios[0].scenario_id")
        self.assertEqual(result[0].changes.monthly_savings, 0)
        for guard in guards:
            guard.assert_not_called()
        self.assertEqual(random.getstate(), state)
        self.assertEqual((stdout.getvalue(), stderr.getvalue()), ("", ""))

    def test_missing_argument_and_unknown_keyword_are_python_type_errors(self):
        with self.assertRaises(TypeError):
            map_what_if_scenarios()
        with self.assertRaises(TypeError):
            map_what_if_scenarios([], baseline=profile())

    def test_saved_input_and_new_mapper_feed_real_what_if_engine(self):
        raw = saved_data()
        original = deepcopy(raw)
        mapped = map_retirement_profile(raw)
        result = calculate_what_if(mapped.profile, map_simulation_assumptions(assumption_data()),
                                  map_what_if_scenarios([
                                      scenario("save_more", monthly_savings=1_000_000),
                                      scenario("unchanged"),
                                  ]))
        self.assertEqual(result.baseline.final_balance_percentiles.p50, -36_000_000)
        self.assertEqual(result.baseline.success_probability, 0)
        self.assertEqual(result.scenarios[0].result.final_balance_percentiles.p50, 24_000_000)
        self.assertEqual(result.scenarios[0].success_probability_delta_pp, 100)
        self.assertEqual(result.scenarios[1].result, result.baseline)
        self.assertEqual(result.scenarios[1].final_balance_delta.p50, 0)
        self.assertEqual(result.scenarios[1].success_probability_delta_pp, 0)
        self.assertEqual(raw, original)
        self.assertEqual(mapped, map_retirement_profile(original))

    def test_contract_one_year_example_matches_independent_hand_calculation(self):
        baseline = profile(current_age=60, end_age=60, liquid_assets=10_000, monthly_savings=0,
                           retirement_expenses=(RetirementExpensePeriod(**expense(60, 60, 1000)),),
                           pension_incomes=())
        config = map_simulation_assumptions(assumption_data())
        result = calculate_what_if(baseline, config, map_what_if_scenarios([
            scenario("lower_expenses", retirement_expenses=[expense(60, 60, 800)]),
        ]))
        changed = result.scenarios[0]
        self.assertEqual(result.baseline.final_balance_percentiles.p50, 10_000 - 12 * 1000)
        self.assertEqual(changed.result.final_balance_percentiles.p50, 10_000 - 12 * 800)
        self.assertEqual(changed.final_balance_delta.p50, 2400)
        self.assertEqual(changed.final_shortfall_delta.p50, -2000)
        self.assertEqual(changed.success_probability_delta_pp, 100)
        self.assertEqual(result.baseline.first_depletion_counts[0].count, 2)
        self.assertEqual(changed.result.first_depletion_counts[0].count, 0)


if __name__ == "__main__":
    unittest.main()
