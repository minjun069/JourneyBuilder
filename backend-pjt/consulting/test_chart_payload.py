import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase

from reports.models import Report
from reports.policy import load_financial_policy

from .chart_payload import (
    PRESENTATION_CHART_STEP_IDS,
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


class MappedReports:
    def __init__(self, reports):
        self.reports = reports
        self.selected = None

    def filter(self, **criteria):
        selected = self.reports.get(criteria.get('report_type'))
        return FakeReports(selected)


def ready_report():
    policy = load_financial_policy()
    return SimpleNamespace(
        id=701,
        report_type=Report.ReportType.LIFETIME_CASHFLOW,
        version=2,
        is_latest=True,
        status=Report.Status.READY,
        generated_at=datetime(2026, 9, 10, 1, 0, tzinfo=timezone.utc),
        content={
            'input_snapshot_digest': 'current-digest',
            'calculation_policy_version': policy['policy_version'],
            'assumptions': {
                **policy['assumptions'],
                'calculation_policy_version': policy['policy_version'],
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
            'explanation_data': {
                'events': [
                    {'type': 'retirement_start', 'age': 60},
                    {'type': 'pension_start', 'age': 61, 'annual_amount': 36_000_000},
                ],
                'components_by_age': [
                    {
                        'age': 60,
                        'cash_inflow': {'earned_and_other_income': 60_000_000},
                        'cash_outflow': {'retirement_living_expenses': 40_000_000},
                    },
                    {
                        'age': 61,
                        'cash_inflow': {'pension_income': 36_000_000, 'rental_income': 0},
                        'cash_outflow': {'retirement_living_expenses': 45_000_000},
                    },
                ],
            },
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
    source = ready_report()
    return SimpleNamespace(
        id=payload['source_report']['report_id'],
        report_type=payload['source_report']['report_type'],
        version=payload['source_report']['version'],
        is_latest=True,
        status=Report.Status.READY,
        generated_at=datetime.fromisoformat(payload['generated_at'].replace('Z', '+00:00')),
        content=source.content,
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
        self.assertEqual(result.payload['schema_version'], '2.1')
        self.assertEqual(result.payload['status'], 'ready')
        self.assertEqual(result.payload['source_report']['report_id'], 701)
        self.assertEqual(result.payload['period'], {
            'dimension': 'age', 'from': 60, 'to': 61, 'interval': 1, 'unit': 'year',
        })
        self.assertEqual(result.payload['series'][1]['net_flow'], -9_000_000)
        self.assertEqual(result.payload['events'][-1]['type'], 'pension_start')
        self.assertEqual(result.payload['assumption_details'][0]['display'], '3%')

    @patch('consulting.chart_payload.calculation_input_digest', return_value='current-digest')
    def test_invalid_formula_is_rejected_before_ai(self, _digest):
        report = ready_report()
        report.content['chart_data'][1]['net_flow'] = 123

        result = build_chart_payload(
            session=self.session(report),
            user=SimpleNamespace(id=3),
            step_id='lifetime-cashflow',
        )

        self.assertEqual(result.payload['status'], 'guidance_only')
        self.assertFalse(result.payload['availability']['numeric_interpretation_allowed'])
        self.assertEqual(result.reason, 'formula_invalid')

    @patch('consulting.chart_payload.calculation_input_digest', return_value='current-digest')
    def test_same_policy_version_with_wrong_value_is_guidance_only(self, _digest):
        report = ready_report()
        report.content['assumptions']['inflation_rate'] = 0.026

        result = build_chart_payload(
            session=self.session(report),
            user=SimpleNamespace(id=3),
            step_id='lifetime-cashflow',
        )

        self.assertEqual(result.reason, 'policy_value_mismatch')
        self.assertEqual(result.payload['status'], 'guidance_only')
        self.assertEqual(result.payload['highlights'], [])

    @patch('consulting.chart_payload.calculation_input_digest', return_value='current-digest')
    def test_missing_report_keeps_purpose_only_contract(self, _digest):
        result = build_chart_payload(
            session=self.session(None),
            user=SimpleNamespace(id=3),
            step_id='cash-flow',
        )

        self.assertEqual(result.reason, 'report_not_found')
        self.assertEqual(result.payload['chart_id'], 'cash-flow')
        self.assertEqual(result.payload['status'], 'guidance_only')

    def test_all_ten_presentation_steps_are_registered(self):
        self.assertEqual(PRESENTATION_CHART_STEP_IDS, (
            'balance-sheet', 'cash-flow', 'financial-indicators',
            'financial-goal-result', 'retirement-fund',
            'retirement-readiness', 'retirement-simulation',
            'pension-schedule', 'lifetime-cashflow', 'required-savings',
        ))

    @patch('consulting.chart_payload.calculation_input_digest', return_value='current-digest')
    def test_connections_include_only_latest_ready_compatible_snapshot(self, _digest):
        policy = load_financial_policy()
        cash_report = SimpleNamespace(
            id=702,
            report_type=Report.ReportType.CASH_FLOW,
            version=1,
            is_latest=True,
            status=Report.Status.READY,
            generated_at=datetime(2026, 9, 10, 1, 0, tzinfo=timezone.utc),
            content={
                'input_snapshot_digest': 'current-digest',
                'calculation_policy_version': policy['policy_version'],
                'assumptions': {
                    **policy['assumptions'],
                    'calculation_policy_version': policy['policy_version'],
                    'sources': {},
                },
                'total_cash_inflow': 500,
                'total_cash_outflow': 400,
                'net_cash_flow': 100,
                'inflow_categories': {
                    'income': {'label': '소득', 'total': 500, 'ratio': 100, 'items': []},
                },
                'outflow_categories': {
                    'expense': {'label': '지출', 'total': 400, 'ratio': 100, 'items': []},
                },
            },
        )
        reports = MappedReports({
            Report.ReportType.LIFETIME_CASHFLOW: ready_report(),
            Report.ReportType.CASH_FLOW: cash_report,
        })
        session = SimpleNamespace(user_id=3, reports=reports)

        result = build_chart_payload(
            session=session,
            user=SimpleNamespace(id=3),
            step_id='lifetime-cashflow',
        )
        self.assertEqual(
            [connection['step_id'] for connection in result.payload['connections']],
            ['cash-flow'],
        )

        cash_report.content['input_snapshot_digest'] = 'older-digest'
        stale_result = build_chart_payload(
            session=session,
            user=SimpleNamespace(id=3),
            step_id='lifetime-cashflow',
        )
        self.assertEqual(stale_result.payload['connections'], [])

    @patch('consulting.chart_payload.calculation_input_digest', return_value='current-digest')
    @patch('simulations.services.package_version', return_value='1.2.3')
    @patch('simulations.services.run_analysis')
    @patch('simulations.services.prepare_input', return_value=object())
    @patch('simulations.services.load_consistent_snapshot', return_value=({}, 'current-digest'))
    @patch('simulations.services.load_policy')
    def test_retirement_simulation_is_recomputed_and_verified_by_backend(
        self, load_policy, _snapshot, _prepare, run_analysis, _package, _digest,
    ):
        assumptions = {
            'annual_return_mean': 0.04,
            'annual_return_volatility': 0.1,
            'annual_inflation_rate': 0.02,
            'num_paths': 100,
            'seed': 42,
        }
        load_policy.return_value = (assumptions, 'simulation-2026-v1')
        run_analysis.return_value = ({
            'contract_version': 'retirement-analysis-v1',
            'mapping_version': 'retirement-input-v1',
            'mode': 'baseline',
            'units': {
                'amount': 'KRW', 'monthly_amount': 'KRW/month',
                'probability': 'fraction', 'probability_delta': 'pp',
                'annual_rate': 'fraction', 'age': 'years',
            },
            'notices': [],
            'baseline_profile': {
                'current_age': 60, 'retirement_age': 60, 'end_age': 61,
                'retirement_expenses': [], 'pension_incomes': [],
            },
            'metadata': {'assumptions': assumptions},
            'baseline': {
                'success_probability': 0.75,
                'final_balance_percentiles': {'p10': -10, 'p50': 20, 'p90': 40},
                'final_shortfall_percentiles': {'p10': 0, 'p50': 0, 'p90': 10},
                'asset_percentiles': [
                    {'age': 60, 'balance': {'p10': 10, 'p50': 20, 'p90': 30}},
                    {'age': 61, 'balance': {'p10': -10, 'p50': 20, 'p90': 40}},
                ],
                'first_depletion_counts': [
                    {'age': 60, 'count': 0}, {'age': 61, 'count': 25},
                ],
                'num_paths': 100,
            },
        }, 10)

        result = build_chart_payload(
            session=self.session(None),
            user=SimpleNamespace(id=3),
            step_id='retirement-simulation',
        )

        self.assertIsNone(result.reason)
        self.assertEqual(result.payload['status'], 'ready')
        self.assertEqual(result.payload['highlights'][0]['display'], '75.0%')
        self.assertEqual(result.payload['events'][0]['age'], 61)
        self.assertEqual(result.payload['assumption_details'][0]['display'], '4%')

    def test_chart_report_is_bound_to_authenticated_owner(self):
        with self.assertRaises(ChartPayloadOwnershipError):
            build_chart_payload(
                session=self.session(ready_report()),
                user=SimpleNamespace(id=4),
                step_id='lifetime-cashflow',
            )
