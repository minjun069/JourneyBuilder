"""
hybrid_search.py 단위 테스트.
vector_search, es_search, DB는 모두 모킹하여 RRF 로직과 분기 조건만 검증.
"""
from unittest.mock import MagicMock, patch


def _news(link: str, title: str = "제목") -> dict:
    return {"id": hash(link), "title": title, "link": link,
            "pub_date": "2026-06-20", "category": "정책", "score": 1.0}


# ---------------------------------------------------------------------------
# _reciprocal_rank_fusion 로직 검증
# ---------------------------------------------------------------------------

def test_rrf_deduplication():
    """두 검색 결과에 동일 기사가 있으면 최종 결과에 한 번만 등장해야 한다."""
    news_a = _news("https://example.com/1")
    news_b = _news("https://example.com/2")
    news_c = _news("https://example.com/3")

    with (
        patch("search.hybrid_search._get_representative_ids", return_value=None),
        patch("search.hybrid_search.vector_search", return_value=[news_a, news_b]),
        patch("search.hybrid_search.es_search", return_value=[news_b, news_c]),
    ):
        from search.hybrid_search import hybrid_search
        results = hybrid_search("연금", ["연금"], top_k=10)

    links = [r["link"] for r in results]
    assert len(links) == len(set(links)), "중복 기사가 포함되었음"


def test_rrf_item_in_both_lists_ranks_higher():
    """두 검색 결과 모두에 등장한 기사는 한쪽에만 있는 기사보다 높은 순위여야 한다."""
    shared = _news("https://example.com/shared")
    only_vector = _news("https://example.com/vector-only")
    only_es = _news("https://example.com/es-only")

    with (
        patch("search.hybrid_search._get_representative_ids", return_value=None),
        patch("search.hybrid_search.vector_search", return_value=[only_vector, shared]),
        patch("search.hybrid_search.es_search", return_value=[only_es, shared]),
    ):
        from search.hybrid_search import hybrid_search
        results = hybrid_search("연금", ["연금"], top_k=10)

    links = [r["link"] for r in results]
    assert links.index(shared["link"]) < links.index(only_vector["link"])
    assert links.index(shared["link"]) < links.index(only_es["link"])


def test_rrf_score_ordering():
    """두 검색 모두 1위인 기사가 최상위에 와야 한다."""
    winner = _news("https://example.com/winner")
    loser_a = _news("https://example.com/loser-a")
    loser_b = _news("https://example.com/loser-b")

    with (
        patch("search.hybrid_search._get_representative_ids", return_value=None),
        patch("search.hybrid_search.vector_search", return_value=[winner, loser_a]),
        patch("search.hybrid_search.es_search", return_value=[winner, loser_b]),
    ):
        from search.hybrid_search import hybrid_search
        results = hybrid_search("연금", ["연금"], top_k=3)

    # winner는 양쪽 모두 rank 1 → RRF 점수가 명확하게 가장 높음
    assert results[0]["link"] == winner["link"]


def test_top_k_limits_result_count():
    """top_k=3 이면 최대 3건만 반환해야 한다."""
    news_list = [_news(f"https://example.com/{i}") for i in range(10)]

    with (
        patch("search.hybrid_search._get_representative_ids", return_value=None),
        patch("search.hybrid_search.vector_search", return_value=news_list),
        patch("search.hybrid_search.es_search", return_value=news_list),
    ):
        from search.hybrid_search import hybrid_search
        results = hybrid_search("연금", ["연금"], top_k=3)

    assert len(results) <= 3


# ---------------------------------------------------------------------------
# use_clusters 분기 검증
# ---------------------------------------------------------------------------

def test_use_clusters_false_skips_representative_fetch():
    """use_clusters=False 이면 _get_representative_ids를 호출하지 않아야 한다."""
    with (
        patch("search.hybrid_search._get_representative_ids") as mock_get_ids,
        patch("search.hybrid_search.vector_search", return_value=[]),
        patch("search.hybrid_search.es_search", return_value=[]),
    ):
        from search.hybrid_search import hybrid_search
        hybrid_search("연금", ["연금"], use_clusters=False)

    mock_get_ids.assert_not_called()


def test_use_clusters_true_calls_representative_fetch():
    """use_clusters=True(기본값)이면 _get_representative_ids를 호출해야 한다."""
    with (
        patch("search.hybrid_search._get_representative_ids", return_value=[1, 2]) as mock_get_ids,
        patch("search.hybrid_search.vector_search", return_value=[]),
        patch("search.hybrid_search.es_search", return_value=[]),
    ):
        from search.hybrid_search import hybrid_search
        hybrid_search("연금", ["연금"], use_clusters=True)

    mock_get_ids.assert_called_once()


def test_fallback_to_all_news_when_no_clusters():
    """클러스터링 결과가 없으면(None 반환) representative_ids=None 으로 전체 검색해야 한다."""
    with (
        patch("search.hybrid_search._get_representative_ids", return_value=None),
        patch("search.hybrid_search.vector_search", return_value=[]) as mock_vs,
        patch("search.hybrid_search.es_search", return_value=[]) as mock_es,
    ):
        from search.hybrid_search import hybrid_search
        hybrid_search("연금", ["연금"], use_clusters=True)

    _, vs_kwargs = mock_vs.call_args
    _, es_kwargs = mock_es.call_args
    assert vs_kwargs.get("representative_ids") is None
    assert es_kwargs.get("representative_ids") is None
