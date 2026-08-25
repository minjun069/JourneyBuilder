"""
es_search.py 단위 테스트.
Elasticsearch 클라이언트는 모킹.
"""
from unittest.mock import MagicMock, patch

MOCK_HITS = [
    {
        "_score": 1.5,
        "_source": {
            "pg_id": 1,
            "title": "국민연금 개혁",
            "link": "https://news.example.com/1",
            "pub_date": "2026-06-20",
            "category": "정책",
        },
    },
    {
        "_score": 1.2,
        "_source": {
            "pg_id": 2,
            "title": "퇴직연금 수익률",
            "link": "https://news.example.com/2",
            "pub_date": "2026-06-21",
            "category": "재테크",
        },
    },
]


def _make_mock_client(hits=MOCK_HITS):
    mock_client = MagicMock()
    mock_client.search.return_value = {"hits": {"hits": hits}}
    return mock_client


# ---------------------------------------------------------------------------
# 반환 형식 검증
# ---------------------------------------------------------------------------

def test_returns_list_of_dicts():
    with patch("search.es_search._get_client", return_value=_make_mock_client()):
        from search.es_search import es_search
        results = es_search(["국민연금", "연금개혁"])
    assert isinstance(results, list)
    assert all(isinstance(r, dict) for r in results)


def test_result_has_required_keys():
    with patch("search.es_search._get_client", return_value=_make_mock_client()):
        from search.es_search import es_search
        results = es_search(["국민연금"])
    required = {"id", "title", "link", "pub_date", "category", "score"}
    for r in results:
        assert required.issubset(r.keys())


# ---------------------------------------------------------------------------
# 쿼리 빌드 검증
# ---------------------------------------------------------------------------

def test_keywords_joined_in_multi_match():
    """keywords 리스트가 공백으로 합쳐져 multi_match query에 포함되는지 확인."""
    mock_client = _make_mock_client()
    with patch("search.es_search._get_client", return_value=mock_client):
        from search.es_search import es_search
        es_search(["국민연금", "퇴직연금"])

    body = mock_client.search.call_args[1]["body"]
    multi_match_query = body["query"]["bool"]["must"][0]["multi_match"]["query"]
    assert "국민연금" in multi_match_query
    assert "퇴직연금" in multi_match_query


def test_date_range_filter_always_present():
    """날짜 필터(range.pub_date)는 categories/ids 여부와 무관하게 항상 포함."""
    mock_client = _make_mock_client()
    with patch("search.es_search._get_client", return_value=mock_client):
        from search.es_search import es_search
        es_search(["국민연금"])

    filters = mock_client.search.call_args[1]["body"]["query"]["bool"]["filter"]
    range_filters = [f for f in filters if "range" in f]
    assert len(range_filters) == 1
    assert "pub_date" in range_filters[0]["range"]


def test_category_filter_added_when_provided():
    """categories 전달 시 terms.category 필터가 추가되는지 확인."""
    mock_client = _make_mock_client()
    with patch("search.es_search._get_client", return_value=mock_client):
        from search.es_search import es_search
        es_search(["국민연금"], categories=["정책", "재테크"])

    filters = mock_client.search.call_args[1]["body"]["query"]["bool"]["filter"]
    terms_filters = [f for f in filters if "terms" in f]
    assert any("category" in f["terms"] for f in terms_filters)


def test_no_category_filter_when_none():
    """categories=None 시 terms 필터가 추가되지 않아야 한다."""
    mock_client = _make_mock_client()
    with patch("search.es_search._get_client", return_value=mock_client):
        from search.es_search import es_search
        es_search(["국민연금"], categories=None)

    filters = mock_client.search.call_args[1]["body"]["query"]["bool"]["filter"]
    terms_filters = [f for f in filters if "terms" in f]
    assert len(terms_filters) == 0


# ---------------------------------------------------------------------------
# representative_ids 필터 동작
# ---------------------------------------------------------------------------

def test_representative_ids_adds_ids_filter():
    """representative_ids 전달 시 ES ids 필터가 추가되는지 확인."""
    mock_client = _make_mock_client()
    with patch("search.es_search._get_client", return_value=mock_client):
        from search.es_search import es_search
        es_search(["국민연금"], representative_ids=[1, 2, 3])

    filters = mock_client.search.call_args[1]["body"]["query"]["bool"]["filter"]
    ids_filters = [f for f in filters if "ids" in f]
    assert len(ids_filters) == 1
    assert ids_filters[0]["ids"]["values"] == ["1", "2", "3"]


def test_no_ids_filter_when_representative_ids_none():
    """representative_ids=None 시 ids 필터가 없어야 한다."""
    mock_client = _make_mock_client()
    with patch("search.es_search._get_client", return_value=mock_client):
        from search.es_search import es_search
        es_search(["국민연금"], representative_ids=None)

    filters = mock_client.search.call_args[1]["body"]["query"]["bool"]["filter"]
    ids_filters = [f for f in filters if "ids" in f]
    assert len(ids_filters) == 0
