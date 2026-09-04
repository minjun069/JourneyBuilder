"""Self-tests for the P13 synthetic data-masking contract."""

from __future__ import annotations

import unittest

from tests.security.masking_contract import (
    ALL_SINKS,
    MASKING_SENTINELS,
    OBSERVABILITY_SINKS,
    DataClass,
    Sink,
    assert_no_raw_sentinels,
    find_raw_sentinel_leaks,
    sentinels_for_sink,
)


class MaskingFixtureQualityTests(unittest.TestCase):
    def test_every_project_data_class_has_a_sentinel(self) -> None:
        covered = {case.data_class for case in MASKING_SENTINELS}
        self.assertEqual(covered, set(DataClass))

    def test_case_ids_and_raw_values_are_unique(self) -> None:
        case_ids = [case.case_id for case in MASKING_SENTINELS]
        raw_values = [case.raw_value for case in MASKING_SENTINELS]
        self.assertEqual(len(case_ids), len(set(case_ids)))
        self.assertEqual(len(raw_values), len(set(raw_values)))

    def test_every_sink_has_an_unconditional_leak_sentinel(self) -> None:
        for sink in Sink:
            with self.subTest(sink=sink.value):
                self.assertTrue(sentinels_for_sink(sink))

    def test_direct_identifiers_are_forbidden_at_every_sink(self) -> None:
        direct_cases = (
            case
            for case in MASKING_SENTINELS
            if case.data_class is DataClass.DIRECT_IDENTIFIER
        )
        for case in direct_cases:
            with self.subTest(case_id=case.case_id):
                self.assertEqual(case.forbidden_sinks, ALL_SINKS)

    def test_financial_raw_values_are_forbidden_from_observability(self) -> None:
        financial_cases = (
            case
            for case in MASKING_SENTINELS
            if case.data_class is DataClass.PROJECT_HIGH_RISK_FINANCIAL
        )
        for case in financial_cases:
            with self.subTest(case_id=case.case_id):
                self.assertEqual(case.forbidden_sinks, OBSERVABILITY_SINKS)


class MaskingLeakAssertionTests(unittest.TestCase):
    def test_passthrough_payload_is_rejected_at_every_sink(self) -> None:
        for sink in Sink:
            payload = " | ".join(
                case.raw_value for case in sentinels_for_sink(sink)
            )
            with self.subTest(sink=sink.value):
                with self.assertRaisesRegex(AssertionError, sink.value):
                    assert_no_raw_sentinels(payload, sink)

    def test_safe_examples_pass_the_literal_leak_contract(self) -> None:
        payload = " | ".join(case.safe_example for case in MASKING_SENTINELS)
        for sink in Sink:
            with self.subTest(sink=sink.value):
                assert_no_raw_sentinels(payload, sink)

    def test_utf8_bytes_are_inspected(self) -> None:
        payload = b"trace=P13-REQUEST-RAW-0001"
        leaks = find_raw_sentinel_leaks(payload, Sink.TRACE_EXPORT)
        self.assertEqual(leaks, ("operational-request:P13-REQUEST-RAW-0001",))

    def test_formatted_financial_literal_is_detected_in_trace(self) -> None:
        payload = "monthly_income=7,310,000"
        leaks = find_raw_sentinel_leaks(payload, Sink.TRACE_EXPORT)
        self.assertIn("financial-income:7,310,000", leaks)

    def test_financial_model_use_is_left_for_allowlist_binding(self) -> None:
        payload = "monthly_income=P13-FIN-INCOME-7310000"
        self.assertEqual(find_raw_sentinel_leaks(payload, Sink.MODEL_INPUT), ())
        self.assertNotEqual(find_raw_sentinel_leaks(payload, Sink.APPLICATION_LOG), ())


if __name__ == "__main__":
    unittest.main()
