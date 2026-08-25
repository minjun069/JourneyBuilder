"""
pgvector cosine similarity 기반 벡터 검색.
사용자 관심 텍스트를 임베딩으로 변환한 뒤 news 테이블에서 유사 기사를 검색한다.
"""
import os

import google.generativeai as genai
from db import get_news_connection

genai.configure(api_key=os.getenv("GEMINI_API_KEY"))

# output_dimensionality=1536: DB news.embedding VECTOR(1536) 컬럼과 차원 수 일치 유지
_EMBEDDING_MODEL = "models/gemini-embedding-001"
_EMBEDDING_DIM = 1536


def _embed_query(text: str) -> list[float]:
    result = genai.embed_content(
        model=_EMBEDDING_MODEL,
        content=text,
        task_type="RETRIEVAL_QUERY",
        output_dimensionality=_EMBEDDING_DIM,
    )
    return result["embedding"]


def vector_search(
    query_text: str,
    top_k: int = 20,
    days: int = 30,
    representative_ids: list[int] | None = None,
) -> list[dict]:
    """
    pgvector cosine similarity 기반 뉴스 검색.

    Args:
        query_text: 임베딩할 검색 쿼리 (사용자 관심사 텍스트)
        top_k: 반환할 최대 결과 수
        days: 검색 대상 기간 (최근 N일)
        representative_ids: 클러스터 대표 기사 ID 목록.
                            전달 시 해당 기사만 검색 대상으로 제한.
                            None이면 전체 뉴스 대상 검색.

    Returns:
        list of {id, title, link, pub_date, category, score}
    """
    embedding = _embed_query(query_text)
    vector_str = "[" + ",".join(str(v) for v in embedding) + "]"

    if representative_ids:
        sql = """
            SELECT
                id,
                title,
                link,
                pub_date,
                category,
                1 - (embedding <=> %s::vector) AS score
            FROM news
            WHERE
                embedding IS NOT NULL
                AND pub_date >= NOW() - INTERVAL '%s days'
                AND id = ANY(%s)
            ORDER BY score DESC
            LIMIT %s;
        """
        params = (vector_str, days, representative_ids, top_k)
    else:
        sql = """
            SELECT
                id,
                title,
                link,
                pub_date,
                category,
                1 - (embedding <=> %s::vector) AS score
            FROM news
            WHERE
                embedding IS NOT NULL
                AND pub_date >= NOW() - INTERVAL '%s days'
            ORDER BY score DESC
            LIMIT %s;
        """
        params = (vector_str, days, top_k)

    conn = get_news_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
    finally:
        conn.close()

    return [
        {
            "id": row[0],
            "title": row[1],
            "link": row[2],
            "pub_date": row[3],
            "category": row[4],
            "score": float(row[5]),
        }
        for row in rows
    ]
