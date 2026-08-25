# Report 자동화 보고서 시스템 구현 계획

## 개요

`data-pjt/Report`에서 일정 주기마다 DB에 저장된 사용자 정보 각각에 대해,
최신 뉴스를 하이브리드 검색(PostgreSQL pgvector + ElasticSearch)으로 찾고,
주요 정보를 보고서 형태로 생성하는 배치 파이프라인.

**자동화 스택**: Airflow (스케줄링) + Spark (배치 처리)

---

## 현재 인프라 상태

| 컴포넌트 | 상태 | 비고 |
|----------|------|------|
| PostgreSQL + pgvector | 완성 | 뉴스 임베딩 1536-dim (text-embedding-3-small) |
| ElasticSearch | 패키지 설치됨, 미구현 | elasticsearch==8.17.1 |
| Airflow | 미구현 | docker-compose 추가 필요 |
| Spark | 빈 디렉토리 | pyspark==3.5.4 설치됨 |
| 뉴스 파이프라인 | 완성 | Naver API → Kafka → Flink → PostgreSQL |
| 사용자/보고서 테이블 | 미구현 | schema/report_tables.sql 참조 |

### 기존 DB 테이블 (참조)

```
news               - title, link, pub_date, text, category, keywords(JSON), embedding(VECTOR 1536)
accounts_users     - id, email, name, birth_year, is_active
consulting_sessions          - user_id, status, current_stage
consulting_financial_data    - goal_type, target_retirement_age, assets_*, monthly_income, monthly_expense
```

---

## 디렉토리 구조

```
data-pjt/Report/
├── docs/
│   └── plan.md                     # 이 파일
├── schema/
│   └── report_tables.sql           # 신규 테이블 스키마
├── search/
│   ├── __init__.py
│   ├── vector_search.py            # pgvector cosine similarity 검색
│   ├── es_search.py                # ElasticSearch BM25 검색
│   └── hybrid_search.py            # RRF 융합
├── generator/
│   ├── __init__.py
│   └── report_generator.py         # LLM 보고서 생성 + DB 저장
├── spark_jobs/
│   ├── sync_news_to_es.py          # PostgreSQL → ES 증분 동기화
│   └── search_and_report.py        # 사용자별 검색 + 보고서 생성
└── dags/
    └── report_pipeline_dag.py      # Airflow DAG (매일 02:00)
```

---

## Phase별 구현 계획

### Phase 1 — 인프라 세팅 (1~1.5일)

| 작업 | 예상 시간 | 비고 |
|------|-----------|------|
| ElasticSearch Docker 설정 + 인덱스 구성 | 2~3h | docker-compose.yml에 ES 서비스 추가 |
| Airflow Docker 설정 | 2~3h | webserver + scheduler + worker |
| 신규 테이블 스키마 설계/적용 | 1~2h | report_tables.sql |

> **이 저장소에서는 테이블 스키마 설계만 완료. Docker 설정은 별도 진행.**

**신규 테이블:**
- `report_user_preferences` — 사용자별 관심 키워드/카테고리 설정
- `reports` — 생성된 보고서 저장

---

### Phase 2 — ElasticSearch 뉴스 동기화 (0.5~1일)

| 작업 | 예상 시간 |
|------|-----------|
| PostgreSQL → ES 동기화 Spark 잡 | 3~4h |
| pub_date 기반 증분 동기화 지원 | 포함 |

**파일**: `spark_jobs/sync_news_to_es.py`

---

### Phase 3 — 하이브리드 검색 모듈 (1~1.5일)

| 작업 | 예상 시간 |
|------|-----------|
| pgvector 벡터 검색 | 2~3h |
| ElasticSearch BM25 검색 | 2~3h |
| RRF(Reciprocal Rank Fusion) 결합 | 2~3h |

```
사용자 관심사
    ├── embedding → pgvector cosine search → rank_pg
    └── keywords → ES BM25 search          → rank_es
                           ↓
                  RRF fusion → 최종 Top-K 뉴스
```

**파일**: `search/vector_search.py`, `search/es_search.py`, `search/hybrid_search.py`

---

### Phase 4 — 보고서 생성 모듈 (1~1.5일)

| 작업 | 예상 시간 |
|------|-----------|
| 사용자 프로필 → 검색 파라미터 유도 | 1~2h |
| LLM 기반 보고서 생성 (GPT-4o-mini) | 3~4h |
| 보고서 DB 저장 | 1~2h |

**파일**: `generator/report_generator.py`

**보고서 출력 형식 (JSON):**
```json
{
  "summary": "이번 주 주요 요약",
  "sections": [
    {
      "category": "정책",
      "headline": "핵심 내용 한 줄",
      "detail": "사용자에게 미치는 영향 및 시사점"
    }
  ],
  "action_items": ["취할 수 있는 행동 1", "행동 2"]
}
```

---

### Phase 5 — Spark 배치 잡 (1~1.5일)

| 작업 | 예상 시간 |
|------|-----------|
| 활성 사용자 로드 (JDBC) | 0.5h |
| 사용자별 hybrid_search 호출 | 2~3h |
| 사용자별 report_generator 호출 | 1~2h |

**파일**: `spark_jobs/search_and_report.py`

---

### Phase 6 — Airflow DAG (0.5~1일)

**스케줄**: 매일 오전 02:00

```
sync_news_to_es → generate_reports → validate_reports
```

| Task | 역할 |
|------|------|
| sync_news_to_es | ES 증분 동기화 (전날 뉴스) |
| generate_reports | Spark 배치 잡 실행 |
| validate_reports | 생성 건수 검증 |

**파일**: `dags/report_pipeline_dag.py`

---

### Phase 7 — 테스트 및 검증 (1일)

| 작업 | 예상 시간 |
|------|-----------|
| 단위 테스트 (검색 정확도, 보고서 형식) | 2~3h |
| 통합 테스트 (E2E 전체 파이프라인) | 2~3h |
| 모니터링 설정 (Airflow 알림, Spark 로그) | 1~2h |

---

## 전체 일정 요약

| Phase | 작업 | 예상 시간 |
|-------|------|-----------|
| 1 | 인프라 세팅 | 1~1.5일 |
| 2 | ES 뉴스 동기화 | 0.5~1일 |
| 3 | 하이브리드 검색 모듈 | 1~1.5일 |
| 4 | 보고서 생성 모듈 | 1~1.5일 |
| 5 | Spark 배치 잡 | 1~1.5일 |
| 6 | Airflow DAG | 0.5~1일 |
| 7 | 테스트 및 검증 | 1일 |
| **합계** | | **6~8일** |

---

## 환경 변수 (추가 필요)

```env
# 기존
DB_HOST=localhost
DB_PORT=5432
DB_NAME=streaming_data
DB_USER=ssafy
DB_PASSWORD=ssafy
OPENAI_API_KEY=<GMS_KEY>

# 신규 추가
ES_HOST=http://localhost:9200
ES_PORT=9200
ES_USER=elastic
ES_PASSWORD=
```
