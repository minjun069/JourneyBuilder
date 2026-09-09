from __future__ import annotations

from copy import deepcopy
from datetime import date
import unittest
from unittest.mock import patch

from tools.financial_policy import (
    ApprovedFinancialPolicyError,
    ApprovedReferenceValueNotFound,
    load_approved_financial_policy,
    lookup_approved_reference_value,
)


class ApprovedFinancialPolicyTests(unittest.TestCase):
    def test_age_boundary_selects_one_exact_approved_value(self):
        reference = lookup_approved_reference_value(
            reference_id="debt_service_ratio_max",
            age=65,
            period="annual",
            population="retirewise_kr_households",
            effective_on=date(2026, 9, 9),
        )

        self.assertEqual(reference["value"], 0)
        self.assertEqual(reference["comparator"], "eq")
        self.assertEqual(reference["unit"], "percent")
        self.assertEqual(reference["source_version"], "1.1.0")
        self.assertEqual(reference["calculation_policy_version"], "2026-09-07.v1")

    def test_missing_age_period_or_population_match_is_not_interpolated(self):
        cases = (
            {"age": 19, "period": "annual", "population": "retirewise_kr_households"},
            {"age": 40, "period": "monthly", "population": "retirewise_kr_households"},
            {"age": 40, "period": "annual", "population": "another_population"},
        )
        for criteria in cases:
            with self.subTest(criteria=criteria), self.assertRaises(
                ApprovedReferenceValueNotFound
            ):
                lookup_approved_reference_value(
                    reference_id="debt_service_ratio_max",
                    effective_on=date(2026, 9, 9),
                    **criteria,
                )

    def test_expired_policy_and_broken_provenance_fail_closed(self):
        policy = deepcopy(load_approved_financial_policy())
        policy["valid_until"] = "2026-09-08"
        with patch(
            "tools.financial_policy.load_approved_financial_policy",
            return_value=policy,
        ), self.assertRaises(ApprovedFinancialPolicyError):
            lookup_approved_reference_value(
                reference_id="debt_service_ratio_max",
                age=40,
                period="annual",
                population="retirewise_kr_households",
                effective_on=date(2026, 9, 9),
            )

        policy = deepcopy(load_approved_financial_policy())
        for source in policy["sources"]:
            if source["source_id"] == "retirewise-household-financial-indicator-policy":
                source["as_of"] = "2026-09-06"
        with patch(
            "tools.financial_policy.load_approved_financial_policy",
            return_value=policy,
        ), self.assertRaises(ApprovedFinancialPolicyError):
            lookup_approved_reference_value(
                reference_id="debt_service_ratio_max",
                age=40,
                period="annual",
                population="retirewise_kr_households",
                effective_on=date(2026, 9, 9),
            )


if __name__ == "__main__":
    unittest.main()
