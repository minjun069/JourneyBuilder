from django.test import SimpleTestCase

from .chart_payload_adapters import PROJECTORS
from .test_chart_payload import ready_report


def category(label, total, ratio=0):
    return {'label': label, 'total': total, 'ratio': ratio, 'items': []}


class PresentationProjectionTests(SimpleTestCase):
    def test_nine_stored_report_steps_have_normal_projection(self):
        cases = {
            'balance-sheet': {
                'total_assets': 500, 'total_liabilities': 200, 'net_worth': 300,
                'asset_categories': {'cash': category('현금', 500, 100)},
                'liability_categories': {'loan': category('부채', 200, 100)},
            },
            'cash-flow': {
                'total_cash_inflow': 500, 'total_cash_outflow': 600,
                'net_cash_flow': -100,
                'inflow_categories': {'income': category('소득', 500, 100)},
                'outflow_categories': {'expense': category('지출', 600, 100)},
            },
            'financial-indicators': {
                'household_balance': 80.0,
                'total_savings_propensity': 20.0,
                'emergency_reserve': 3.0,
                'insurance_preparation': 9.0,
                'debt_service_ratio': 15.0,
                'financial_asset_ratio': 50.0,
                'retirement_savings': 55.0,
            },
            'financial-goal-result': {
                'pre_retirement': {
                    'desired_expenses': [{
                        'from_age': 40, 'to_age': 41,
                        'current_total': 100, 'future_total': 120,
                    }],
                    'expected_expenses': [],
                    'achieved_funds': [],
                },
                'post_retirement_phases': [{
                    'phase_name': '활동기', 'start_age': 60, 'end_age': 70,
                    'desired_expenses': [], 'expected_expenses': [],
                    'achieved_funds': [],
                }],
            },
            'retirement-fund': {
                'total_required_fund': 1_000,
                'total_prepared_fund': 400,
                'shortfall': 600,
                'phase_details': [{
                    'phase_name': '활동기', 'duration_years': 10,
                    'monthly_required': 5, 'annual_required': 60,
                    'total_required': 1_000,
                }],
            },
            'retirement-readiness': {
                'current_age': 59, 'retirement_age': 60,
                'total_retirement_assets': 440,
                'retirement_income_sources': {
                    'severance_pay_estimated': 40,
                    'public_pension_monthly': 2,
                    'rental_income_annual': 3,
                    'reverse_mortgage_annual': 0,
                },
                'projections_by_age': [
                    {'age': 59, 'financial': 100, 'real_estate': 200, 'pension': 50, 'total': 350, 'yearly_growth': 0},
                    {'age': 60, 'financial': 120, 'real_estate': 220, 'pension': 60, 'total': 400, 'yearly_growth': 50},
                ],
            },
            'pension-schedule': {
                'results': {
                    'estimate_method': 'user_input',
                    'pension_start_age': 60,
                    'final_monthly_est': 10,
                },
                'yearly_schedule': [
                    {'age': 60, 'year': 2030, 'monthly_amount': 10, 'annual_amount': 120},
                    {'age': 61, 'year': 2031, 'monthly_amount': 10, 'annual_amount': 121},
                ],
            },
            'lifetime-cashflow': ready_report().content,
            'required-savings': {
                'current_monthly_savings': 100,
                'target_monthly_savings': 150,
                'additional_required_savings': 50,
                'retirement_gap': 1_000,
                'simulation_base_data': {
                    'current_age': 40, 'base_retirement_age': 60,
                    'original_shortfall': 1_000,
                },
            },
        }

        self.assertEqual(set(cases), set(PROJECTORS))
        for step_id, content in cases.items():
            with self.subTest(step_id=step_id):
                projected = PROJECTORS[step_id](content)
                self.assertLessEqual(len(projected['highlights']), 3)
                self.assertIn('period', projected)
                self.assertIn('units', projected)
                self.assertIn('warnings', projected)
                for highlight in projected['highlights']:
                    self.assertEqual(
                        highlight['provenance']['source'],
                        'verified_payload',
                    )

    def test_deficit_and_zero_values_remain_exact(self):
        projected = PROJECTORS['cash-flow']({
            'total_cash_inflow': 0,
            'total_cash_outflow': 100,
            'net_cash_flow': -100,
            'inflow_categories': {'income': category('소득', 0)},
            'outflow_categories': {'expense': category('지출', 100)},
        })

        self.assertEqual(projected['summary']['net_cash_flow'], -100)
        self.assertEqual(
            {warning['code'] for warning in projected['warnings']},
            {'monthly_deficit'},
        )

    def test_lifetime_detects_multiple_deficit_transitions_and_depletion(self):
        content = ready_report().content
        first, second = content['chart_data']
        content['chart_data'] = [
            first,
            second,
            {
                **second,
                'age': 62,
                'cash_inflow': 50_000_000,
                'cash_outflow': -40_000_000,
                'net_flow': 10_000_000,
                'liquid_assets': 10_000_000,
                'pension_assets': 210_000_000,
                'real_estate_assets': 520_000_000,
                'total_net_worth': 640_000_000,
            },
            {
                **second,
                'age': 63,
                'cash_inflow': 10_000_000,
                'cash_outflow': -40_000_000,
                'net_flow': -30_000_000,
                'liquid_assets': -20_000_000,
                'pension_assets': 220_000_000,
                'real_estate_assets': 530_000_000,
                'total_net_worth': 630_000_000,
            },
        ]
        content['depletion_point'] = {'is_depleted': True, 'depletion_age': 63}
        content['final_status'] = {
            'end_age': 63,
            'final_deficit': 20_000_000,
            'final_net_worth': 630_000_000,
        }
        content['explanation_data']['components_by_age'].extend([
            {
                'age': 62,
                'cash_inflow': {'pension_income': 50_000_000},
                'cash_outflow': {'retirement_living_expenses': 40_000_000},
            },
            {
                'age': 63,
                'cash_inflow': {'pension_income': 10_000_000},
                'cash_outflow': {'retirement_living_expenses': 40_000_000},
            },
        ])

        projected = PROJECTORS['lifetime-cashflow'](content)
        event_types = [event['type'] for event in projected['events']]
        self.assertEqual(event_types.count('cashflow_deficit_start'), 2)
        self.assertIn('asset_depletion', event_types)
