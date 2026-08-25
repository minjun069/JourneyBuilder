"""
save_report() 단위 테스트.
DB는 모킹하여 INSERT 동작, 날짜 계산, JSONB content 키 제외 로직을 검증.
"""
import json
from datetime import date, timedelta
from unittest.mock import MagicMock, call, patch


def _make_mock_conn(returned_id=42):
    mock_cursor = MagicMock()
    mock_cursor.fetchone.return_value = (returned_id,)
    mock_conn = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
    mock_conn.cursor.return_value.__exit__.return_value = False
    return mock_conn, mock_cursor


SAMPLE_CONTENT = {
    "summary": "이번 주 국민연금 개혁 논의가 활발했습니다.",
    "sections": [{"category": "정책", "headline": "연금법 개정", "detail": "국회 통과 예정"}],
    "action_items": ["연금 수령액 확인"],
}


# ---------------------------------------------------------------------------
# INSERT 동작 (UPSERT 아님)
# ---------------------------------------------------------------------------

def test_no_on_conflict_in_sql():
    """SQL에 ON CONFLICT가 없어야 한다 (UPSERT 아닌 순수 INSERT)."""
    mock_conn, mock_cursor = _make_mock_conn()
    with patch("generator.report_generator.get_connection", return_value=mock_conn):
        from generator.report_generator import save_report
        save_report(1, [1, 2, 3], SAMPLE_CONTENT)

    sql = mock_cursor.execute.call_args[0][0]
    assert "ON CONFLICT" not in sql


def test_returns_inserted_id():
    """저장 후 DB가 반환한 id를 그대로 반환해야 한다."""
    mock_conn, _ = _make_mock_conn(returned_id=99)
    with patch("generator.report_generator.get_connection", return_value=mock_conn):
        from generator.report_generator import save_report
        report_id = save_report(1, [1], SAMPLE_CONTENT)
    assert report_id == 99


def test_commit_called():
    """INSERT 후 conn.commit()이 호출되어야 한다."""
    mock_conn, _ = _make_mock_conn()
    with patch("generator.report_generator.get_connection", return_value=mock_conn):
        from generator.report_generator import save_report
        save_report(1, [1], SAMPLE_CONTENT)
    mock_conn.commit.assert_called_once()


# ---------------------------------------------------------------------------
# 날짜 계산 검증
# ---------------------------------------------------------------------------

def test_period_start_is_6_days_before_today():
    """period_start = 오늘 - 6일이어야 한다."""
    mock_conn, mock_cursor = _make_mock_conn()
    with patch("generator.report_generator.get_connection", return_value=mock_conn):
        from generator.report_generator import save_report
        save_report(1, [1], SAMPLE_CONTENT)

    params = mock_cursor.execute.call_args[0][1]
    # params 순서: user_id, period_start, period_end, summary, content, news_ids, run_date
    period_start = params[1]
    expected = date.today() - timedelta(days=6)
    assert period_start == expected


def test_period_end_is_today():
    """period_end = 오늘이어야 한다."""
    mock_conn, mock_cursor = _make_mock_conn()
    with patch("generator.report_generator.get_connection", return_value=mock_conn):
        from generator.report_generator import save_report
        save_report(1, [1], SAMPLE_CONTENT)

    params = mock_cursor.execute.call_args[0][1]
    period_end = params[2]
    assert period_end == date.today()


def test_run_date_is_today():
    """run_date = 오늘이어야 한다."""
    mock_conn, mock_cursor = _make_mock_conn()
    with patch("generator.report_generator.get_connection", return_value=mock_conn):
        from generator.report_generator import save_report
        save_report(1, [1], SAMPLE_CONTENT)

    params = mock_cursor.execute.call_args[0][1]
    run_date = params[6]
    assert run_date == date.today()


# ---------------------------------------------------------------------------
# JSONB content — summary 키 제외 검증
# ---------------------------------------------------------------------------

def test_summary_excluded_from_content_jsonb():
    """content JSONB에는 summary 키가 없어야 한다 (별도 컬럼에 저장)."""
    mock_conn, mock_cursor = _make_mock_conn()
    with patch("generator.report_generator.get_connection", return_value=mock_conn):
        from generator.report_generator import save_report
        save_report(1, [1], SAMPLE_CONTENT)

    params = mock_cursor.execute.call_args[0][1]
    content_json_str = params[4]
    content_dict = json.loads(content_json_str)
    assert "summary" not in content_dict


def test_sections_and_action_items_in_content_jsonb():
    """content JSONB에는 sections와 action_items가 포함되어야 한다."""
    mock_conn, mock_cursor = _make_mock_conn()
    with patch("generator.report_generator.get_connection", return_value=mock_conn):
        from generator.report_generator import save_report
        save_report(1, [1], SAMPLE_CONTENT)

    params = mock_cursor.execute.call_args[0][1]
    content_dict = json.loads(params[4])
    assert "sections" in content_dict
    assert "action_items" in content_dict


def test_summary_saved_to_separate_column():
    """summary 텍스트는 별도 컬럼 파라미터(params[3])로 전달되어야 한다."""
    mock_conn, mock_cursor = _make_mock_conn()
    with patch("generator.report_generator.get_connection", return_value=mock_conn):
        from generator.report_generator import save_report
        save_report(1, [1], SAMPLE_CONTENT)

    params = mock_cursor.execute.call_args[0][1]
    assert params[3] == SAMPLE_CONTENT["summary"]
