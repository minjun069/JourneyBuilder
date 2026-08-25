# Report 모듈 초기 구현 현황 및 잔여 작업 명세

> 작성일: 2026-06-23  
> 담당: 민준  
> 관련 문서: [plan.md](./plan.md)

---

## 1. 프로젝트 컨텍스트

### 목적
DB에 저장된 사용자(accounts_users) 각각에 대해 최신 뉴스를 하이브리드 검색으로 찾고,
주요 정보를 보고서 형태로 자동 생성하는 배치 파이프라인 구현.

### 기술 스택

| 역할 | 기술 | 비고 |
|------|------|------|
| 벡터 검색 | PostgreSQL + pgvector | cosine similarity, 임베딩 1536-dim |
| 전문 검색 | ElasticSearch 8.x | BM25 multi-match |
| 하이브리드 융합 | RRF (Reciprocal Rank Fusion) | 직접 구현 |
| 배치 처리 | PySpark 3.5.4 | 사용자 목록 로드 + 병렬 처리 |
| 스케줄링 | Apache Airflow | 매일 02:00 DAG |
| LLM | GPT-4o-mini (GMS 엔드포인트) | 기존 preprocessing.py 패턴 재사용 |

### 의존 테이블 (기존, 수정 없음)

```
accounts_users           — id, email, name, birth_year, is_active
consulting_sessions      — user_id, status('completed'), created_at
consulting_financial_data — session_id, goal_type, target_retirement_age,
                            assets_pension, assets_financial, assets_real_estate,
                            monthly_income, monthly_expense
news                     — id, title, link, pub_date, text,
                            category, keywords(JSON), embedding(VECTOR 1536)
```

---

## 2. 완료된 작업

### 2-1. 테이블 스키마 설계 — `schema/report_tables.sql`

**신규 테이블 2개 + 인덱스 3개 + 업데이트 트리거 1개**

#### `report_user_preferences`
사용자별 추가 관심사를 수동으로 등록하는 테이블.  
`consulting_financial_data`의 자산/목표 정보만으로 유도되지 않는 세부 키워드/카테고리를 저장한다.

| 컬럼 | 타입 | 설명 |
|------|------|------|
| `user_id` | INTEGER FK | accounts_users.id |
| `interest_keywords` | TEXT[] | 관심 검색 키워드 배열 |
| `interest_categories` | VARCHAR(50)[] | 관심 카테고리 필터 (빈 배열이면 전체) |
| `max_news_per_report` | INTEGER | 보고서당 최대 참조 뉴스 수 (기본 10) |

#### `news_clusters`
Spark KMeans 클러스터링 결과를 저장하는 테이블.  
매일 `cluster_news.py`가 실행되며 `run_date` 기준으로 당일 데이터를 교체한다.

| 컬럼 | 타입 | 설명 |
|------|------|------|
| `cluster_id` | INTEGER | KMeans 클러스터 번호 (0부터 시작) |
| `representative_news_id` | INTEGER FK | 대표 기사 (centroid에 가장 가까운 news.id) |
| `news_ids` | INTEGER[] | 클러스터 내 전체 news.id 목록 |
| `run_date` | DATE | 클러스터링 실행일 |

#### `reports`
생성된 보고서를 저장하는 테이블.  
동일 사용자의 같은 주(`user_id + period_start` UNIQUE) 보고서는 UPSERT로 덮어쓴다.

| 컬럼 | 타입 | 설명 |
|------|------|------|
| `user_id` | INTEGER FK | accounts_users.id |
| `period_start` / `period_end` | DATE | 보고서가 다룬 뉴스의 날짜 범위 (의미 메타데이터) |
| `summary` | TEXT | LLM이 생성한 전체 요약 |
| `content` | JSONB | 섹션별 상세 내용 ({sections, action_items}) — summary는 별도 컬럼 |
| `news_ids` | INTEGER[] | 참조한 news.id 목록 |
| `run_date` | DATE | 보고서를 실제 생성·실행한 날짜 |

> **미적용 상태**: 아직 PostgreSQL에 실행되지 않음 → [잔여 작업 3-1](#3-1-db-테이블-생성) 참조

---

### 2-2. 하이브리드 검색 모듈 — `search/`

#### `search/vector_search.py`
- `vector_search(query_text, top_k, days)` 함수
- 사용자 관심 텍스트 → OpenAI `text-embedding-3-small` → 1536-dim 벡터
- `news.embedding <=> vector` cosine distance로 Top-K 검색
- `pub_date >= NOW() - INTERVAL N days` 필터 적용
- 반환: `[{id, title, link, pub_date, category, score}]`

#### `search/es_search.py`
- `es_search(keywords, categories, top_k, days)` 함수
- `title^2`, `text`, `keywords` 필드에 대해 `multi_match best_fields` 쿼리
- `pub_date` range 필터 + `category` terms 필터 선택 적용
- 반환: `[{id(pg_id), title, link, pub_date, category, score}]`

#### `search/hybrid_search.py`
- `hybrid_search(query_text, keywords, categories, top_k, days)` 함수
- 두 검색기를 각각 `top_k * 2`로 호출한 뒤 RRF로 결합
- **RRF 공식**: `score(d) = Σ 1 / (60 + rank_i(d))`
- 링크(`link`)를 dedup 키로 사용
- 최종 top_k 반환

---

### 2-3. 보고서 생성 모듈 — `generator/report_generator.py`

#### `_fetch_user_profile(user_id)`
- `accounts_users` + `consulting_financial_data` + `report_user_preferences` LEFT JOIN
- 상담 완료(`status='completed'`) 세션 중 가장 최신 재무 데이터 사용
- 반환: name, birth_year, goal_type, assets_*, monthly_*, interest_keywords, interest_categories

#### `_derive_search_params(profile)`
- 사용자 자산 보유 여부에 따라 기본 키워드 자동 추가
  - `assets_pension` 보유 → `['국민연금', '퇴직연금', '연금 개혁']` 추가
  - `assets_financial` 보유 → `['주식', '펀드', '재테크', '금리']` 추가
  - `assets_real_estate` 보유 → `['부동산', '주택연금', '임대']` 추가
- `interest_keywords`로 추가 확장 후 중복 제거
- 반환: `(query_text, keywords, categories)`

#### `generate_report(user_id, news_items)`
- 사용자 프로필 + Top-K 뉴스 제목/카테고리/날짜 → GPT-4o-mini 프롬프트
- `response_format={"type": "json_object"}` 강제 → JSON 파싱
- 반환 형식:
  ```json
  {
    "summary": "...",
    "sections": [{"category": "...", "headline": "...", "detail": "..."}],
    "action_items": ["...", "..."]
  }
  ```

#### `save_report(user_id, news_ids, content)`
- `reports` 테이블 UPSERT (`user_id + period_start` 충돌 시 덮어씀)
- `period_start = today - 6일`, `period_end = today`
- 반환: 저장된 `reports.id`

---

### 2-4. Spark 배치 잡 — `spark_jobs/`

> **전제**: PostgreSQL(pgvector)과 ElasticSearch에 최신 뉴스가 이미 저장되어 있다고 가정.
> 뉴스 수집 및 ES 동기화는 Flink 등 별도 파이프라인 담당. Report 모듈은 검색·생성만 담당.

#### `spark_jobs/cluster_news.py`
- 인자: `[days=7] [k=자동]`
- PostgreSQL `news` 테이블에서 최근 N일치 임베딩 로드
- **Spark MLlib KMeans**로 비슷한 주제 뉴스를 k개 클러스터로 묶음 (분산 학습)
- 각 클러스터에서 centroid에 가장 가까운 기사를 대표로 선정 (numpy, 드라이버)
- `news_clusters` 테이블에 저장 (당일 데이터 교체)
- k 미지정 시 `sqrt(뉴스 수)` 휴리스틱 사용

#### `spark_jobs/search_and_report.py`
- `accounts_users` 테이블에서 `is_active=True` 사용자 전체 로드
- 각 사용자에 대해 `_process_user()` 순차 실행
  - `_fetch_user_profile` → `_derive_search_params` → `hybrid_search` → `generate_report` → `save_report`
- 실행 결과 집계: 성공 / 건너뜀(뉴스 없음 or 프로필 없음) / 실패 건수 출력

---

### 2-5. Airflow DAG — `dags/report_pipeline_dag.py`

- `dag_id`: `report_pipeline`
- `schedule_interval`: `0 2 * * *` (매일 02:00)
- `catchup=False`, `retries=1`, `retry_delay=5분`

| Task | 타입 | 내용 |
|------|------|------|
| `cluster_news` | BashOperator | `cluster_news.py 7` (최근 7일 뉴스 클러스터링) |
| `generate_reports` | BashOperator | `search_and_report.py 7` (클러스터 대표 기사 대상 검색 + 보고서) |

---

## 3. 잔여 작업 명세

### 3-1. DB 테이블 생성
**우선순위: 최고 / 예상 시간: 30분**

```bash
psql -h localhost -U ssafy -d streaming_data -f schema/report_tables.sql
```

또는 PostgreSQL 컨테이너 내부에서:
```bash
docker exec -i postgres psql -U ssafy -d streaming_data < schema/report_tables.sql
```

확인:
```sql
\dt report*   -- report_user_preferences, reports 테이블 존재 확인
\dt news_*    -- news_clusters 테이블 존재 확인
```

---

### 3-2. ElasticSearch 인프라 설정
**우선순위: 높음 / 예상 시간: 1시간**

> `data-pjt/elasticsearch/docker-compose.yml` 및 `Dockerfile`이 팀원에 의해 작성됨.  
> nori 플러그인은 Dockerfile에서 빌드 시 자동 설치(`analysis-nori --batch`).  
> 아래 항목부터 직접 수행 필요.

#### 3-2-2. ES `news` 인덱스 매핑 명시적 생성 스크립트 작성

> ES 인덱스는 외부 파이프라인(Flink 등)이 자동 생성하거나,
> `pub_date` date 타입 및 nori 한국어 분석기를 명시적으로 설정하려면 아래 스크립트 필요.

작성 위치: `schema/create_es_index.py`

```python
# schema/create_es_index.py (초안)
from elasticsearch import Elasticsearch

es = Elasticsearch("http://localhost:9200")
es.indices.create(
    index="news",
    body={
        "mappings": {
            "properties": {
                "pg_id":    {"type": "keyword"},
                "title":    {"type": "text", "analyzer": "nori"},
                "link":     {"type": "keyword"},
                "pub_date": {"type": "date"},
                "text":     {"type": "text", "analyzer": "nori"},
                "category": {"type": "keyword"},
                "keywords": {"type": "text"},
            }
        }
    },
    ignore=400,  # 이미 존재하면 무시
)
```

#### 3-2-3. ES 데이터 적재 확인

ES 인덱스에 뉴스 데이터가 실제로 들어와 있는지 확인:

```bash
curl http://localhost:9200/news/_count
# {"count": N, ...} — N > 0 이면 정상
```

---

### 3-3. Airflow 인프라 설정
**우선순위: 높음 / 예상 시간: 1시간**

> `data-pjt/airflow/docker-compose.yml`이 팀원에 의해 작성됨.  
> DB 마이그레이션 및 admin 계정 생성은 `airflow-init` 서비스가 자동으로 처리.  
> 아래 항목부터 직접 수행 필요.

#### 3-3-1. Airflow에서 필요한 환경 변수 전달

BashOperator/SparkSubmitOperator가 `.env`를 읽지 못하므로
Airflow Variables 또는 `airflow/docker-compose.yml`의 `environment` 섹션에 직접 등록 필요:

```bash
# Airflow Variables로 등록하는 방법
docker exec airflow-webserver airflow variables set DB_HOST postgres
docker exec airflow-webserver airflow variables set DB_NAME streaming_data
docker exec airflow-webserver airflow variables set DB_USER ssafy
docker exec airflow-webserver airflow variables set DB_PASSWORD <값>
docker exec airflow-webserver airflow variables set ES_HOST http://elasticsearch:9200
docker exec airflow-webserver airflow variables set OPENAI_API_KEY <값>
```

---

### 3-4. 환경 변수 `.env` 추가 항목
**우선순위: 높음 / 예상 시간: 10분**

기존 `.env`에 아래 항목 추가:

```env
# ElasticSearch
ES_HOST=http://localhost:9200
ES_PORT=9200
ES_USER=elastic
ES_PASSWORD=          # xpack.security.enabled=false 시 빈 값

# Airflow (DAG 경로, Spark 경로)
SPARK_HOME=/opt/spark
REPORT_DIR=/opt/airflow/Report
```

---

### 3-5. Spark JDBC 드라이버 준비
**우선순위: 중간 / 예상 시간: 30분**

Spark에서 PostgreSQL JDBC 연결에 `postgresql-42.7.3.jar`가 필요.  
`--packages` 옵션으로 자동 다운로드되지 않는 환경이면 수동 배포 필요.

```bash
# Maven 저장소에서 직접 다운로드
wget https://jdbc.postgresql.org/download/postgresql-42.7.3.jar \
  -O /opt/spark/jars/postgresql-42.7.3.jar
```

---

### 3-6. 단위 테스트 실행 확인
**우선순위: 중간 / 예상 시간: 30분** *(작성 완료 — 실행 확인만 남음)*

작성 위치: `data-pjt/Report/tests/`

| 테스트 파일 | 검증 항목 |
|-------------|-----------|
| `test_vector_search.py` | 쿼리 임베딩 생성 확인, Top-K 결과 수 검증, score 범위(0~1), representative_ids 필터 동작 |
| `test_es_search.py` | ES 연결 확인, 키워드 매칭 결과 반환, 날짜 필터 동작, representative_ids 필터 동작 |
| `test_hybrid_search.py` | RRF 점수 정렬 검증, dedup 동작 확인, use_clusters 폴백 동작 |
| `test_cluster_news.py` | 클러스터 수 k 자동 계산 검증, 대표 기사 선정 결과 확인, news_clusters 저장 확인 |
| `test_report_generator.py` | 프로필 없는 사용자 처리, JSON 파싱 실패 시 fallback 동작 |
| `test_save_report.py` | 매 실행마다 새 행 INSERT, run_date 컬럼 정상 저장 확인 |

---

### 3-7. `report_user_preferences` 초기 데이터 입력 전략 결정
**우선순위: 중간 / 예상 시간: 1~2시간 (의사결정 포함)**

현재 구현에서는 `report_user_preferences`가 비어 있으면 `consulting_financial_data`의 자산 보유 여부만으로 키워드를 자동 유도한다.  
다음 중 방향을 결정해야 한다:

| 방안 | 설명 | 장점 | 단점 |
|------|------|------|------|
| **A. 수동 등록** | 관리자가 직접 사용자별 관심사 입력 | 정확도 높음 | 운영 부담 |
| **B. 상담 완료 시 자동 생성** | `consulting_sessions.status='completed'` 이벤트에서 자동 INSERT | 자동화 | 백엔드 코드 수정 필요 |
| **C. 기본값만 사용** | `report_user_preferences` 테이블 없이 자산 기반 키워드만 사용 | 즉시 동작 | 개인화 부족 |

---

### 3-8. 보고서 API 연동 (백엔드)
**우선순위: 낮음 / 예상 시간: 2~3시간**

현재 보고서는 `reports` 테이블에만 저장되며, 프론트엔드에서 조회할 API가 없다.  
`backend-pjt`에 아래 엔드포인트 추가 필요:

```
GET /api/reports/          — 로그인 사용자의 보고서 목록 (최신순)
GET /api/reports/{id}/     — 특정 보고서 상세 (content JSONB 반환)
```

Django 앱: `backend-pjt/reports/` (신규 앱 생성 또는 `consulting` 앱 확장)

---

### 3-9. 통합 테스트 (E2E)
**우선순위: 낮음 / 예상 시간: 2~3시간**

인프라 구축 완료 후 전체 파이프라인 실행 확인:

```bash
# 1. 뉴스 클러스터링 실행
spark-submit spark_jobs/cluster_news.py 7

# 2. 클러스터 결과 확인
psql -U ssafy -d news -c \
  "SELECT COUNT(*), run_date FROM news_clusters GROUP BY run_date;"

# 3. 단일 사용자로 보고서 생성 테스트
python -c "
from generator.report_generator import _fetch_user_profile, _derive_search_params, generate_report, save_report
from search.hybrid_search import hybrid_search

user_id = 1
profile = _fetch_user_profile(user_id)
q, kw, cats = _derive_search_params(profile)
news = hybrid_search(q, kw, cats, top_k=10, days=7)
content = generate_report(user_id, news)
report_id = save_report(user_id, [n['id'] for n in news], content)
print('report_id:', report_id)
print('summary:', content['summary'])
"

# 4. 전체 배치 실행
spark-submit spark_jobs/search_and_report.py 7

# 5. DB 확인
psql -U ssafy -d streaming_data -c \
  "SELECT user_id, period_start, run_date, summary FROM reports ORDER BY created_at DESC LIMIT 5;"
```

---

## 4. 미결 의사결정 사항

| # | 항목 | 현재 상태 | 결정 필요 내용 |
|---|------|-----------|----------------|
| 1 | `report_user_preferences` 데이터 입력 방식 | 미결 | 수동/자동/기본값 중 선택 → [3-7](#3-7-report_user_preferences-초기-데이터-입력-전략-결정) |
| 2 | ES 인증 방식 | `xpack.security.enabled=false` (개발) | 운영 환경 전환 시 Basic Auth 또는 API Key 적용 필요 |
| 3 | Spark 실행 모드 | `local[*]` (단일 머신) | 사용자 수 증가 시 Standalone Cluster 또는 YARN 전환 검토 |
| 4 | 보고서 생성 주기 | 매일 02:00 | 주간 보고서로 변경 시 `0 2 * * 1` (월요일 02:00)으로 수정 |
| 5 | 백엔드 API 연동 | 미구현 | 보고서 조회 API 추가 여부 및 담당자 결정 |

---

## 5. 파일 구조 최종 현황

```
data-pjt/Report/
├── docs/
│   ├── plan.md              ✅ 완료 — 전체 구현 계획
│   └── 01init.md            ✅ 완료 — 이 문서
├── schema/
│   └── report_tables.sql    ✅ 완료 — 미적용(DB 실행 필요)
├── search/
│   ├── __init__.py          ✅ 완료
│   ├── vector_search.py     ✅ 완료
│   ├── es_search.py         ✅ 완료
│   └── hybrid_search.py     ✅ 완료
├── generator/
│   ├── __init__.py          ✅ 완료
│   └── report_generator.py  ✅ 완료
├── spark_jobs/
│   ├── cluster_news.py      ✅ 완료
│   └── search_and_report.py ✅ 완료
├── dags/
│   └── report_pipeline_dag.py ✅ 완료
└── tests/                   ✅ 완료 (conftest + 6개 테스트 파일)
```

---

## 6. 즉시 실행 가능한 다음 단계 (우선순위 순)

```
1. schema/report_tables.sql → PostgreSQL 실행                           (30분)
2. ES 인덱스 매핑 스크립트(schema/create_es_index.py) 작성 및 실행     (30분)
3. ES 데이터 적재 확인                                                  (10분)
4. Airflow 환경변수 등록 (streaming_data DB, ES, OpenAI)               (20분)
5. DAG 경로 문제 해결 (아래 별도 보고 참조)                            (30분)
6. cluster_news.py 단독 실행 및 news_clusters 결과 확인                (30분)
7. 단일 사용자 E2E 테스트 실행 (3-9 Python 스니펫)                     (1시간)
8. DAG 수동 트리거 테스트                                               (30분)
```
