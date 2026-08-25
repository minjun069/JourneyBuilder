"""PostgreSQL(news 테이블) 적재.

enriched 레코드를 link 기준 upsert(ON CONFLICT) 한다 → 배치 재실행에도 멱등.
PostgreSQL 은 원천(source of truth). 임베딩은 pgvector, 태그/키워드는 JSONB.
"""

import json

from psycopg2.extras import execute_values

from common.db import connection
from common.logger import get_logger

log = get_logger(__name__)

_INSERT_SQL = """
INSERT INTO news (
    title, link, pub_date, press, text,
    category, tags, keywords, embedding, batch_date
)
VALUES %s
ON CONFLICT (link) DO UPDATE SET
    title      = EXCLUDED.title,
    pub_date   = EXCLUDED.pub_date,
    press      = EXCLUDED.press,
    text       = EXCLUDED.text,
    category   = EXCLUDED.category,
    tags       = EXCLUDED.tags,
    keywords   = EXCLUDED.keywords,
    embedding  = EXCLUDED.embedding,
    batch_date = EXCLUDED.batch_date,
    updated_at = NOW()
RETURNING id, link;
"""

# 각 행의 캐스팅 템플릿 (jsonb / vector)
_TEMPLATE = "(%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s::vector,%s)"


def _vec(embedding) -> str | None:
    """임베딩 리스트를 pgvector 리터럴 '[v1,v2,...]' 로 변환. 비면 None."""
    if not embedding:
        return None
    return "[" + ",".join(str(v) for v in embedding) + "]"


def upsert(records: list[dict], batch_date: str) -> dict[str, int]:
    """enriched 레코드들을 news 테이블에 upsert. link -> news.id 매핑 반환."""
    rows = []
    for r in records:
        if not r.get("link"):
            continue
        rows.append(
            (
                r.get("title"),
                r.get("link"),
                r.get("pub_date"),
                r.get("press"),
                r.get("text"),
                r.get("category"),
                json.dumps(r.get("tags") or [], ensure_ascii=False),
                json.dumps(r.get("keywords") or [], ensure_ascii=False),
                _vec(r.get("embedding")),
                batch_date,
            )
        )

    if not rows:
        return {}

    with connection() as conn:
        with conn.cursor() as cur:
            result = execute_values(
                cur,
                _INSERT_SQL,
                rows,
                template=_TEMPLATE,
                fetch=True,
            )

    log.info("PostgreSQL upsert: %d건", len(rows))
    return {link: news_id for news_id, link in result}
