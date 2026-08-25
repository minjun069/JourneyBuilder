"""Elasticsearch 연결 헬퍼.

news(적재) 와 reports(검색) 가 공유한다. 인덱스 이름도 여기서 공유.

환경변수:
  ES_URL        : Elasticsearch 주소 (기본 http://elasticsearch:9200,
                  호스트 테스트 시 http://localhost:9200)
  ES_NEWS_INDEX : 뉴스 인덱스 이름 (기본 news)
"""

import os

from elasticsearch import Elasticsearch

from common.logger import get_logger

log = get_logger(__name__)

ES_URL = os.getenv("ES_URL", "http://elasticsearch:9200")
NEWS_INDEX = os.getenv("ES_NEWS_INDEX", "news")

_client: Elasticsearch | None = None


def get_client() -> Elasticsearch:
    """Elasticsearch 클라이언트를 lazy 하게 생성/반환한다."""
    global _client
    if _client is None:
        _client = Elasticsearch(ES_URL)
    return _client
