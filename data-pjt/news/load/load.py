"""적재 단계 - HDFS enriched 를 읽어 PostgreSQL + Elasticsearch 에 이중 쓰기.

흐름:
  HDFS enriched 읽기 -> ES 인덱스 보장 -> PG upsert(원천) -> ES bulk 색인(검색)

PostgreSQL 이 원천(source of truth)이라 먼저 적재한다. 둘 다 link 기준 멱등.

실행:
    python -m news.load.load
"""

from datetime import date

from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv())

from common.hdfs_client import enriched_path, read_jsonl
from common.logger import get_logger
from news.load.es_writer import bulk_index, ensure_index
from news.load.pg_writer import upsert

log = get_logger(__name__)


def load(target_date: date | None = None) -> dict:
    """target_date 의 enriched 결과를 PG + ES 에 적재한다."""
    target_date = target_date or date.today()
    date_str = target_date.isoformat()

    records = read_jsonl(enriched_path(date_str))
    log.info("enriched 읽기: %d건", len(records))

    ensure_index()
    link_to_id = upsert(records, batch_date=date_str)
    for record in records:
        record["pg_id"] = link_to_id.get(record.get("link"))
    n_es = bulk_index(records)

    result = {"date": date_str, "records": len(records), "pg": len(link_to_id), "es": n_es}
    log.info("적재 결과: %s", result)
    return result


if __name__ == "__main__":
    load()
