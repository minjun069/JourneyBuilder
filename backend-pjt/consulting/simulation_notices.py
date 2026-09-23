"""User-facing explanations of the simulation adapter's existing scope notices."""

from collections.abc import Mapping
import re


_MESSAGES = {
    "partial_financial_scope": "이 시뮬레이션은 연결된 금융자산·연금·은퇴 후 생활비를 기준으로 계산합니다. 전체 재무상태는 다른 보고서와 함께 확인해 주세요.",
    "single_portfolio_assumption": "금융자산을 하나의 운용자산으로 묶어 같은 수익률 가정을 적용합니다. 자산별 운용 차이는 반영하지 않습니다.",
    "pre_retirement_cashflow_not_recalculated": "은퇴 전 적립에는 입력한 월 저축액을 사용하며 소득·지출 차이로 다시 계산하지 않습니다. 금융자산의 월 저축액이 실제 계획과 맞는지 확인해 주세요.",
    "pension_account_not_modeled": "연금 계좌의 적립금과 납입액은 운용자산에 합산하지 않습니다. 연금 재원은 입력한 예상 수령액과 수령 기간을 기준으로 확인해 주세요.",
    "real_estate_not_modeled": "부동산은 이번 시뮬레이션의 재원에 반영하지 않습니다. 부동산을 활용할 계획은 자산·부채와 현금흐름 보고서에서 함께 확인해 주세요.",
    "liabilities_not_modeled": "부채와 상환 일정은 이번 시뮬레이션에 반영하지 않습니다. 상환 부담은 부채와 현금흐름 보고서에서 별도로 확인해 주세요.",
    "milestones_not_modeled": "재무목표의 일회성 지출은 이번 시뮬레이션에 반영하지 않습니다. 해당 지출은 재무목표 보고서에서 함께 확인해 주세요.",
    "confirmed_empty_financial_assets": "금융자산이 없다고 확인한 입력을 사용했습니다. 금융자산을 추가했다면 입력을 갱신한 뒤 다시 계산해 주세요.",
    "confirmed_empty_pensions": "연금이 없다고 확인한 입력을 사용했습니다. 수령할 연금이 생겼다면 예상 수령액과 일정을 입력한 뒤 다시 계산해 주세요.",
    "other_financial_asset_not_modeled": "이 유형의 금융자산은 계산에 포함되지 않습니다. 해당 자산의 분류와 은퇴 재원 활용 계획을 확인해 주세요.",
    "pension_outside_horizon": "수령 기간이 계산 기간과 겹치지 않아 연금 수령액을 반영하지 않았습니다. 연금 일정과 계획 종료 연령을 확인해 주세요.",
    "pension_horizon_limited": "연금 수령액은 계획 종료 연령까지만 계산에 반영했습니다. 계획 종료 이후의 연금은 이 결과에 포함되지 않습니다.",
}
_ITEM_LABELS = {"financial_assets": "금융자산", "pension_assets": "연금자산"}


def simulation_notice_warning(notice: Mapping[str, object]) -> dict[str, str]:
    code = str(notice.get("code", ""))
    field_name = str(notice.get("field_name", ""))
    message = _MESSAGES.get(
        code,
        "시뮬레이션에 반영된 입력 범위를 확인할 수 없는 항목이 있습니다. 입력 내역과 계산 범위를 확인해 주세요.",
    )
    match = re.fullmatch(r"(financial_assets|pension_assets)\[([0-9]+)\]", field_name)
    if match:
        label = _ITEM_LABELS[match[1]]
        message = f"{label} {int(match[2]) + 1}번째 항목: {message}"
    return {"code": code, "field_name": field_name, "message": message}
