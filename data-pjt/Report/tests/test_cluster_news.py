"""
cluster_news.py 단위 테스트.
Spark 의존성이 없는 순수 Python 로직(_parse_embedding, k 계산, 대표 기사 선정)만 검증.
"""
import json
import sys
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

# pyspark가 설치되지 않은 환경에서도 테스트 가능하도록 미리 모킹
for mod in [
    "pyspark", "pyspark.ml", "pyspark.ml.clustering",
    "pyspark.ml.linalg", "pyspark.sql", "pyspark.sql.functions",
]:
    sys.modules.setdefault(mod, MagicMock())

# Vectors.dense가 실제 list를 반환하도록 설정 (파싱 성공 여부 확인용)
sys.modules["pyspark.ml.linalg"].Vectors.dense.side_effect = lambda x: x


# ---------------------------------------------------------------------------
# _parse_embedding
# ---------------------------------------------------------------------------

def test_parse_embedding_valid_string():
    """유효한 JSON 배열 문자열은 파싱에 성공하고 None이 아니어야 한다."""
    from spark_jobs.cluster_news import _parse_embedding
    vec = [0.1, 0.2, 0.3]
    result = _parse_embedding(json.dumps(vec))
    assert result is not None


def test_parse_embedding_returns_correct_values():
    """파싱 결과가 원래 임베딩 값과 동일해야 한다."""
    from spark_jobs.cluster_news import _parse_embedding
    vec = [0.1, 0.2, 0.3]
    result = _parse_embedding(json.dumps(vec))
    assert result == vec


def test_parse_embedding_invalid_string_returns_none():
    """JSON 파싱이 불가능한 문자열은 None을 반환해야 한다."""
    from spark_jobs.cluster_news import _parse_embedding
    assert _parse_embedding("not_json") is None
    assert _parse_embedding("") is None
    assert _parse_embedding("{invalid}") is None


# ---------------------------------------------------------------------------
# k 자동 계산 (_calc_k)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("n, expected_k", [
    (100, 10),   # sqrt(100) = 10
    (50, 7),     # int(50**0.5) = 7
    (4, 2),      # int(4**0.5) = 2
    (1, 1),      # min(k, n)에 의해 n=1로 잘림 (main()이 n<2를 사전 차단)
])
def test_k_auto_calculation(n, expected_k):
    """k = max(2, int(n**0.5)) 공식 검증."""
    from spark_jobs.cluster_news import _calc_k
    assert _calc_k(n) == expected_k


def test_k_override_respected():
    """k_override 지정 시 해당 값을 사용해야 한다."""
    from spark_jobs.cluster_news import _calc_k
    assert _calc_k(100, k_override=20) == 20


def test_k_capped_at_data_size():
    """k는 데이터 수를 초과할 수 없다."""
    from spark_jobs.cluster_news import _calc_k
    assert _calc_k(3) <= 3


# ---------------------------------------------------------------------------
# 대표 기사 선정 (_select_representative)
# ---------------------------------------------------------------------------

def test_representative_is_closest_to_centroid():
    """centroid에 L2 거리가 가장 짧은 기사가 대표로 선정되어야 한다."""
    from spark_jobs.cluster_news import _select_representative
    centroid = np.array([1.0, 0.0, 0.0])
    id_to_vec = {
        1: np.array([0.95, 0.05, 0.0]),   # 가장 가까움
        2: np.array([0.5, 0.5, 0.0]),
        3: np.array([0.0, 0.0, 1.0]),     # 가장 멂
    }
    assert _select_representative([1, 2, 3], id_to_vec, centroid) == 1


def test_representative_among_two_candidates():
    """후보가 2개일 때 더 가까운 쪽이 선정되어야 한다."""
    from spark_jobs.cluster_news import _select_representative
    centroid = np.array([0.0, 1.0])
    id_to_vec = {
        10: np.array([0.0, 0.9]),   # 거리 0.1
        20: np.array([0.0, 0.5]),   # 거리 0.5
    }
    assert _select_representative([10, 20], id_to_vec, centroid) == 10


# ---------------------------------------------------------------------------
# _save_clusters DB 저장 검증
# ---------------------------------------------------------------------------

def test_save_clusters_deletes_today_before_insert():
    """저장 전 오늘 날짜 기존 데이터를 DELETE해야 한다."""
    mock_cursor = MagicMock()
    mock_conn = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
    mock_conn.cursor.return_value.__exit__.return_value = False

    with patch("spark_jobs.cluster_news.get_connection", return_value=mock_conn):
        from spark_jobs.cluster_news import _save_clusters
        _save_clusters([{"cluster_id": 0, "representative_id": 1, "news_ids": [1, 2]}])

    delete_sql = mock_cursor.execute.call_args[0][0]
    assert "DELETE" in delete_sql
    assert "CURRENT_DATE" in delete_sql


def test_save_clusters_inserts_correct_count():
    """클러스터 수만큼 executemany로 INSERT가 호출되어야 한다."""
    clusters = [
        {"cluster_id": 0, "representative_id": 1, "news_ids": [1, 2]},
        {"cluster_id": 1, "representative_id": 3, "news_ids": [3, 4]},
    ]
    mock_cursor = MagicMock()
    mock_conn = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
    mock_conn.cursor.return_value.__exit__.return_value = False

    with patch("spark_jobs.cluster_news.get_connection", return_value=mock_conn):
        from spark_jobs.cluster_news import _save_clusters
        _save_clusters(clusters)

    insert_args = mock_cursor.executemany.call_args[0][1]
    assert len(insert_args) == 2
