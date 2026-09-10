import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase

from reports.models import Report

from .chart_payload import (
    ChartPayloadOwnershipError,
    build_chart_payload,
)


class FakeReports:
    def __init__(self, report):
        self.report = report

    def filter(self, **_criteria):
        return self

    def order_by(self, *_fields):
        return self

    def first(self):
        return self.report


def ready_report():
    return SimpleNamespace(
        id=701,
        report_type=Report.ReportType.LIFETIME_CASHFLOW,
        version=2,
        is_latest=True,
        status=Report.Status.READY,
        generated_at=datetime(2026, 9, 10, 1, 0, tzinfo=timezone.utc),
        content={
            'input_snapshot_digest': 'current-digest',
            'assumptions': {
                'calculation_policy_version': '2026-09-07.v1',
                'inflation_rate': 0.03,
                'investment_rate': 0.04,
                'wage_growth_rate': 0.03,
                'real_estate_growth_rate': 0.02,
                'sources': {
                    'inflation_rate': {'label': '승인 정책', 'url': None},
                    'investment_rate': {'label': '승인 정책', 'url': None},
                    'wage_growth_rate': {'label': '승인 정책', 'url': None},
                    'real_estate_growth_rate': {'label': '승인 정책', 'url': None},
                },
            },
            'depletion_point': {'is_depleted': False, 'depletion_age': None},
            'final_status': {
                'end_age': 61,
                'final_deficit': 0,
                'final_net_worth': 713_000_000,
            },
            'chart_data': [
                {
                    'age': 60,
                    'cash_inflow': 60_000_000,
                    'cash_outflow': -40_000_000,
                    'net_flow': 20_000_000,
                    'liquid_assets': 100_000_000,
                    'pension_assets': 200_000_000,
                    'real_estate_assets': 500_000_000,
                    'liabilities': 100_000_000,
                    'total_net_worth': 700_000_000,
                },
                {
                    'age': 61,
                    'cash_inflow': 36_000_000,
                    'cash_outflow': -45_000_000,
                    'net_flow': -9_000_000,
                    'liquid_assets': 95_000_000,
                    'pension_assets': 208_000_000,
                    'real_estate_assets': 510_000_000,
                    'liabilities': 100_000_000,
                    'total_net_worth': 713_000_000,
                },
            ],
        },
    )


def contract_fixture_payload():
    fixture_path = (
        Path(__file__).resolve().parents[2]
        / 'contract-fixtures'
        / 'ai-lifecycle'
        / 'v2'
        / 'chart-payloads.json'
    )
    fixture = json.loads(fixture_path.read_text(encoding='utf-8'))
    return fixture['cases']['lifetime_cashflow_ready']['django_ai_request_fragment']['chart_payload']


def fixture_backed_report():
    payload = contract_fixture_payload()
    return SimpleNamespace(
        id=payload['source_report']['report_id'],
        report_type=payload['source_report']['report_type'],
        version=payload['source_report']['version'],
        is_latest=True,
        status=Report.Status.READY,
        generated_at=datetime.fromisoformat(payload['generated_at'].replace('Z', '+00:00')),
        content={
            'input_snapshot_digest': 'current-digest',
            'assumptions': {
                **payload['assumptions'],
                'calculation_policy_version': '2026-09-07.v1',
            },
            'depletion_point': payload['summary']['depletion_point'],
            'final_status': payload['summary']['final_status'],
            'chart_data': payload['series'],
        },
    )


class ChartPayloadProviderTests(SimpleTestCase):
    def session(self, report):
        return SimpleNamespace(user_id=3, reports=FakeReports(report))

    @patch('consulting.chart_payload.calculation_input_digest', return_value='current-digest')
    def test_provider_output_matches_shared_contract_fixture(self, _digest):
        result = build_chart_payload(
            session=self.session(fixture_backed_report()),
            user=SimpleNamespace(id=3),
            step_id='lifetime-cashflow',
        )

        self.assertEqual(result.payload, contract_fixture_payload())

    @patch('consulting.chart_payload.calculation_input_digest', return_value='current-digest')
    def test_ready_report_becomes_verified_read_only_payload(self, _digest):
        result = build_chart_payload(
            session=self.session(ready_report()),
            user=SimpleNamespace(id=3),
            step_id='lifetime-cashflow',
        )

        self.assertIsNone(result.reason)
        self.assertTrue(result.payload['read_only'])
        self.assertEqual(result.payload['source_report']['report_id'], 701)
        self.assertEqual(result.payload['period'], {
            'dimension': 'age', 'from': 60, 'to': 61, 'interval': 1, 'unit': 'year',
        })
        self.assertEqual(result.payload['series'][1]['net_flow'], -9_000_000)

    @patch('consulting.chart_payload.calculation_input_digest', return_value='current-digest')
    def test_invalid_formula_is_rejected_before_ai(self, _digest):
        report = ready_report()
        report.content['chart_data'][1]['net_flow'] = 123

        result = build_chart_payload(
            session=self.session(report),
            user=SimpleNamespace(id=3),
            step_id='lifetime-cashflow',
        )

        self.assertIsNone(result.payload)
        self.assertEqual(result.reason, 'series_formula_invalid')

    def test_chart_report_is_bound_to_authenticated_owner(self):
        with self.assertRaises(ChartPayloadOwnershipError):
            build_chart_payload(
                session=self.session(ready_report()),
                user=SimpleNamespace(id=4),
                step_id='lifetime-cashflow',
            )
