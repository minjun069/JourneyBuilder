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

운영 후보의 기계 단일 원천은 `rag/data/catalog.json`이다. 2026-09-17 기준으로
인터페이스 가이드 10개와 금융 지식 문서 15개가 등재되어 있으며, catalog 최상위
`acceptance`에 필수·권장 지식의 도메인 승인과 서비스 경계를 기록했다.

금융 지식은 은퇴진단에 필요한 재무상태·현금흐름, 내부 가계재무지표, 목표와
화폐의 시간가치, 계산 기본 가정, 국민·퇴직·개인·주택연금, 부채·자산·부동산,
상담 윤리를 포함한다. 보험·교육·상속·투자상품 전용 설계와 상품 추천·실행은
포함하지 않는다. 참고 PDF 원문과 사용자별 재무정보도 적재하지 않는다.

`financial-knowledge/product-terms.md`는 제품 화면과 상담에서 노출하는 용어의 정의,
별칭, 사용 맥락과 주의사항을 Markdown으로 소유한다. 각 용어 절은 별도 청크로 색인되며
별도의 용어 JSON이나 QA 고정 답변을 사용하지 않는다.

운영 adapter는 catalog의 승인·출처·버전·적용 범위·라이선스·민감도와 파일
SHA-256을 `DocumentMetadata`로 변환한다. 경로가 collection pattern을 벗어나거나
파일이 없거나 SHA-256이 다르면 embedding 호출 전에 실패한다.

## 승인 corpus 재현 검사

외부 API와 DB 없이 승인 corpus 25개와 gold query 66개를 검사한다.

```bash
python3 -m rag.pipeline.cli run-catalog-dry-run \
  --workspace /tmp/retirewise-rag-catalog
```

이 검사는 인터페이스 질문 20개와 금융 지식·제품 용어·경계·근거 없음 질문 46개를 분리해
Recall@5 0.85 이상, MRR@5 0.70 이상, provenance 완전성 100%, collection 혼입 0건,
근거 없음 질문의 false evidence 0건을 요구한다. deterministic embedding 결과는
파이프라인 회귀검사용이며 운영 Gemini 품질을 대신하지 않는다.

## F06-T06 로컬 DB와 공식 색인

RAG는 애플리케이션과 같은 PostgreSQL의 `rag_index_manifests`,
`rag_index_chunks`, `rag_active_indexes`를 사용한다. 별도 RAG DB 접속 설정을
애플리케이션에 추가하지 않는다. `rag/docker-compose.yml`은 RAG 담당자가 실제
색인과 artifact를 만드는 동안 쓸 수 있는 로컬 애플리케이션 DB 하네스다. Compose
project와 volume 이름에 `RAG_INSTANCE_ID`를 넣으므로 개발자별 데이터가 격리된다.

```bash
cd ai-pjt/rag
mkdir -p .local
cp ops.env.example .local/ops.env
```

`.local/ops.env`의 `RAG_INSTANCE_ID`, `RAG_POSTGRES_PASSWORD`, `GOOGLE_API_KEY`를 실제
값으로 설정한다. 이 파일은 Git에 추가하지 않는다.

```bash
set -a
source .local/ops.env
set +a
./ops.sh up
./ops.sh db-check
```

`stop`은 컨테이너만 멈추고 volume은 유지한다. 이 절차는 `down -v`를 제공하지
않으므로 실수로 생성 데이터를 지우지 않는다.

```bash
./ops.sh status
./ops.sh stop
```

실험 실행은 모든 개발자가 할 수 있다. `artifact_kind=experiment`가 파일명,
physical collection(`interface-guides__experiment-...`)과 DB manifest에 기록된다.

```bash
./ops.sh qualify \
  --report-dir .local/reports \
  --artifact-kind experiment \
  --environment-id <개발자-id> \
  --index-version catalog-2-2-0
```

### 공식 artifact 생성과 갱신

승인 corpus 변경, 공식 physical index, 공식 qualification report와 배포 artifact는
RAG 담당자만 만든다. 공식 실행은 생산자 식별자와 권한 확인을 반드시 기록하고,
GMS의 `gemini-embedding-2` 3,072차원만 허용한다. catalog와 승인된 계산 정책 파일을
읽어 각각의 SHA-256과 버전을 embedding 호출 전에 고정한다.

공식 실행 전에 `git status --short` 출력이 없어야 한다.

```bash
git status --short
./ops.sh qualify \
  --report-dir .local/reports \
  --artifact-kind official \
  --environment-id <rag-owner-db> \
  --operator-id <rag-owner-id> \
  --confirm-rag-owner \
  --index-version <catalog-version> \
  --promote
```

명령은 catalog에서 collection별 승인 문서를 읽고 Markdown chunk를 만든 다음 GMS
Gemini embedding을 호출해 새 physical index에 upsert한다. 이어 20개 인터페이스
질문과 46개 금융 질문을 각각 검색해 Recall@5, MRR@5, provenance, collection 혼입,
false evidence를 계산한다. 두 결과는 PASS/FAIL과 관계없이 collection 보고서와 하나의
`official__...__qualification.json`으로 확정된다. 두 collection이 모두 통과한 경우에만
`activatable=true`가 되며 `--promote`는 두 active pointer를 한 transaction에서 함께
바꾼다. 하나라도 실패하면 기존 pointer는 그대로다.

운영 검색은 원문 query의 dense 후보와 한국어 조사를 정규화한 BM25 후보를 weighted
reciprocal-rank fusion한다. BM25는 제목 4.0, 별칭 2.5, 본문 1.0의 필드 가중치를
사용하고 용어명 완전 일치, 별칭 완전 일치 순으로 보너스를 준다. 완전 일치 후보는
RRF에서도 한 번 더 우선하며 dense 후보도 최종 순위에 함께 반영한다.

dense cosine similarity가 `0.54` 미만인 후보는 제외한다. catalog 2.1.0의 정답
문서 최저 점수 `0.5401`은 보존하면서, catalog 2.2.0 용어 사전이 무관한 여행 질의에
반응한 dense 최고 점수 `0.5329`를 차단하도록 GMS `gemini-embedding-2`로 다시 측정한
경계다. 제목·별칭·본문 BM25 결과는 dense 경계와 독립적으로 RRF에 참여하므로
용어 정확 일치는 유지한다. catalog 2.2.0은 이 경계로 66문항 공식 qualification을
통과해 승격됐다.

`.local/reports/official__<catalog-version>__qualification.json`에서 다음을 모두
확인한다.

- 두 collection의 `decision`이 `PASS`
- `overall_decision=PASS`, `activatable=true`, `deployable=true`
- `activation.performed=true`이고 `activation.after`가 새 공식 physical ID와 일치
- Recall@5 0.85 이상, MRR@5 0.70 이상, provenance 1.0
- collection leakage 0건, false evidence 0건
- `embedding.model=gemini-embedding-2`, `dimensions=3072`
- `retrieval.minimum_dense_score=0.54`

하나라도 충족하지 않으면 export하지 않고 기존 active pointer를 유지한다. 전부
충족하면 다음을 실행한다.

```bash
./ops.sh export \
  --qualification .local/reports/official__<catalog-version>__qualification.json \
  --output-dir artifacts

PYTHONPATH=.. python3 -m rag.artifacts inspect \
  --artifact artifacts/official__official-<catalog-version>.tar.gz
sha256sum artifacts/official__official-<catalog-version>.tar.gz
./ops.sh stop
```

첫 artifact가 20 MiB 이하이면 일반 Git으로 커밋한다. 누적 크기가 문제가 될 때만
`artifacts/*.tar.gz`를 Git LFS로 전환한다.

### catalog 2.2.0 공식 실행 결과

- source revision `e1bfb4da08aef2ef09b5041e4f1e7582d28b51d4`, catalog SHA-256
  `06f58788a6b8db622d72ad7af4035f32a9cf2b9c564e2f6bcca7f6aa858d3102`, 계산 정책
  `2026-09-12.v1`/`972ffb38464183cad16f714cc200b962e7c8bf73d3151e0550bb3b7cb49fb7a7`를 고정했다.
- `interface-guides__official-catalog-2-2-0`은 10문서·38 chunk, Recall@5 `1.000`,
  MRR@5 `0.967`, provenance `1.000`, 혼입·false evidence 0건이다.
- `financial-knowledge__official-catalog-2-2-0`은 15문서·230 chunk, Recall@5 `1.000`,
  MRR@5 `0.911`, provenance `1.000`, 혼입·false evidence 0건이다. 5개 no-result 질의는
  모두 빈 결과를 반환했다.
- 전체 66문항은 `PASS`, `activatable=true`, `deployable=true`고, 두 active pointer를
  한 transaction에서 2.2.0 physical index로 전환했다.
- artifact는 `artifacts/official__official-catalog-2-2-0.tar.gz`, 4,429,692 bytes,
  SHA-256 `3e6e5e20e3e490578ad06a435275c9e196655ff915fe4d98b309e87435792a56`다.
- 독립 consumer의 최초 import는 pointer를 유지한 채 두 index를 삽입했고,
  활성화·재실행은 두 index를 byte-equivalent로 재사용하면서 38·230 chunk와
  active pointer를 일치시켰다.

## 논리 백업 artifact 승계

artifact에는 qualified physical ID의 RAG 테이블 행, active snapshot, qualification과
checksum manifest만 포함한다. Django 테이블과 DB 자격 증명은 포함하지 않는다.

소비자는 기존 RAG 데이터가 있는 같은 애플리케이션 DB에 가져올 수 있다. 같은
physical ID가 이미 있으면 manifest와 모든 chunk가 동일한 경우만 재사용하고, 다르면
덮어쓰지 않고 실패한다. import만 하면 현재 active pointer를 보존하며, 공식 PASS
artifact에 `--activate`를 준 경우에만 두 pointer를 함께 전환한다.

```bash
DATABASE_URL='postgresql://...' PYTHONPATH=.. \
  python3 -m rag.artifacts import \
  --artifact artifacts/official__official-<catalog-version>.tar.gz

DATABASE_URL='postgresql://...' PYTHONPATH=.. \
  python3 -m rag.artifacts import \
  --artifact artifacts/official__official-<catalog-version>.tar.gz \
  --activate
```

## 안전 경계

- 비운영 실행은 이름이 `synthetic_`으로 시작하는 collection만 허용한다.
- synthetic collection에는 검토자 정보가 있고 `review_status=synthetic`인 문서만
  적재한다.
- 운영 target은 검토자 정보가 있고 `review_status=approved`인 문서만 허용한다.
- 금융 지식과 인터페이스 가이드는 같은 collection에 섞지 않는다.
- 새 버전은 별도 physical collection에 만든다. 두 collection의 평가가 모두
  통과하기 전에는 기존 활성 버전을 바꾸지 않는다.
- synthetic fixture는 실제 금융·세무 판단의 근거로 사용하지 않는다.

## Adapter와 F06 경계

자동 테스트는 외부 호출이 없는 `DeterministicHashEmbedding`을 사용한다.
운영 provider는 이미 생성된 LlamaIndex embedding 객체를
`LlamaIndexEmbeddingAdapter`에 주입한다. Provider 패키지를 pipeline import
시점에 불러오지 않으므로 synthetic 검증은 API key나 모델 네트워크에
의존하지 않는다.

`IndexedRetriever.retrieve()`는 활성 logical collection을 해석한다. PostgreSQL의
cosine dense 검색과 전체 활성 collection의 필드 가중 BM25 결과를 각각 최대 10개 가져와 RRF로
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
