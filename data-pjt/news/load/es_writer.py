"""Elasticsearch 적재 (한국어 전문검색 + 벡터 하이브리드용 색인).

enriched 레코드를 link 를 _id 로 하여 bulk 색인 → 재실행에도 멱등.
ES 는 파생 검색 인덱스(원천은 PostgreSQL). 리포트 검색은 RRF(BM25+kNN).

매핑:
  - title/text : nori(한국어 형태소) 분석
  - pg_id      : PostgreSQL news.id (리포트 RRF/저장 연결용)
  - tags/keywords/category/press/link : keyword (필터/패싯)
  - embedding  : dense_vector(1536, cosine) → kNN
"""

from elasticsearch import helpers

from common.es_client import NEWS_INDEX, get_client
from common.logger import get_logger

log = get_logger(__name__)

INDEX_MAPPING = {
    "properties": {
        "title": {"type": "text", "analyzer": "nori"},
        "pg_id": {"type": "long"},
        "text": {"type": "text", "analyzer": "nori"},
        "press": {"type": "keyword"},
        "category": {"type": "keyword"},
        "tags": {"type": "keyword"},
        "keywords": {"type": "keyword"},
        "pub_date": {"type": "date"},
        "link": {"type": "keyword"},
        "embedding": {
            "type": "dense_vector",
            "dims": 1536,
            "index": True,
            "similarity": "cosine",
        },
    }
}

# ES 로 보낼 필드
_DOC_FIELDS = [
    "title",
    "pg_id",
    "text",
    "press",
    "category",
    "tags",
    "keywords",
    "pub_date",
    "link",
    "embedding",
]


def ensure_index() -> None:
    """뉴스 인덱스가 없으면 매핑과 함께 생성한다 (있으면 그대로)."""
    es = get_client()
    if es.indices.exists(index=NEWS_INDEX):
        return
    es.indices.create(index=NEWS_INDEX, mappings=INDEX_MAPPING)
    log.info("ES 인덱스 생성: %s", NEWS_INDEX)


def _to_doc(r: dict) -> dict:
    doc = {k: r.get(k) for k in _DOC_FIELDS}
    # 임베딩 없는 기사는 dense_vector 색인 오류 방지를 위해 필드 제거
    if not doc.get("embedding"):
        doc.pop("embedding", None)
    return doc


def bulk_index(records: list[dict]) -> int:
    """enriched 레코드들을 link 를 _id 로 bulk 색인. 색인 건수 반환."""
    es = get_client()
    actions = [
        {"_index": NEWS_INDEX, "_id": r["link"], "_source": _to_doc(r)}
        for r in records
        if r.get("link")
    ]
    if not actions:
        return 0

    ok, errors = helpers.bulk(es, actions, raise_on_error=False)
    if errors:
        log.warning("ES bulk 일부 실패: %d건", len(errors))
    log.info("Elasticsearch 색인: %d건", ok)
    return ok
