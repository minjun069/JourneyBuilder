# data-pjt

뉴스 수집 → 처리(키워드/카테고리/임베딩) → PostgreSQL/Elasticsearch 적재 배치 파이프라인.

## 폴더 구조

DB 스키마는 전사 공통 인프라이므로 프로젝트 루트(`de_pjt/db/`)에 위치합니다.

```
de_pjt/
├── db/                   # DB 인프라 (전사 공통)
│   └── postgres/
│       └── init.sql      # pgvector 확장 + 권한 설정
│
└── data-pjt/
    ├── airflow/          # Airflow 인프라 (공통)
    │   ├── docker-compose.yml
    │   └── dags/         # 모든 DAG (news / reports 공용)
    ├── hadoop/           # HDFS 인프라 (공통)
    ├── spark/            # Spark 인프라 (공통)
    ├── common/           # 공통 유틸 (llm_client, hdfs_client, db, logger)
    ├── news/             # 뉴스 도메인 (담당: 수경)
    │   ├── collector/    # 네이버 경제 섹션 수집(list.naver + requests/BS4)
    │   ├── processor/    # 정제 / 키워드 / 카테고리 / 임베딩
    │   └── load/         # PostgreSQL / Elasticsearch 적재
    ├── reports/          # 리포트 도메인 (담당: 민준)
    │
    ├── producer/         # (기존) Kafka 실시간 파이프라인 - 참조용
    └── consumer/         # (기존) Flink 실시간 파이프라인 - 참조용
```

## 경계 기준
- **DB 인프라**(스키마) → 프로젝트 루트 `de_pjt/db/` (backend / ai 등 전사 공유)
- **인프라**(airflow / hadoop / spark) → `data-pjt/` 최상위 공통
- **유틸리티**(LLM 호출, HDFS, DB 연결) → `common/`
- **도메인 로직** → `news/`, `reports/` 각자

## import 규칙
`data-pjt`가 패키지 루트입니다. 실행 시 `PYTHONPATH`에 `data-pjt`를 포함하면
`from common.llm_client import ...` 형태로 import 가능합니다.

```bash
export PYTHONPATH=/home/ssafy/de_pjt/data-pjt:$PYTHONPATH
```

## 파이프라인 흐름
```
Airflow → 뉴스 수집 → HDFS(raw) → 처리/LLM enrich → HDFS(enriched) → PostgreSQL(+pgvector) / Elasticsearch
```
매일 새벽 2시 자동 실행 (1일 단위 배치).

뉴스 수집기는 네이버 `list.naver`의 날짜 페이지네이션으로 대상일 경제 기사 목록을
결정적으로 수집하고, 각 기사 본문은 Selenium 없이 `requests`와 BeautifulSoup으로
추출합니다.
