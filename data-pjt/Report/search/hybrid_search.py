"""
하이브리드 검색: pgvector(벡터) + ElasticSearch(BM25) → RRF 융합.

use_clusters=True(기본값)일 때 오늘 클러스터링 대표 기사만 검색 대상으로 삼아
보고서 내 중복 주제를 방지한다. 클러스터링 결과가 없으면 전체 뉴스로 자동 폴백.

RRF (Reciprocal Rank Fusion):
    score(d) = Σ 1 / (k + rank_i(d))
    k=60 은 Robertson et al. 2009 권장값.
"""
import logging
from datetime import date

from db import get_news_connection
from .es_search import es_search
from .vector_search import vector_search

logger = logging.getLogger(__name__)


def _get_representative_ids() -> list[int] | None:
    """
    오늘 날짜 클러스터링 결과의 대표 뉴스 ID 목록을 반환.
    cluster_news.py가 아직 실행되지 않았거나 결과가 없으면 None.
    """
    conn = get_news_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT representative_news_id FROM news_clusters WHERE run_date = %s",
                (date.today(),),
            )
            ids = [row[0] for row in cur.fetchall()]
    finally:
        conn.close()
    return ids if ids else None


def _reciprocal_rank_fusion(
    rank_lists: list[list[dict]],
    k: int = 60,
) -> list[dict]:
    scores: dict[str, dict] = {}
    for rank_list in rank_lists:
        for rank, item in enumerate(rank_list, start=1):
            key = item["link"]
            if key not in scores:
                scores[key] = {"item": item, "rrf_score": 0.0}
            scores[key]["rrf_score"] += 1.0 / (k + rank)
    return sorted(scores.values(), key=lambda x: x["rrf_score"], reverse=True)


def hybrid_search(
    query_text: str,
    keywords: list[str],
    categories: list[str] | None = None,
    top_k: int = 10,
    days: int = 30,
    use_clusters: bool = True,
) -> list[dict]:
    """
    pgvector + ElasticSearch 하이브리드 검색.

    Args:
        query_text: 벡터 검색용 자연어 쿼리
        keywords: ES BM25 검색용 키워드 목록
        categories: 카테고리 필터 (None이면 전체)
        top_k: 최종 반환 뉴스 수
        days: 최근 N일 이내 뉴스만 대상
        use_clusters: True면 오늘 클러스터 대표 기사만 검색.
                      클러스터링 결과 없으면 전체 뉴스로 자동 폴백.

    Returns:
        RRF 점수 기준으로 정렬된 top_k 뉴스 목록
    """
    representative_ids = _get_representative_ids() if use_clusters else None

    if use_clusters and representative_ids:
        logger.info("클러스터 대표 기사 %d건 대상 검색", len(representative_ids))
    else:
        logger.info("전체 뉴스 대상 검색 (클러스터링 결과 없음)")

    fetch_k = top_k * 2

    vector_results = vector_search(
        query_text, top_k=fetch_k, days=days, representative_ids=representative_ids
    )
    es_results = es_search(
        keywords, categories=categories, top_k=fetch_k, days=days, representative_ids=representative_ids
    )

    fused = _reciprocal_rank_fusion([vector_results, es_results])
    return [entry["item"] for entry in fused[:top_k]]
