from unittest import TestCase

from consulting.simulation_notices import simulation_notice_warning


class SimulationNoticeTests(TestCase):
    def test_pension_limit_names_the_affected_row_and_retains_provenance(self):
        warning = simulation_notice_warning({
            "code": "pension_horizon_limited", "field_name": "pension_assets[1]",
        })
        self.assertEqual(warning["field_name"], "pension_assets[1]")
        self.assertEqual(warning["code"], "pension_horizon_limited")
        self.assertIn("연금자산 2번째 항목", warning["message"])
        self.assertIn("계획 종료 연령", warning["message"])
        self.assertNotIn("pension_assets", warning["message"])

    def test_confirmed_empty_is_not_described_as_missing_input(self):
        warning = simulation_notice_warning({
            "code": "confirmed_empty_pensions", "field_name": "pension_assets",
        })
        self.assertIn("없다고 확인한 입력", warning["message"])
        self.assertNotIn("누락", warning["message"])

    def test_notice_types_have_distinct_actionable_explanations(self):
        cases = {
            "pre_retirement_cashflow_not_recalculated": ("financial_assets", "월 저축액"),
            "real_estate_not_modeled": ("real_estate_assets", "부동산"),
            "liabilities_not_modeled": ("liabilities", "상환"),
            "pension_outside_horizon": ("pension_assets[0]", "수령 기간"),
        }
        messages = []
        for code, (field, subject) in cases.items():
            warning = simulation_notice_warning({"code": code, "field_name": field})
            self.assertIn(subject, warning["message"])
            self.assertIn("확인", warning["message"])
            messages.append(warning["message"])
        self.assertEqual(len(set(messages)), len(cases))

    def test_unknown_notice_does_not_echo_internal_content(self):
        warning = simulation_notice_warning({"code": "unknown-code", "field_name": "internal.field"})
        self.assertNotIn("internal.field", warning["message"])
        self.assertIn("확인할 수 없는 항목", warning["message"])
