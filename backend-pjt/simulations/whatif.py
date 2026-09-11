"""요청 본문의 변경안을 모듈 시나리오로 바꾼다 (S15P21B109-138).

Django 를 import 하지 않는 순수 변환이다.

## 두 종류의 오류를 구분한다

- **형식 오류**(허용하지 않는 키, 잘못된 타입) → 400. 요청을 만든 쪽이 잘못했다
- **적용 불가**(변경 후 생활비에 빈 구간이 생김) → 422. 사용자가 화면에서 고칠 수 있다

두 번째가 이 파일의 핵심이다. 은퇴를 앞당기면 **새로 생긴 은퇴 기간도 채워야 하는데**,
모듈은 실패 시 invalid_profile 만 주고 어느 연도가 비었는지 알려주지 않는다.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from .adapter import InputIssue, coverage_gaps, describe_gaps, overlapping_pairs
from .errors import SimulationError

MAX_AGE = 120
# 서버가 부여한다. 사용자 자유 제목을 계산 식별자로 쓰지 않는다.
SCENARIO_ID = 'comparison'
# 모듈 what_if_adapter._CHANGE_FIELDS 와 같아야 한다.
CHANGE_FIELDS = ('monthly_savings', 'retirement_age', 'retirement_expenses')
EXPENSE_FIELDS = ('from_age', 'to_age', 'monthly_amount')


def _invalid(message: str, details: dict | None = None) -> SimulationError:
    return SimulationError(400, 'REQUEST_INVALID', message, details=details)


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _int_or_400(value: object, label: str, *, minimum: int, maximum: int | None = None) -> int:
    if not _is_int(value):
        raise _invalid(f'{label}은(는) 정수여야 합니다.')
    if value < minimum or (maximum is not None and value > maximum):
        bound = f'{minimum}~{maximum}' if maximum is not None else f'{minimum} 이상'
        raise _invalid(f'{label}은(는) {bound} 범위여야 합니다.')
    return value


def _expenses(value: object) -> list[dict]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise _invalid('retirement_expenses 는 목록이어야 합니다.')
    if not value:
        # 빈 목록은 "생활비 없음" 이 아니라 잘못된 입력이다. 모듈도 missing_value 로 막는다.
        raise _invalid(
            'retirement_expenses 를 비워 보낼 수 없습니다. 변경하지 않으려면 생략해 주세요.'
        )
    rows = []
    for index, item in enumerate(value):
        path = f'changes.retirement_expenses[{index}]'
        if not isinstance(item, Mapping):
            raise _invalid(f'{path} 는 객체여야 합니다.')
        unknown = set(item) - set(EXPENSE_FIELDS)
        if unknown:
            raise _invalid(
                f'{path} 에 지원하지 않는 필드가 있습니다.',
                {'unsupported_fields': sorted(unknown)},
            )
        missing = [field for field in EXPENSE_FIELDS if field not in item]
        if missing:
            raise _invalid(f'{path} 에 필수 필드가 없습니다.', {'missing_fields': missing})
        start = _int_or_400(item['from_age'], f'{path}.from_age', minimum=1, maximum=MAX_AGE)
        end = _int_or_400(item['to_age'], f'{path}.to_age', minimum=1, maximum=MAX_AGE)
        if start > end:
            raise _invalid(f'{path} 의 종료 나이가 시작 나이보다 빠릅니다.')
        amount = _int_or_400(item['monthly_amount'], f'{path}.monthly_amount', minimum=0)
        rows.append({'from_age': start, 'to_age': end, 'monthly_amount': amount})
    return rows


def parse_changes(raw: object) -> dict:
    """요청 본문의 changes 를 모듈이 받는 형태로. 형식 위반은 400."""
    if not isinstance(raw, Mapping):
        raise _invalid('changes 는 객체여야 합니다.')
    unknown = set(raw) - set(CHANGE_FIELDS)
    if unknown:
        raise _invalid(
            '지원하지 않는 변경 항목이 있습니다.',
            {'unsupported_fields': sorted(unknown), 'supported': list(CHANGE_FIELDS)},
        )
    if not any(raw.get(field) is not None for field in CHANGE_FIELDS):
        raise _invalid(
            '변경할 항목을 최소 하나 지정해 주세요.', {'supported': list(CHANGE_FIELDS)}
        )

    changes: dict = {}
    # 생략과 null 은 "유지" 다. 0 은 유효한 변경이라 구분해야 한다.
    if raw.get('monthly_savings') is not None:
        changes['monthly_savings'] = _int_or_400(
            raw['monthly_savings'], 'monthly_savings', minimum=0
        )
    if raw.get('retirement_age') is not None:
        changes['retirement_age'] = _int_or_400(
            raw['retirement_age'], 'retirement_age', minimum=18, maximum=100
        )
    if raw.get('retirement_expenses') is not None:
        changes['retirement_expenses'] = _expenses(raw['retirement_expenses'])
    return changes


def _baseline_ages(saved_data: Mapping) -> tuple[int, int, int]:
    info = saved_data['basic_info']
    return info['age'], info['retirement_age'], info['end_age']


def _baseline_expense_ranges(saved_data: Mapping) -> list[tuple[int, int]]:
    return [
        (row['from_age'], row['to_age'])
        for row in saved_data['goal_periods']
        if row.get('phase') == 'after'
    ]


def check_applicable(changes: Mapping, saved_data: Mapping) -> None:
    """변경을 적용했을 때 모듈이 받아들일 수 있는지 미리 본다. 불가면 422."""
    current_age, base_retirement_age, end_age = _baseline_ages(saved_data)
    issues: list[InputIssue] = []

    retirement_age = changes.get('retirement_age', base_retirement_age)
    if 'retirement_age' in changes and retirement_age != base_retirement_age:
        if base_retirement_age < current_age:
            issues.append(
                InputIssue(
                    code='retirement_age_already_past',
                    field='changes.retirement_age',
                    message='이미 은퇴한 상태에서는 은퇴 나이를 변경할 수 없습니다.',
                )
            )
        elif retirement_age < current_age:
            issues.append(
                InputIssue(
                    code='retirement_age_before_current_age',
                    field='changes.retirement_age',
                    message=f'은퇴 나이는 현재 나이({current_age}세)보다 빠를 수 없습니다.',
                )
            )

    if 'retirement_expenses' in changes:
        ranges = [(row['from_age'], row['to_age']) for row in changes['retirement_expenses']]
        field = 'changes.retirement_expenses'
    else:
        ranges = _baseline_expense_ranges(saved_data)
        field = 'changes.retirement_age'

    for index, other in overlapping_pairs(ranges):
        issues.append(
            InputIssue(
                code='expense_periods_overlap',
                field=f'{field}[{index}]' if 'expenses' in field else field,
                message='생활비 구간이 서로 겹칩니다. 한 살도 겹치지 않게 나눠 주세요.',
                hint={'overlaps_with': other},
            )
        )

    if not issues:
        gaps = coverage_gaps(max(current_age, retirement_age), end_age, ranges)
        if gaps:
            # 은퇴를 앞당기면 새로 생긴 기간도 채워야 한다. 문서가 지적한 함정이다.
            issues.append(
                InputIssue(
                    code='expense_periods_gap',
                    field=field,
                    message=(
                        f'{describe_gaps(gaps)} 구간의 생활비가 비어 있습니다. '
                        '변경 후 은퇴 시점부터 기대 수명까지 빠짐없이 채워 주세요.'
                    ),
                    hint={'missing': gaps},
                )
            )

    if issues:
        raise SimulationError(
            422,
            'CHANGES_NOT_APPLICABLE',
            '변경안을 적용할 수 없습니다. 아래 항목을 확인해 주세요.',
            details={
                'issues': [
                    {
                        'code': issue.code,
                        'field': issue.field,
                        'message': issue.message,
                        **({'hint': issue.hint} if issue.hint is not None else {}),
                    }
                    for issue in issues
                ]
            },
        )


def build_scenario(raw_changes: object, saved_data: Mapping) -> dict:
    changes = parse_changes(raw_changes)
    check_applicable(changes, saved_data)
    return {'scenario_id': SCENARIO_ID, 'changes': changes}
