# 서비스 개선 제안

> 작성일: 2026-06-23

---

## 1. 검색 품질

### 1-1. ES 단일화 (벡터 + BM25 통합)

현재 벡터 검색(pgvector)과 BM25(ES)가 분리되어 있어 Python에서 RRF를 수동으로 수행합니다.  
ES `dense_vector` 타입을 도입하면 ES 내장 RRF로 두 검색을 단일 쿼리로 통합할 수 있습니다.

```json
// ES 8.9+ 내장 RRF
{
  "retriever": {
    "rrf": {
      "retrievers": [
        { "knn": { "field": "embedding", "num_candidates": 50 } },
        { "standard": { "query": { "multi_match": { ... } } } }
      ]
    }
  }
}
```

- **효과**: DB 연결 2개 → ES 1개, `vector_search.py` / `hybrid_search.py` 대폭 단순화
- **선행 조건**: `pipeline_integration.md` 참고 — consumer가 ES에도 임베딩 색인 필요

---

### 1-2. ES `min_score` 임계값 도입

현재 BM25 매칭이 약해도 결과가 반환됩니다.  
실데이터로 점수 분포를 확인한 뒤 `min_score`를 설정하면 낮은 관련성 기사를 제거할 수 있습니다.

```python
query = {
    "min_score": <실험으로 결정>,
    "query": { "bool": { ... } },
}
```

---

### 1-3. 클러스터 품질 측정

현재 KMeans 결과의 품질을 확인하는 수단이 없습니다.  
실루엣 계수(Silhouette Score)를 로그로 남기면 k 설정의 적절성을 모니터링할 수 있습니다.

```python
from sklearn.metrics import silhouette_score
score = silhouette_score(vectors, labels)
logger.info("실루엣 계수: %.4f", score)
```

---

## 2. 성능

### 2-1. 사용자별 보고서 병렬 처리

현재 `search_and_report.py`는 사용자를 순차 처리합니다.  
`concurrent.futures.ThreadPoolExecutor`로 API 호출 대기 시간을 겹칠 수 있습니다.

```python
from concurrent.futures import ThreadPoolExecutor, as_completed

with ThreadPoolExecutor(max_workers=5) as executor:
    futures = {executor.submit(_process_user, uid, search_days): uid for uid in user_ids}
    results = [f.result() for f in as_completed(futures)]
```

- **주의**: Gemini / OpenAI API rate limit에 맞게 `max_workers` 조정 필요

---

### 2-2. DB 커넥션 풀링

현재 매 함수 호출마다 새 커넥션을 열고 닫습니다.  
`psycopg2.pool.SimpleConnectionPool`을 사용하면 커넥션 재사용으로 오버헤드를 줄일 수 있습니다.

```python
# db.py
from psycopg2 import pool

_pool = pool.SimpleConnectionPool(minconn=2, maxconn=10, ...)

def get_connection():
    return _pool.getconn()

def release_connection(conn):
    _pool.putconn(conn)
```

---

### 2-3. 대표 기사 ID 캐싱

현재 `_get_representative_ids()`를 사용자마다 호출합니다 (100명이면 100번 DB 조회).  
`search_and_report.py`에서 한 번 조회 후 전달하면 됩니다.

```python
def main():
    ...
    representative_ids = _get_representative_ids()
    results = [_process_user(uid, search_days, representative_ids) for uid in user_ids]
```

---

## 3. 안정성

### 3-1. 사용자별 재시도 로직

현재 `_process_user`에서 예외 발생 시 해당 사용자는 `"error"` 상태로 기록되고 넘어갑니다.  
LLM API 일시 오류처럼 재시도로 해결 가능한 경우를 위해 지수 백오프 재시도를 추가할 수 있습니다.

```python
from tenacity import retry, stop_after_attempt, wait_exponential

@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, max=10))
def _call_llm(...):
    ...
```

---

### 3-2. 보고서 중복 생성 방지

현재 DAG가 같은 날 두 번 실행되면 동일 사용자에 대해 보고서가 중복 생성됩니다.  
`reports` 테이블에 `(user_id, run_date)` 유니크 제약 또는 UPSERT를 적용하면 됩니다.

```sql
ALTER TABLE reports ADD CONSTRAINT uq_user_run_date UNIQUE (user_id, run_date);
```

또는 저장 전 오늘 보고서 존재 여부를 확인합니다.

```python
cur.execute("SELECT id FROM reports WHERE user_id=%s AND run_date=CURRENT_DATE", (user_id,))
if cur.fetchone():
    return  # 이미 생성됨
```

---

## 4. 모니터링

### 4-1. 보고서 생성 지표 수집

현재 성공/실패 건수를 로그로만 남깁니다.  
Airflow Variable 또는 별도 `report_run_logs` 테이블에 실행 결과를 저장하면 추세 파악이 가능합니다.

```sql
CREATE TABLE report_run_logs (
    id          SERIAL PRIMARY KEY,
    run_date    DATE NOT NULL,
    total       INT,
    success     INT,
    skipped     INT,
    failed      INT,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);
```

---

### 4-2. 클러스터링 실행 여부 확인

`search_and_report`는 `cluster_news`가 먼저 실행됐다고 가정합니다.  
당일 `news_clusters` 데이터가 없으면 전체 뉴스 폴백이 발생하는데, 이를 로그나 알림으로 명시하면 운영 상황을 파악하기 쉬워집니다.

---

## 5. 사용자 경험

### 5-1. 사용자 관심사 자동 갱신

현재 `report_user_preferences`는 초기 설정 후 변경되지 않습니다.  
사용자가 보고서를 읽은 기사나 클릭한 카테고리를 피드백으로 받아 관심사를 주기적으로 업데이트하면 개인화 품질이 향상됩니다.

---

### 5-2. 보고서 품질 피드백

사용자가 보고서에 좋아요/싫어요를 남길 수 있는 컬럼을 `reports` 테이블에 추가하면, 향후 프롬프트 개선이나 검색 파라미터 튜닝의 근거 데이터로 활용할 수 있습니다.

```sql
ALTER TABLE reports ADD COLUMN feedback SMALLINT; -- 1: 좋음, -1: 나쁨, NULL: 미응답
```
