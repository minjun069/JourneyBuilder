# 데이터 파이프라인 연동 가이드

> 작성일: 2026-06-23
> 업데이트: 2026-06-24
> Report 모듈이 정상 동작하려면 뉴스 수집·저장 파이프라인이 아래 요구사항을 충족해야 합니다.

---

## 1. 임베딩 모델

### 현재 상태

| 구분 | 모델 | 위치 | 상태 |
|------|------|------|------|
| 뉴스 저장 시 문서 임베딩 | `gemini-embedding-001` (Google) | `common/llm_client.py`, `news/processor/process.py` | 변경 완료 |
| 보고서 검색 쿼리 임베딩 | `gemini-embedding-001` (Google) | `Report/search/vector_search.py` | 변경 완료 |

뉴스 저장 임베딩과 보고서 검색 쿼리 임베딩은 같은 Gemini embedding 모델을 사용합니다.
`news.embedding` 컬럼은 기존 `VECTOR(1536)`을 유지하므로 양쪽 모두 `output_dimensionality=1536`으로 맞춥니다.

### 임베딩 설정

뉴스 문서 임베딩은 `data-pjt/common/llm_client.py`의 `transform_to_embedding()`에서 생성합니다.

```python
def transform_to_embedding(text: str) -> list[float]:
    result = genai.embed_content(
        model="models/gemini-embedding-001",
        content=text,
        task_type="RETRIEVAL_DOCUMENT",
        output_dimensionality=1536,  # DB VECTOR(1536) 컬럼과 차원 수 일치
    )
    return result["embedding"]
```

보고서 검색 쿼리 임베딩은 `data-pjt/Report/search/vector_search.py`에서 같은 모델을 사용하되 `task_type="RETRIEVAL_QUERY"`로 생성합니다.

뉴스 임베딩 입력은 현재 `title + "\n" + text` 형식입니다. 제목의 핵심 키워드와 본문 맥락을 함께 반영하기 위한 설정입니다.

### 환경 변수 추가

```env
GEMINI_API_KEY=<발급받은 키>
```

기존 `OPENAI_API_KEY` 또는 `GMS_API_KEY`는 키워드 추출과 카테고리 분류에 여전히 사용되므로 제거하지 않습니다.
현재는 임베딩만 Gemini로 통일하고, 키워드/카테고리 분류는 기존 LLM 클라이언트 흐름을 유지합니다.

### 기존 뉴스 재임베딩

기존 `news.embedding` 값은 `data-pjt/news/processor/reembed_embeddings.py`로 Gemini 임베딩으로 재계산했습니다.

필요 시 아래처럼 범위를 나누어 다시 실행할 수 있습니다.

```bash
python -m news.processor.reembed_embeddings --min-id 1 --limit 10 --sleep 3
```

이 스크립트는 `category`, `tags`, `keywords`는 건드리지 않고 `embedding` 컬럼만 업데이트합니다.

---

## 2. PostgreSQL `news` 테이블 요구사항

### 필수 컬럼

Report 모듈이 사용하는 컬럼은 아래와 같습니다.

| 컬럼 | 타입 | 설명 |
|------|------|------|
| `id` | SERIAL | 기본 키. ES의 `pg_id`와 동일한 값이어야 함 |
| `title` | TEXT | 기사 제목 |
| `link` | TEXT | 기사 URL. **RRF 중복 제거 기준** |
| `pub_date` | DATE / TIMESTAMP | 게시일. `NOW() - INTERVAL N days` 필터에 사용 |
| `category` | VARCHAR | 카테고리. 아래 허용값 참고 |
| `embedding` | VECTOR(1536) | gemini-embedding-001 벡터 (1536-dim) |

### 카테고리 허용값

`report_generator.py`의 `ALL_CATEGORIES`와 일치해야 합니다.

```python
["정책·제도", "투자·재테크", "연금·은퇴", "금융상품", "자산운용", "기업·실적", "시장동향", "사회·경제"]
```

다른 값이 저장되면 카테고리 필터가 작동하지 않습니다. 분류 불가 시 `"미분류"` 또는 위 목록 중 가장 근접한 값을 사용하세요.

### `link` 중복 처리

동일한 URL의 뉴스가 중복 저장되면 RRF 점수가 분산됩니다. 수집 시 `link` 기준 중복 체크를 권장합니다.

```sql
INSERT INTO news (...) VALUES (...)
ON CONFLICT (link) DO NOTHING;
```

### `embedding` NULL 허용

임베딩 생성이 실패하면 `NULL`로 저장해도 됩니다. `cluster_news.py`와 `vector_search.py` 모두 `embedding IS NOT NULL` 조건으로 NULL을 제외합니다.

---

## 3. ElasticSearch `news` 인덱스 요구사항

### 필수 필드

| 필드 | 타입 | 설명 |
|------|------|------|
| `pg_id` | integer | PostgreSQL `news.id`와 동일한 값. **필수** |
| `title` | text | 기사 제목 (nori 분석기 적용 권장) |
| `text` | text | 본문 (nori 분석기 적용 권장) |
| `link` | keyword | 기사 URL |
| `pub_date` | date | 게시일 (ISO 8601: `2026-06-23T00:00:00`) |
| `category` | keyword | 카테고리 (위 허용값과 동일) |
| `keywords` | text | 추출된 키워드 목록 |

### `pg_id` 필드 필수

`es_search.py`는 `hit["_source"]["pg_id"]`를 PostgreSQL `news.id`로 사용합니다.  
`pg_id`가 없으면 `hybrid_search.py`의 RRF 중복 제거가 깨집니다.

현재 뉴스 적재 코드(`news/load/load.py`, `news/load/es_writer.py`)는 PostgreSQL upsert 후 반환된 `news.id`를 ES 문서의 `pg_id`로 함께 저장합니다.
이미 색인되어 있던 기존 ES 문서는 `news/load/backfill_es_pg_id.py`로 `pg_id`를 백필했습니다.

```python
doc = {
    "pg_id": news_id,   # PostgreSQL news.id 와 동일한 값
    "title": ...,
    "text": ...,
    "link": ...,
    "pub_date": ...,    # "2026-06-23T00:00:00" 형식
    "category": ...,
    "keywords": ...,
}
es_client.index(index="news", document=doc)
```

### `pub_date` 형식

ES의 `range` 필터가 ISO 8601 형식을 기준으로 동작합니다. PostgreSQL의 `pub_date`와 ES의 `pub_date`는 같은 날짜를 가리켜야 합니다.

---

## 4. 수집 타이밍

Report DAG는 매일 **오전 02:00**에 실행됩니다.  
뉴스 수집 및 ES 색인은 **02:00 이전**에 완료되어야 당일 보고서에 반영됩니다.

```
[뉴스 수집 파이프라인]  00:00 ~ 01:30  수집 → PostgreSQL 저장 + ES 색인
[cluster_news]          02:00           KMeans 클러스터링
[search_and_report]     02:00 ~         보고서 생성
```

---

## 5. (선택) DB 컬럼 3072-dim으로 변경하는 경우

현재 `output_dimensionality=1536`으로 제한하고 있으나, 더 높은 차원을 사용하려면:

```sql
ALTER TABLE news DROP COLUMN embedding;
ALTER TABLE news ADD COLUMN embedding VECTOR(3072);
```

이후 재임베딩 시 `output_dimensionality=3072`로 변경하고,  
`Report/search/vector_search.py`의 `_EMBEDDING_DIM = 3072`로 함께 수정합니다.
