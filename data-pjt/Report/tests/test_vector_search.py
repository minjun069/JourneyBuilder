"""
vector_search.py 단위 테스트.
외부 의존성(PostgreSQL, Gemini API)은 모두 모킹.
"""
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

MOCK_ROWS = [
    (1, "국민연금 개혁", "https://news.example.com/1", datetime(2026, 6, 20), "정책", 0.92),
    (2, "퇴직연금 수익률", "https://news.example.com/2", datetime(2026, 6, 21), "재테크", 0.85),
]
MOCK_EMBEDDING = [0.1] * 1536


def _make_mock_conn(rows=MOCK_ROWS):
    mock_cursor = MagicMock()
    mock_cursor.fetchall.return_value = rows
    mock_conn = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
    mock_conn.cursor.return_value.__exit__.return_value = False
    return mock_conn, mock_cursor


# ---------------------------------------------------------------------------
# 반환 형식 검증
# ---------------------------------------------------------------------------

def test_returns_list_of_dicts():
    mock_conn, _ = _make_mock_conn()
    with (
        patch("search.vector_search.get_connection", return_value=mock_conn),
        patch("search.vector_search._embed_query", return_value=MOCK_EMBEDDING),
    ):
        from search.vector_search import vector_search
        results = vector_search("국민연금")
    assert isinstance(results, list)
    assert all(isinstance(r, dict) for r in results)


def test_result_has_required_keys():
    mock_conn, _ = _make_mock_conn()
    with (
        patch("search.vector_search.get_connection", return_value=mock_conn),
        patch("search.vector_search._embed_query", return_value=MOCK_EMBEDDING),
    ):
        from search.vector_search import vector_search
        results = vector_search("국민연금")
    required = {"id", "title", "link", "pub_date", "category", "score"}
    for r in results:
        assert required.issubset(r.keys())


def test_score_is_float():
    mock_conn, _ = _make_mock_conn()
    with (
        patch("search.vector_search.get_connection", return_value=mock_conn),
        patch("search.vector_search._embed_query", return_value=MOCK_EMBEDDING),
    ):
        from search.vector_search import vector_search
        results = vector_search("국민연금")
    assert all(isinstance(r["score"], float) for r in results)


# ---------------------------------------------------------------------------
# representative_ids 필터 동작
# ---------------------------------------------------------------------------

def test_representative_ids_uses_any_filter():
    """representative_ids 전달 시 SQL에 ANY 필터가 포함되는지 확인."""
    mock_conn, mock_cursor = _make_mock_conn()
    with (
        patch("search.vector_search.get_connection", return_value=mock_conn),
        patch("search.vector_search._embed_query", return_value=MOCK_EMBEDDING),
    ):
        from search.vector_search import vector_search
        vector_search("국민연금", representative_ids=[1, 2, 3])

    sql_called = mock_cursor.execute.call_args[0][0]
    assert "ANY" in sql_called


def test_no_representative_ids_no_any_filter():
    """representative_ids=None 시 SQL에 ANY 필터가 없어야 한다."""
    mock_conn, mock_cursor = _make_mock_conn()
    with (
        patch("search.vector_search.get_connection", return_value=mock_conn),
        patch("search.vector_search._embed_query", return_value=MOCK_EMBEDDING),
    ):
        from search.vector_search import vector_search
        vector_search("국민연금", representative_ids=None)

    sql_called = mock_cursor.execute.call_args[0][0]
    assert "ANY" not in sql_called


def test_representative_ids_passed_as_param():
    """representative_ids 값이 실제로 SQL 파라미터로 전달되는지 확인."""
    ids = [10, 20, 30]
    mock_conn, mock_cursor = _make_mock_conn()
    with (
        patch("search.vector_search.get_connection", return_value=mock_conn),
        patch("search.vector_search._embed_query", return_value=MOCK_EMBEDDING),
    ):
        from search.vector_search import vector_search
        vector_search("국민연금", representative_ids=ids)

    params = mock_cursor.execute.call_args[0][1]
    assert ids in params
