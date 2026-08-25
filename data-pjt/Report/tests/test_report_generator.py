"""
report_generator.py 단위 테스트.
DB, OpenAI API는 모두 모킹.
"""
import json
from unittest.mock import MagicMock, patch

import pytest


def _make_mock_conn(row=None):
    mock_cursor = MagicMock()
    mock_cursor.fetchone.return_value = row
    mock_conn = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
    mock_conn.cursor.return_value.__exit__.return_value = False
    return mock_conn


# ---------------------------------------------------------------------------
# _fetch_user_profile
# ---------------------------------------------------------------------------

def test_fetch_profile_returns_empty_dict_when_no_row():
    """DB에 해당 사용자 row가 없으면 빈 dict를 반환해야 한다."""
    mock_conn = _make_mock_conn(row=None)
    with patch("generator.report_generator.get_connection", return_value=mock_conn):
        from generator.report_generator import _fetch_user_profile
        result = _fetch_user_profile(9999)
    assert result == {}


def test_fetch_profile_returns_dict_with_expected_keys():
    """DB row가 있으면 필요한 키를 모두 포함한 dict를 반환해야 한다."""
    mock_row = (
        "홍길동", 1970, "은퇴 준비", 60,
        True, True, False,
        3000000, 1500000,
        ["국민연금"],  # interest_keywords
        ["정책"],      # interest_categories
    )
    mock_conn = _make_mock_conn(row=mock_row)
    with patch("generator.report_generator.get_connection", return_value=mock_conn):
        from generator.report_generator import _fetch_user_profile
        result = _fetch_user_profile(1)

    required = {
        "name", "birth_year", "goal_type", "target_retirement_age",
        "assets_pension", "assets_financial", "assets_real_estate",
        "monthly_income", "monthly_expense",
        "interest_keywords", "interest_categories",
    }
    assert required.issubset(result.keys())


# ---------------------------------------------------------------------------
# _derive_search_params
# ---------------------------------------------------------------------------

def test_derive_params_pension_assets_adds_keywords():
    """assets_pension=True 이면 '국민연금', '퇴직연금' 키워드가 포함되어야 한다."""
    from generator.report_generator import _derive_search_params
    profile = {"assets_pension": True, "assets_financial": False, "assets_real_estate": False}
    _, keywords, _ = _derive_search_params(profile)
    assert "국민연금" in keywords
    assert "퇴직연금" in keywords


def test_derive_params_financial_assets_adds_keywords():
    """assets_financial=True 이면 '주식', '펀드' 키워드가 포함되어야 한다."""
    from generator.report_generator import _derive_search_params
    profile = {"assets_pension": False, "assets_financial": True, "assets_real_estate": False}
    _, keywords, _ = _derive_search_params(profile)
    assert "주식" in keywords
    assert "펀드" in keywords


def test_derive_params_real_estate_adds_keywords():
    """assets_real_estate=True 이면 '부동산', '주택연금' 키워드가 포함되어야 한다."""
    from generator.report_generator import _derive_search_params
    profile = {"assets_pension": False, "assets_financial": False, "assets_real_estate": True}
    _, keywords, _ = _derive_search_params(profile)
    assert "부동산" in keywords
    assert "주택연금" in keywords


def test_derive_params_empty_categories_uses_all():
    """interest_categories가 비어있으면 ALL_CATEGORIES 전체를 사용해야 한다."""
    from generator.report_generator import _derive_search_params, ALL_CATEGORIES
    profile = {"interest_categories": []}
    _, _, categories = _derive_search_params(profile)
    assert categories == ALL_CATEGORIES


def test_derive_params_categories_from_preferences():
    """interest_categories가 설정되어 있으면 그 값을 사용해야 한다."""
    from generator.report_generator import _derive_search_params
    profile = {"interest_categories": ["정책", "재테크"]}
    _, _, categories = _derive_search_params(profile)
    assert categories == ["정책", "재테크"]


def test_derive_params_user_keywords_appended():
    """interest_keywords가 있으면 base_keywords 뒤에 추가되어야 한다."""
    from generator.report_generator import _derive_search_params
    profile = {"interest_keywords": ["특수목적법인", "연금개혁안"]}
    _, keywords, _ = _derive_search_params(profile)
    assert "특수목적법인" in keywords
    assert "연금개혁안" in keywords


def test_derive_params_no_duplicate_keywords():
    """user_keywords와 base_keywords에 동일 키워드가 있으면 중복 없이 한 번만 포함되어야 한다."""
    from generator.report_generator import _derive_search_params
    profile = {"assets_pension": True, "interest_keywords": ["국민연금"]}
    _, keywords, _ = _derive_search_params(profile)
    assert keywords.count("국민연금") == 1


# ---------------------------------------------------------------------------
# generate_report
# ---------------------------------------------------------------------------

def test_generate_report_returns_parsed_json():
    """LLM 응답이 유효한 JSON이면 dict로 파싱하여 반환해야 한다."""
    expected = {"summary": "요약", "sections": [], "action_items": ["행동1"]}
    mock_response = MagicMock()
    mock_response.choices[0].message.content = json.dumps(expected, ensure_ascii=False)

    mock_conn = _make_mock_conn(row=None)  # 빈 프로필

    with (
        patch("generator.report_generator.get_connection", return_value=mock_conn),
        patch("generator.report_generator._client") as mock_client,
    ):
        mock_client.chat.completions.create.return_value = mock_response
        from generator.report_generator import generate_report
        result = generate_report(1, [])

    assert result["summary"] == "요약"
    assert result["action_items"] == ["행동1"]


def test_generate_report_fallback_on_invalid_json():
    """LLM 응답이 JSON 파싱 실패 시 fallback dict를 반환해야 한다."""
    mock_response = MagicMock()
    mock_response.choices[0].message.content = "이것은 JSON이 아닙니다"

    mock_conn = _make_mock_conn(row=None)

    with (
        patch("generator.report_generator.get_connection", return_value=mock_conn),
        patch("generator.report_generator._client") as mock_client,
    ):
        mock_client.chat.completions.create.return_value = mock_response
        from generator.report_generator import generate_report
        result = generate_report(1, [])

    assert "summary" in result
    assert result["sections"] == []
    assert result["action_items"] == []
