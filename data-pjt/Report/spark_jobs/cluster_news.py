"""
Spark 잡: 뉴스 임베딩 KMeans 클러스터링 + 대표 기사 선정.

비슷한 주제의 뉴스를 하나의 클러스터로 묶고, 각 클러스터에서
centroid에 가장 가까운 기사를 대표로 선정하여 news_clusters 테이블에 저장.
보고서 생성 시 대표 기사만 검색 대상으로 삼아 중복 주제를 방지한다.

KMeans.fit()은 Spark 워커에 분산되어 실행됨 (대용량 벡터 연산이 Spark 사용 근거).
대표 기사 선정(centroid 거리 계산)은 드라이버에서 numpy로 수행.

실행:
  spark-submit cluster_news.py        # 최근 7일 뉴스, k=sqrt(n) 자동
  spark-submit cluster_news.py 30     # 최근 30일 뉴스
  spark-submit cluster_news.py 7 50   # 최근 7일, k=50 고정
"""
import json
import logging
import os
import sys
from datetime import datetime, timedelta

import numpy as np
from pyspark.ml.clustering import KMeans
from pyspark.ml.linalg import Vectors
from pyspark.sql import Row, SparkSession
from pyspark.sql.functions import col

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from db import get_connection

logger = logging.getLogger(__name__)

PG_URL = (
    f"jdbc:postgresql://{os.getenv('DB_HOST', 'localhost')}:"
    f"{os.getenv('DB_PORT', '5432')}/{os.getenv('DB_NAME', 'streaming_data')}"
)
PG_PROPS = {
    "user": os.getenv("DB_USER", "ssafy"),
    "password": os.getenv("DB_PASSWORD", ""),
    "driver": "org.postgresql.Driver",
}


def _parse_embedding(embedding_str: str):
    """pgvector 형식 '[0.1,0.2,...]' → MLlib DenseVector. 파싱 실패 시 None."""
    try:
        return Vectors.dense(json.loads(embedding_str))
    except Exception:
        return None


def _calc_k(n: int, k_override: int | None = None) -> int:
    """클러스터 수 결정: 지정값 우선, 없으면 sqrt(n) 휴리스틱. 최소 2, 최대 n."""
    k = k_override or max(2, int(n ** 0.5))
    return min(k, n)


def _select_representative(
    member_ids: list[int],
    id_to_vec: dict[int, np.ndarray],
    centroid: np.ndarray,
) -> int:
    """클러스터 멤버 중 centroid에 L2 거리가 가장 가까운 기사 ID를 반환."""
    return min(member_ids, key=lambda nid: np.linalg.norm(id_to_vec[nid] - centroid))


def _save_clusters(clusters: list[dict]) -> None:
    """오늘 클러스터링 결과를 news_clusters 테이블에 저장 (당일 데이터 교체)."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM news_clusters WHERE run_date = CURRENT_DATE")
            cur.executemany(
                """
                INSERT INTO news_clusters (cluster_id, representative_news_id, news_ids)
                VALUES (%s, %s, %s)
                """,
                [(c["cluster_id"], c["representative_id"], c["news_ids"]) for c in clusters],
            )
        conn.commit()
    finally:
        conn.close()


def main():
    
    spark = (
        SparkSession.builder.appName("cluster_news")
        .config("spark.jars.packages", "org.postgresql:postgresql:42.7.3")
        .getOrCreate()
    )
    spark.sparkContext.setLogLevel("WARN")

    # embedding 있는 뉴스만 로드 (embedding 컬럼은 VECTOR → JDBC로 문자열로 수신)
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 7
    since = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d")

    raw_df = (
        spark.read.jdbc(url=PG_URL, table="news", properties=PG_PROPS)
        .filter(col("pub_date") >= since)
        .filter(col("embedding").isNotNull())
        .select("id", "embedding")
    )
    rows = raw_df.collect()

    logger.info("대상 뉴스: %d건 (최근 %d일)", len(rows), days)

    if len(rows) < 2:
        logger.warning("클러스터링 대상 부족, 종료")
        spark.stop()
        return


    # embedding 문자열 파싱 및 MLlib 입력용 DataFrame 구성
    valid = [(row["id"], _parse_embedding(row["embedding"])) for row in rows]
    valid = [(nid, vec) for nid, vec in valid if vec is not None]

    if not valid:
        logger.warning("유효한 임베딩 없음, 종료")
        spark.stop()
        return

    vector_df = spark.createDataFrame(
        [Row(news_id=nid, features=vec) for nid, vec in valid]
    )


    # KMeans 학습 — Spark 워커들이 반복적으로 centroid를 분산 계산
    k_override = int(sys.argv[2]) if len(sys.argv) > 2 else None
    k = _calc_k(len(valid), k_override)

    logger.info("k=%d, 유효 뉴스=%d건", k, len(valid))

    model = (
        KMeans(k=k, seed=42, maxIter=20, featuresCol="features", predictionCol="cluster_id")
        .fit(vector_df)
    )

    # 클러스터 할당 결과 수집 및 클러스터별 멤버 그룹핑
    assigned = model.transform(vector_df).select("news_id", "cluster_id").collect()

    cluster_map: dict[int, list[int]] = {}
    for row in assigned:
        cluster_map.setdefault(row["cluster_id"], []).append(row["news_id"])


    # 대표 기사 선정: centroid에 가장 가까운 기사 (L2 거리 최소)
    centroids = [np.array(c) for c in model.clusterCenters()]
    id_to_vec = {nid: np.array(vec) for nid, vec in valid}

    clusters = []
    for cluster_id, member_ids in cluster_map.items():
        centroid = centroids[cluster_id]
        representative_id = _select_representative(member_ids, id_to_vec, centroid)
        clusters.append({
            "cluster_id": cluster_id,
            "representative_id": representative_id,
            "news_ids": member_ids,
        })


    # 클러스터링 결과 저장
    _save_clusters(clusters)
    logger.info("완료: %d개 클러스터, 대표 기사 %d건 저장", len(clusters), len(clusters))
    spark.stop()


if __name__ == "__main__":
    main()
