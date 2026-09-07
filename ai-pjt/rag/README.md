# RAG pipeline

`rag.pipeline`은 승인된 운영 문서와 분리된 synthetic corpus로 수집, Markdown 구조
청킹, embedding, 버전형 upsert, 하이브리드 검색 평가와 승격을 재현한다.

## Synthetic 실행

`ai-pjt`에서 다음 명령을 실행한다.

```bash
python3 -m rag.pipeline.cli run-synthetic \
  --workspace /tmp/retirewise-rag-synthetic
```

명령은 `rag/fixtures/synthetic`의 corpus와 gold query를 사용하고 다음 결과를
workspace에 기록한다.

- 버전별 collection과 chunk vector
- corpus, chunker, embedding, index 버전을 포함한 manifest
- Recall@5, MRR@5, context precision과 query별 검색 결과
- 평가 기준을 통과한 버전만 가리키는 활성 collection 포인터

같은 corpus와 설정으로 같은 활성 버전을 다시 실행하면 기존 manifest를
재사용한다. 활성 버전의 입력이나 설정이 달라지면 덮어쓰지 않고 새
`--index-version`을 요구한다.

## 운영 후보 corpus

운영 후보의 기계 단일 원천은 `rag/data/catalog.json`이다. 2026-09-07 기준으로
인터페이스 가이드 10개와 금융 지식 문서 14개가 등재되어 있으며, catalog 최상위
`acceptance`에 필수·권장 지식의 도메인 승인과 서비스 경계를 기록했다.

금융 지식은 은퇴진단에 필요한 재무상태·현금흐름, 내부 가계재무지표, 목표와
화폐의 시간가치, 계산 기본 가정, 국민·퇴직·개인·주택연금, 부채·자산·부동산,
상담 윤리를 포함한다. 보험·교육·상속·투자상품 전용 설계와 상품 추천·실행은
포함하지 않는다. 참고 PDF 원문과 사용자별 재무정보도 적재하지 않는다.

운영 adapter는 catalog의 승인·출처·버전·적용 범위·라이선스·민감도와 파일
SHA-256을 `DocumentMetadata`로 변환한다. 경로가 collection pattern을 벗어나거나
파일이 없거나 SHA-256이 다르면 embedding 호출 전에 실패한다.

## 승인 corpus 재현 검사

외부 API와 DB 없이 승인 corpus 24개와 gold query 58개를 검사한다.

```bash
python3 -m rag.pipeline.cli run-catalog-dry-run \
  --workspace /tmp/retirewise-rag-catalog
```

이 검사는 인터페이스 질문 20개와 금융 지식·경계·근거 없음 질문 38개를 분리해
Recall@5 0.85 이상, MRR@5 0.70 이상, provenance 완전성 100%, collection 혼입 0건,
근거 없음 질문의 false evidence 0건을 요구한다. deterministic embedding 결과는
파이프라인 회귀검사용이며 운영 Gemini 품질을 대신하지 않는다.

## 운영 PostgreSQL 색인

운영 명령은 `DATABASE_URL`, `GOOGLE_API_KEY`, Gemini `gemini-embedding-001`과
3,072차원 설정을 사용한다. 웹 서버 시작 경로에서는 schema 생성, 색인 또는 활성
포인터 변경을 하지 않는다.

```bash
python3 -m rag.pipeline.cli run-operational \
  --report-dir /tmp/retirewise-rag-reports \
  --index-version catalog-2-1-0
```

기본 실행은 새 physical collection을 만들고 평가하지만 활성화하지 않는다. 평가를
통과한 인터페이스 가이드는 다음처럼 명시적으로 승격한다.

```bash
python3 -m rag.pipeline.cli run-operational \
  --report-dir /tmp/retirewise-rag-reports \
  --collection interface-guides \
  --index-version catalog-2-1-0 \
  --promote
```

`financial-knowledge`는 별도 계산기 정합화 작업이 완료되기 전까지 승격을 막는다.
완료 뒤에만 `--promote --confirm-calculator-aligned`를 함께 사용한다. 승격은
`rag_active_indexes` 포인터를 transaction 안에서 교체하며 이전 활성 버전에는
retired 시각을 기록한다. retired 버전은 기본 30일 뒤 다음 승격 명령에서 정리한다.

## 안전 경계

- 비운영 실행은 이름이 `synthetic_`으로 시작하는 collection만 허용한다.
- synthetic collection에는 검토자 정보가 있고 `review_status=synthetic`인 문서만
  적재한다.
- 운영 target은 검토자 정보가 있고 `review_status=approved`인 문서만 허용한다.
- 금융 지식과 인터페이스 가이드는 같은 collection에 섞지 않는다.
- 새 버전은 별도 physical collection에 만든다. 평가가 실패하면 기존 활성
  버전은 바뀌지 않으며 새 버전도 활성화할 수 없다.
- synthetic fixture는 실제 금융·세무 판단의 근거로 사용하지 않는다.

## Adapter와 F06 경계

자동 테스트는 외부 호출이 없는 `DeterministicHashEmbedding`을 사용한다.
운영 provider는 이미 생성된 LlamaIndex embedding 객체를
`LlamaIndexEmbeddingAdapter`에 주입한다. Provider 패키지를 pipeline import
시점에 불러오지 않으므로 synthetic 검증은 API key나 모델 네트워크에
의존하지 않는다.

`IndexedRetriever.retrieve()`는 활성 logical collection을 해석한다. PostgreSQL의
cosine dense 검색과 전체 활성 collection의 BM25 결과를 각각 최대 10개 가져와 RRF로
합치고 최종 5개를 반환한다. 결과에는 `source_id`, catalog 문서 ID, 제목, 발행기관,
원문 위치, 문서 버전·기준일·SHA-256, chunk ID·offset, dense/sparse/fusion 점수가 있다.

`rag.retriever`는 이 경계를 QA와 function tool용 비동기 함수에 연결한다. 활성 인덱스가
없거나 저장소를 읽을 수 없으면 승인 근거가 없다는 제한을 반환하며 구형 단일
`retirement_docs` collection으로 폴백하지 않는다.

## 테스트

`ai-pjt`에서 실행한다.

```bash
python3 -m unittest discover -s rag/tests -v
```
