"""
ElasticSearch BM25 기반 전문 검색(Full-text search).
news 인덱스에 대해 사용자 키워드로 multi_match 검색을 수행한다.

ES 인덱스 매핑 (외부 파이프라인이 생성):
  pg_id(_id), title, link, pub_date, text, category, keywords
"""
import os
from datetime import datetime, timedelta

from dotenv import load_dotenv
from elasticsearch import Elasticsearch

from db import get_news_connection

load_dotenv()

ES_INDEX = "news"


def _get_client() -> Elasticsearch:
    host = os.getenv("ES_HOST", "http://localhost:9200")
    user = os.getenv("ES_USER", "elastic")
    password = os.getenv("ES_PASSWORD", "")

    if password:
        return Elasticsearch(hosts=[host], basic_auth=(user, password))
    return Elasticsearch(hosts=[host])


def _ids_by_link(links: list[str]) -> dict[str, int]:
    if not links:
        return {}

    conn = get_news_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT link, id FROM news WHERE link = ANY(%s)", (links,))
            return {row[0]: row[1] for row in cur.fetchall()}
    finally:
        conn.close()


def es_search(
    keywords: list[str],
    categories: list[str] | None = None,
    top_k: int = 20,
    days: int = 30,
    representative_ids: list[int] | None = None,
) -> list[dict]:
    """
    ElasticSearch BM25 기반 뉴스 검색.

    Args:
        keywords: 검색 키워드 목록
        categories: 필터할 카테고리 목록. None이면 전체.
        top_k: 반환할 최대 결과 수
        days: 검색 대상 기간 (최근 N일)
        representative_ids: 클러스터 대표 기사 ID 목록.
                            전달 시 ES _id 기준으로 해당 기사만 검색 대상으로 제한.
                            None이면 전체 뉴스 대상 검색.

    Returns:
        list of {id, title, link, pub_date, category, score}
    """
    client = _get_client()

    # 키워드 검색 조건
    must_clauses = [
        {
            "multi_match": {
                "query": " ".join(keywords),
                "fields": ["title^2", "text", "keywords"],
                "type": "best_fields",
            }
        }
    ]

    # 필터링 조건
    since = (datetime.utcnow() - timedelta(days=days)).isoformat()
    filter_clauses: list[dict] = [{"range": {"pub_date": {"gte": since}}}]

    if categories:
        filter_clauses.append({"terms": {"category": categories}})

    if representative_ids:
        filter_clauses.append({"ids": {"values": [str(rid) for rid in representative_ids]}})


    query = {
        "query": {
            "bool": {
                "must": must_clauses,
                "filter": filter_clauses,
            }
        },
        "size": top_k,
    }

    resp = client.search(index=ES_INDEX, body=query)
    hits = resp["hits"]["hits"]
    link_to_id = _ids_by_link(
        [
            hit["_source"].get("link")
            for hit in hits
            if not hit["_source"].get("pg_id") and hit["_source"].get("link")
        ]
    )

    return [
        {
            "id": hit["_source"].get("pg_id") or link_to_id.get(hit["_source"].get("link")),
            "title": hit["_source"].get("title"),
            "link": hit["_source"].get("link"),
            "pub_date": hit["_source"].get("pub_date"),
            "category": hit["_source"].get("category"),
            "score": hit["_score"],
        }
        for hit in hits
    ]
