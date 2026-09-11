"""저장 스냅샷을 시뮬레이션 모듈 입력으로 바꾸는 순수 변환 (S15P21B109-136).

Django·ORM·HTTP 를 import 하지 않는다. 입력은 reports.snapshots 가 만든 dict 이고
출력은 retirement_simulation.analyze_retirement() 에 그대로 넘길 dict 다.

## 왜 BE 가 한 번 더 검사하는가

모듈도 같은 규칙을 검사하지만 **첫 오류에서 중단하고** 메시지가 고정 영문
("Retirement input mapping failed.")이라 사용자에게 보여줄 수 없다. 여기서는
**문제를 전부 모아** 한국어 안내와 함께 돌려준다. 고칠 곳이 세 군데면 세 번
왕복하지 않고 한 번에 본다.

## 불변식

BE 사전 점검은 **모듈 규칙보다 넓어서는 안 된다.** 모듈이 받아주는 입력을 여기서
막으면 정상 사용자가 차단된다. 좁은 것은 괜찮다 — 그 경우 모듈이 잡고 뷰가
ANALYSIS_REJECTED 를 낸다. 그건 이 파일에 구멍이 있다는 신호다.

## 원본 불변

스냅샷을 읽기만 하고 새 dict/list 만 만든다. 원본 고객 정보는 수정하지 않는다.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
import re


# 모듈 input_adapter._ASSET_TYPES 와 같아야 한다.
ASSET_TYPES = ("예금/적금", "주식", "펀드/ETF", "채권", "가상자산")
# 모듈 models.MAX_AGE
MAX_AGE = 120
_PENSION_PERIOD = re.compile(r"[1-9][0-9]{0,2}년")

# 모듈이 실제로 읽는 키만 넘긴다. 상품명·가입일·원금 같은 원본 개인정보는
# 계산 경계를 넘지 않는다.
_BASIC_INFO_FIELDS = ("age", "retirement_age", "end_age")
_FINANCIAL_ASSET_FIELDS = ("asset_type", "current_value", "monthly_savings")
_PENSION_FIELDS = (
    "receive_method",
    "pension_start_age",
    "pension_period",
    "expected_amount",
)
# 모듈은 이 세 목록의 행 내부를 읽지 않고 비어 있는지만 본다(notices 생성용).
_PRESENCE_ONLY_SECTIONS = ("real_estate_assets", "liabilities", "goal_milestones")
# 비었으면 "정말 없음" 확인이 필요한 섹션.
_CONFIRMABLE_SECTIONS = ("financial_assets", "pension_assets")


@dataclass(frozen=True, slots=True)
class InputIssue:
    """사용자가 고칠 수 있는 입력 문제 하나.

    field 는 모듈과 같은 경로 문법을 쓴다(financial_assets[0].asset_type).
    message 에 원본 금액이나 상품명을 담지 않는다 — 로그·응답에 재무 원문이 새면 안 된다.
    hint 는 화면이 쓸 수 있는 정적 보조 정보다(허용 목록, 비어 있는 나이 구간 등).
    """

    code: str
    field: str
    message: str
    hint: dict | None = None


@dataclass(frozen=True, slots=True)
class SimulationInput:
    saved_data: dict = field(repr=False)
    confirmed_empty_sections: tuple[str, ...]
    issues: tuple[InputIssue, ...]

    @property
    def ok(self) -> bool:
        return not self.issues


def _rows(value: object) -> list[Mapping]:
    """Mapping 행만 남긴다. 스냅샷은 항상 list[dict] 라 실패는 프로그래밍 오류다."""
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    return [row for row in value if isinstance(row, Mapping)]


def _is_int(value: object) -> bool:
    # bool 은 int 서브클래스지만 금액·나이·횟수로는 유효하지 않다(모듈과 동일 판정).
    return isinstance(value, int) and not isinstance(value, bool)


def _text(value: object) -> str | None:
    """모듈 _text_field 와 같은 판정. 값이 없으면 None."""
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


class _Collector:
    def __init__(self) -> None:
        self.issues: list[InputIssue] = []

    def add(self, code: str, field_name: str, message: str, hint: dict | None = None) -> None:
        self.issues.append(InputIssue(code=code, field=field_name, message=message, hint=hint))

    def amount(
        self, row: Mapping, key: str, path: str, label: str, *, note: str = ""
    ) -> int | None:
        """0 이상 정수 금액. 없으면 issue 를 남기고 None."""
        value = row.get(key)
        if value is None:
            self.add("amount_missing", f"{path}.{key}", f"{label}을(를) 입력해 주세요.{note}")
            return None
        if not _is_int(value):
            self.add("amount_invalid", f"{path}.{key}", f"{label}은(는) 숫자로 입력해 주세요.")
            return None
        if value < 0:
            self.add("amount_invalid", f"{path}.{key}", f"{label}은(는) 0 이상이어야 합니다.")
            return None
        return value

    def age(
        self, row: Mapping, key: str, path: str, label: str, *, minimum: int, maximum: int
    ) -> int | None:
        value = row.get(key)
        if value is None:
            self.add("age_missing", f"{path}.{key}", f"{label}을(를) 입력해 주세요.")
            return None
        if not _is_int(value):
            self.add("age_invalid", f"{path}.{key}", f"{label}은(는) 숫자로 입력해 주세요.")
            return None
        if not minimum <= value <= maximum:
            self.add(
                "age_out_of_range",
                f"{path}.{key}",
                f"{label}은(는) {minimum}세에서 {maximum}세 사이여야 합니다.",
                {"minimum": minimum, "maximum": maximum},
            )
            return None
        return value


def _basic_info(snapshot: Mapping, out: dict, c: _Collector) -> tuple[int | None, int | None, int | None]:
    raw = snapshot.get("basic_info")
    if not isinstance(raw, Mapping):
        c.add(
            "basic_info_missing",
            "basic_info",
            "기본 정보(나이·은퇴 나이·기대 수명)를 먼저 입력해 주세요.",
        )
        return None, None, None

    age = c.age(raw, "age", "basic_info", "현재 나이", minimum=18, maximum=100)
    retirement_age = c.age(
        raw, "retirement_age", "basic_info", "은퇴 나이", minimum=18, maximum=100
    )
    end_age = c.age(raw, "end_age", "basic_info", "기대 수명", minimum=18, maximum=MAX_AGE)

    if end_age is not None and (age is not None or retirement_age is not None):
        floor = max(v for v in (age, retirement_age) if v is not None)
        if end_age < floor:
            c.add(
                "end_age_too_small",
                "basic_info.end_age",
                f"기대 수명은 현재 나이·은퇴 나이({floor}세)보다 크거나 같아야 합니다.",
            )

    out["basic_info"] = {
        key: raw.get(key) for key in _BASIC_INFO_FIELDS
    }
    return age, retirement_age, end_age


def _financial_assets(snapshot: Mapping, out: dict, c: _Collector) -> None:
    rows = _rows(snapshot.get("financial_assets"))
    mapped = []
    for index, row in enumerate(rows):
        path = f"financial_assets[{index}]"
        asset_type = _text(row.get("asset_type"))
        if asset_type is None:
            # '기타' 를 고르면 프론트가 저장값을 비우고 직접 입력 칸을 띄운다
            # (AddableTable.vue:32). 아무것도 적지 않으면 여기로 온다 — 제외가 아니라 미입력이다.
            c.add(
                "asset_type_missing",
                f"{path}.asset_type",
                "자산 유형을 입력해 주세요.",
                {"allowed": list(ASSET_TYPES)},
            )
        elif asset_type not in ASSET_TYPES:
            # 임시 차단. 모듈 0.1.2 가 5종 밖 값을 '기타' 와 같이 행 단위로 제외하도록
            # 넓힌다(모듈 담당 회신 2026-09-11, 저장소 반영 대기 — 지금 소스는 0.1.1 이라
            # 아직 거부한다). 반영되면 모듈 한계가 아니게 되고, 남은 이유는 화면 하나다
            # — 제외된 자산을 결과 화면이 알려주기 전에 통과시키면
            # 사용자는 자기 자산이 빠진 줄 모른 채 결과를 본다. 그 안내가 붙으면 이
            # 분기를 삭제한다(asset_type_missing 은 남긴다).
            c.add(
                "asset_type_unsupported",
                f"{path}.asset_type",
                "이번 시뮬레이션은 5개 자산 유형만 계산에 반영합니다. "
                "해당 자산의 유형을 선택해 주세요.",
                {"allowed": list(ASSET_TYPES)},
            )
        c.amount(row, "current_value", path, "평가금액")
        c.amount(
            row,
            "monthly_savings",
            path,
            "월 적립액",
            note=" 적립하지 않는다면 0을 입력해 주세요.",
        )
        mapped.append({key: row.get(key) for key in _FINANCIAL_ASSET_FIELDS})
    out["financial_assets"] = mapped


def _pension_assets(snapshot: Mapping, out: dict, c: _Collector) -> None:
    rows = _rows(snapshot.get("pension_assets"))
    mapped = []
    for index, row in enumerate(rows):
        path = f"pension_assets[{index}]"
        receive_method = _text(row.get("receive_method"))
        if receive_method is None:
            c.add(
                "pension_receive_method_missing",
                f"{path}.receive_method",
                "연금 수령 방식을 선택해 주세요.",
                {"allowed": ["연금형"]},
            )
        elif receive_method != "연금형":
            # 일시금은 월 현금흐름으로 환산할 수 없다. 조용히 제외하면 자산이 빠진 채
            # 성공확률이 나오므로 사용자에게 되돌린다.
            c.add(
                "pension_lump_sum_unsupported",
                f"{path}.receive_method",
                "일시금으로 받는 연금은 월 현금흐름으로 환산할 수 없어 반영하지 못합니다. "
                "연금형으로 수정하거나 목록에서 제외해 주세요.",
            )

        period = _text(row.get("pension_period"))
        if period is None:
            c.add(
                "pension_period_missing",
                f"{path}.pension_period",
                "연금 수령 기간을 입력해 주세요.",
                {"examples": ["종신", "10년", "20년"]},
            )
        elif period != "종신" and (
            _PENSION_PERIOD.fullmatch(period) is None or int(period[:-1]) > MAX_AGE
        ):
            c.add(
                "pension_period_unsupported",
                f"{path}.pension_period",
                "연금 수령 기간은 '종신' 또는 'N년' 형식으로 입력해 주세요.",
                {"examples": ["종신", "10년", "20년"]},
            )

        c.age(
            row, "pension_start_age", path, "연금 개시 나이", minimum=1, maximum=MAX_AGE
        )
        c.amount(row, "expected_amount", path, "예상 월 수령액")
        mapped.append({key: row.get(key) for key in _PENSION_FIELDS})
    out["pension_assets"] = mapped


def _contiguous(ages: list[int]) -> list[dict]:
    """연속한 나이를 구간으로 묶는다. [75,76,77,80] -> 75~77, 80~80."""
    ranges: list[dict] = []
    for age in ages:
        if ranges and age == ranges[-1]["to_age"] + 1:
            ranges[-1]["to_age"] = age
        else:
            ranges.append({"from_age": age, "to_age": age})
    return ranges


def coverage_gaps(horizon_start: int, end_age: int, ranges: list[tuple[int, int]]) -> list[dict]:
    """은퇴~기대 수명 중 생활비가 비어 있는 구간.

    모듈 RetirementProfile 이 같은 규칙을 검사하지만 실패 시 어느 연도인지 알려주지
    않는다. What-if 로 은퇴를 앞당길 때도 같은 검사가 필요해 공용으로 뺐다.
    """
    if end_age < horizon_start:
        return []
    covered: set[int] = set()
    for start, end in ranges:
        covered.update(range(start, end + 1))
    return _contiguous([a for a in range(horizon_start, end_age + 1) if a not in covered])


def overlapping_pairs(ranges: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """정렬 후 겹치는 구간 쌍의 (뒤 인덱스, 앞 인덱스). 모듈과 같은 판정이다."""
    indexed = sorted((start, end, index) for index, (start, end) in enumerate(ranges))
    found = []
    for (_, prev_end, prev_index), (start, _, index) in zip(indexed, indexed[1:]):
        if start <= prev_end:
            found.append((index, prev_index))
    return found


def describe_gaps(gaps: list[dict]) -> str:
    return ", ".join(f"{gap['from_age']}~{gap['to_age']}세" for gap in gaps)


def _goal_periods(
    snapshot: Mapping,
    out: dict,
    c: _Collector,
    age: int | None,
    retirement_age: int | None,
    end_age: int | None,
) -> None:
    rows = _rows(snapshot.get("goal_periods"))
    mapped = []
    after_ranges: list[tuple[int, int, int]] = []  # (from, to, row index)
    after_seen = False
    ranges_reliable = True

    for index, row in enumerate(rows):
        path = f"goal_periods[{index}]"
        phase = _text(row.get("phase"))
        if phase not in ("before", "after"):
            c.add(
                "expense_phase_unsupported",
                f"{path}.phase",
                "은퇴 전후 구분이 올바르지 않습니다.",
            )
            ranges_reliable = False
        items = _rows(row.get("items"))

        if phase == "after":
            after_seen = True
            start = c.age(row, "from_age", path, "시작 나이", minimum=1, maximum=MAX_AGE)
            end = c.age(row, "to_age", path, "종료 나이", minimum=1, maximum=MAX_AGE)
            if start is None or end is None:
                ranges_reliable = False
            elif start > end:
                c.add(
                    "expense_period_invalid_range",
                    f"{path}.to_age",
                    "종료 나이는 시작 나이보다 빠를 수 없습니다.",
                )
                ranges_reliable = False
            else:
                after_ranges.append((start, end, index))

            if not items:
                c.add(
                    "expense_period_items_missing",
                    f"{path}.items",
                    "이 구간의 생활비 항목을 입력해 주세요.",
                )
            else:
                for item_index, item in enumerate(items):
                    c.amount(
                        item, "amount", f"{path}.items[{item_index}]", "생활비 금액"
                    )

        mapped.append(
            {
                "phase": row.get("phase"),
                "from_age": row.get("from_age"),
                "to_age": row.get("to_age"),
                "items": [{"amount": item.get("amount")} for item in items],
            }
        )

    out["goal_periods"] = mapped

    if not after_seen:
        c.add(
            "expense_periods_missing",
            "goal_periods",
            "은퇴 후 생활비 구간을 입력해 주세요.",
        )
        return
    if not ranges_reliable:
        # 구간 값 자체가 깨져 있으면 겹침·공백 판정이 의미 없다. 먼저 고칠 것을 안내한다.
        return

    ordered = sorted(after_ranges)
    for (prev_start, prev_end, prev_index), (start, end, index) in zip(ordered, ordered[1:]):
        if start <= prev_end:
            c.add(
                "expense_periods_overlap",
                f"goal_periods[{index}]",
                "생활비 구간이 서로 겹칩니다. 한 살도 겹치지 않게 나눠 주세요.",
                {"overlaps_with": prev_index},
            )

    if age is None or retirement_age is None or end_age is None:
        return
    gaps = coverage_gaps(
        max(age, retirement_age), end_age, [(s, e) for s, e, _ in after_ranges]
    )
    if gaps:
        c.add(
            "expense_periods_gap",
            "goal_periods",
            f"{describe_gaps(gaps)} 구간의 은퇴 후 생활비가 비어 있습니다. "
            "은퇴부터 기대 수명까지 빠짐없이 채워 주세요.",
            {"missing": gaps},
        )


def build_simulation_input(
    snapshot: Mapping,
    *,
    confirmed_empty_sections: tuple[str, ...] = (),
) -> SimulationInput:
    """스냅샷을 모듈 입력으로 바꾸고 규격 위반을 전부 모은다.

    issues 가 비어 있지 않으면 호출부는 모듈을 부르지 않고 422 를 반환한다.
    """
    c = _Collector()
    out: dict = {}

    age, retirement_age, end_age = _basic_info(snapshot, out, c)
    _financial_assets(snapshot, out, c)
    _pension_assets(snapshot, out, c)
    _goal_periods(snapshot, out, c, age, retirement_age, end_age)

    for section in _PRESENCE_ONLY_SECTIONS:
        # 모듈은 목록이 비어 있는지만 보고 notices 를 만든다. 행 내부는 읽지 않으므로
        # 빈 dict 로 바꿔 원본 값이 계산 경계를 넘지 않게 한다.
        out[section] = [{} for _ in _rows(snapshot.get(section))]

    confirmed = tuple(s for s in _CONFIRMABLE_SECTIONS if s in confirmed_empty_sections)
    for section in _CONFIRMABLE_SECTIONS:
        if not out.get(section) and section not in confirmed:
            label = "금융자산" if section == "financial_assets" else "연금"
            c.add(
                f"{section}_empty",
                section,
                f"{label} 정보를 입력해 주세요.",
            )

    return SimulationInput(
        saved_data=out,
        confirmed_empty_sections=confirmed,
        issues=tuple(c.issues),
    )
