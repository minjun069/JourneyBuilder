from django.db import transaction
from django.utils.timezone import now
from .models import Report
from .calculators import (
    calculate_asset_statement,
    calculate_cash_flow,
    calculate_financial_goal_result,
    calculate_financial_indicators,
    calculate_lifetime_cashflow,
    calculate_pension_schedule,
    calculate_required_savings,
    calculate_retirement_fund,
    calculate_retirement_readiness,
)
from .policy import active_policy_version
from .snapshots import calculation_input_digest


# 동기 보고서 생성 — 자산현황표, 현금흐름표
def generate_sync_report(session, report_type):
    content = _build_report_content(report_type, session)

    return _create_new_version(
        session=session,
        report_type=report_type,
        status=Report.Status.READY,
        content=content,
        generated_at=now(),
    )


def _create_new_version(session, report_type, status, content=None, generated_at=None):
    with transaction.atomic():
        Report.objects.filter(
            session=session, report_type=report_type, is_latest=True,
        ).update(is_latest=False)

        last_version = (
            Report.objects.filter(session=session, report_type=report_type)
            .order_by('-version')
            .values_list('version', flat=True)
            .first()
        ) or 0

        return Report.objects.create(
            session=session,
            report_type=report_type,
            version=last_version + 1,
            is_latest=True,
            status=status,
            content=content,
            generated_at=generated_at,
        )


def _build_report_content(report_type, session):
    if report_type == Report.ReportType.ASSET_STATEMENT:
        return calculate_asset_statement(session)

    if report_type == Report.ReportType.CASH_FLOW:
        return calculate_cash_flow(session)

    if report_type == Report.ReportType.FINANCIAL_INDICATORS:
        asset_data = calculate_asset_statement(session)
        cash_flow_data = calculate_cash_flow(session)
        return calculate_financial_indicators(session, asset_data, cash_flow_data)

    if report_type == Report.ReportType.FINANCIAL_GOAL_RESULT:
        return calculate_financial_goal_result(session)

    if report_type == Report.ReportType.RETIREMENT_FUND:
        return calculate_retirement_fund(session)

    if report_type == Report.ReportType.RETIREMENT_READINESS:
        return calculate_retirement_readiness(session)

    if report_type == Report.ReportType.PENSION_SCHEDULE:
        return calculate_pension_schedule(session)

    if report_type == Report.ReportType.LIFETIME_CASHFLOW:
        content = calculate_lifetime_cashflow(session)
        if content:
            content['input_snapshot_digest'] = calculation_input_digest(session)
        return content

    if report_type == Report.ReportType.REQUIRED_SAVINGS:
        retirement_fund = calculate_retirement_fund(session)
        retirement_readiness = calculate_retirement_readiness(session)
        total_shortfall = max(
            0,
            retirement_fund.get('total_required_fund', 0)
            - retirement_readiness.get('total_retirement_assets', 0),
        )
        content = calculate_required_savings(
            session,
            total_shortfall,
        )
        content.update({
            'retirement_gap': total_shortfall,
            'calculation_policy_version': active_policy_version(),
            'input_snapshot_digest': calculation_input_digest(session),
        })
        return content

    raise ValueError(f'Unsupported report type: {report_type}')
