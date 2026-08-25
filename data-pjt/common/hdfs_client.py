"""HDFS 읽기/쓰기 헬퍼 (WebHDFS 기반).

수집기는 원본 뉴스를 JSON Lines 로 HDFS 에 저장한다.
  경로 규칙: /news/raw/YYYY/MM/DD/news.jsonl

python `hdfs` 라이브러리(InsecureClient)는 WebHDFS(9870)로 접근한다.

⚠️ 호스트(localhost)에서 파일 쓰기/읽기는 datanode 호스트명 리다이렉트로 실패할 수 있다.
   실제 수집기는 de_net 컨테이너(Airflow) 안에서 namenode:9870 으로 접근하므로 정상 동작한다.
   호스트에서는 디렉토리/상태 조회(namenode 단독 연산)만 안전하다.

환경변수:
  HDFS_URL  : WebHDFS 주소 (기본 http://namenode:9870, 호스트 테스트 시 http://localhost:9870)
  HDFS_USER : HDFS 사용자 (기본 root)
"""

import json
import os
import posixpath

from hdfs import InsecureClient

from common.logger import get_logger

log = get_logger(__name__)

HDFS_URL = os.getenv("HDFS_URL", "http://namenode:9870")
HDFS_USER = os.getenv("HDFS_USER", "root")

_client: InsecureClient | None = None


def get_client() -> InsecureClient:
    """WebHDFS 클라이언트를 lazy 하게 생성/반환한다."""
    global _client
    if _client is None:
        _client = InsecureClient(HDFS_URL, user=HDFS_USER)
    return _client


def _dated_path(base: str, date_str: str, filename: str) -> str:
    y, m, d = date_str.split("-")
    return posixpath.join(base, y, m, d, filename)


def raw_path(date_str: str, filename: str = "news.jsonl") -> str:
    """수집 원본 경로. 예: '2026-06-23' -> /news/raw/2026/06/23/news.jsonl"""
    return _dated_path("/news/raw", date_str, filename)


def enriched_path(date_str: str, filename: str = "news.jsonl") -> str:
    """처리(enrich) 결과 경로. 예: /news/enriched/2026/06/23/news.jsonl"""
    return _dated_path("/news/enriched", date_str, filename)


def exists(path: str) -> bool:
    """경로 존재 여부 (namenode 단독 연산이라 호스트에서도 안전)."""
    return get_client().status(path, strict=False) is not None


def makedirs(path: str) -> None:
    """디렉토리 생성 (이미 있으면 무시)."""
    get_client().makedirs(path)


def write_jsonl(path: str, records: list[dict], overwrite: bool = True) -> int:
    """dict 리스트를 JSON Lines 로 HDFS 에 기록한다. 기록한 행 수를 반환."""
    parent = posixpath.dirname(path)
    if parent:
        makedirs(parent)

    payload = "\n".join(json.dumps(r, ensure_ascii=False) for r in records)
    if payload:
        payload += "\n"

    get_client().write(path, data=payload, overwrite=overwrite, encoding="utf-8")
    log.info("HDFS write: %s (%d rows)", path, len(records))
    return len(records)


def read_jsonl(path: str) -> list[dict]:
    """HDFS 의 JSON Lines 파일을 읽어 dict 리스트로 반환한다."""
    with get_client().read(path, encoding="utf-8") as reader:
        content = reader.read()

    records = [json.loads(line) for line in content.splitlines() if line.strip()]
    log.info("HDFS read: %s (%d rows)", path, len(records))
    return records
