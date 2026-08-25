# 일회성/복구용
# 이미 실행 완료 -> 삭제 가능

"""기존 Elasticsearch news 문서에 PostgreSQL news.id(pg_id)를 보강한다.

이미 색인된 ES 문서가 link 를 _id 로 쓰고 있고 pg_id 필드가 없을 때,
PostgreSQL news 테이블의 link/id 매핑을 이용해 pg_id 만 업데이트한다.

실행:
    python -m news.load.backfill_es_pg_id
"""

from dotenv import find_dotenv, load_dotenv
from elasticsearch import helpers

load_dotenv(find_dotenv())

from common.db import connection
from common.es_client import NEWS_INDEX, get_client
from common.logger import get_logger

log = get_logger(__name__)


def _fetch_link_ids() -> list[tuple[str, int]]:
    with connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT link, id FROM news WHERE link IS NOT NULL")
            return cur.fetchall()


def backfill() -> int:
    rows = _fetch_link_ids()
    actions = [
        {
            "_op_type": "update",
            "_index": NEWS_INDEX,
            "_id": link,
            "doc": {"pg_id": news_id},
            "doc_as_upsert": False,
        }
        for link, news_id in rows
    ]
    if not actions:
        return 0

    ok, errors = helpers.bulk(get_client(), actions, raise_on_error=False)
    if errors:
        log.warning("ES pg_id backfill 일부 실패: %d건", len(errors))
    log.info("ES pg_id backfill: %d건", ok)
    return ok


if __name__ == "__main__":
    backfill()
