# RAG 자료 현재 문제와 D6 인수 범위

상태: **F06-T01~T05 완료 — 실제 Gemini 운영 승격은 계산기 정합화 뒤 수행**

이 문서는 기준 커밋 `ef3f8b7`에서 관찰한 RAG 자료와 검색 코드의 문제, 과거 P08에서
처리한 범위와 현재 F06에 남은 작업을 기록한다. 채택된 데이터 계약을 변경하지 않으며
`backend-pjt/consulting/field_registry.py`를 읽기 전용 단일 원천으로 사용한다.

## 기준 상태에서 확인한 문제

### 금융 지식과 인터페이스 가이드 혼합

기존 `ai-pjt/rag/data/*.md` 10개는 단계·필드 입력을 설명하는 인터페이스 가이드다. 그러나 출처와 기준일이 없는 평균 소득·평균 자산·금리·기대수익률·연금 세제·권장 연령도 같은 파일에 들어 있었다. 이 상태에서는 입력 보조용 규칙과 QA 답변용 금융 사실을 서로 다른 collection으로 선별할 수 없다.

### canonical 필드 불일치

10개 가이드의 `## field_name`을 canonical 필드 레지스트리와 대조했을 때 모든 파일에서 누락 또는 과거 이름을 확인했다.

- `basic-info`: `earnings`, `marital` 대신 `monthly_income`, `marital_status`를 사용해야 한다.
- `financial-mindset`: 기존 4개 camelCase 필드는 현재 6개 정성 필드와 일치하지 않는다.
- `financial-satisfaction`: 기존 점수 필드 6개가 현재 `*_score` 5개 필드와 일치하지 않는다.
- 자산·부채·소득·지출 문서는 `value`, `monthly`, `fixed`, `rate` 같은 과거 이름을 사용했다.
- `financial-goals`는 camelCase 기간 필드와 누락된 phase·category·content·note 필드를 포함하고 있었다.

과거 이름은 저장 또는 AI 변경 계약으로 사용할 수 없으므로 D6 가이드를 현재 레지스트리와 맞췄다.

### 문서 metadata와 생명주기 부재

기존 문서는 `step_id` 외에 출처 식별자, 발행기관, 원문, 기준일, 버전, 적용 범위가 없었다. 어떤 문서를 다시 확인하거나 폐기해야 하는지 기계적으로 판정할 catalog도 없었다.

### 기준 상태의 runtime 경계 미구현

- `ai-pjt/rag/markdown_retriever.py`는 `rag/data/{step_id}.md` 평면 경로와 `markdown_nodes` 테이블을 고정 사용한다.
- `ai-pjt/rag/retriever.py`는 하나의 `settings.rag_collection`을 사용하며 문서 유형이나 catalog 기준으로 검색 대상을 나누지 않는다.
- QA 인용은 node metadata의 `source`만 표시하고 `source_id`, 문서 버전, 기준일, 근거 구간을 전달하지 않는다.
- 운영 적재, 재색인, 활성 인덱스 전환과 검색 품질 평가는 당시 확인되지 않았다.

## D6에서 처리한 범위

- 기존 10개 파일은 평면 경로에 유지하여 현재 직접 조회 코드를 깨뜨리지 않았다.
- 인터페이스 가이드에서 출처 없는 외부 통계·수익률·권장값을 제거하고 canonical 필드명·자료형·단위·제약으로 정리했다.
- 공식 1차 출처를 확인한 금융 지식 문서를 `ai-pjt/rag/data/financial-knowledge/`에 분리했다.
- `ai-pjt/rag/data/catalog.json`에 collection, 문서 원본, 기준일, 버전, 적용 범위, 갱신·폐기 조건과 내용 hash를 기록했다.

## F06-T01·T02에서 추가로 처리한 범위

- 2026-09-07 기준 필수·권장 도메인 지식을 재조사해 금융 지식 문서를 3개에서 14개로 확장했다.
- 공식 제도 사실과 프로젝트 내부 지표·계산 시나리오를 구분하고, 각 문서에 적용 범위와 갱신·폐기 기준을 기록했다.
- 서비스 경계를 은퇴진단까지로 고정하고 상품 추천·실행, 보험·교육·상속·투자상품 전용 설계를 제외했다.
- 한국FPSB 윤리규정과 업무수행기준은 상담 원칙으로 재작성했으며, 사용자 제공 PDF 원문과 사용자별 재무정보는 적재 대상에서 제외했다.
- catalog 최상위 `acceptance`에 승인일, 승인 범위, 서비스 경계, 검토 역할, 라이선스와 민감도 정책을 기록했다.
- 검사 결과와 채택 목록은 `notes/acceptance-audit.md`에 기록했다.

## F06-T03~T05 구현 결과

1. catalog 승인 ID·version allowlist, 허용 경로와 SHA-256을 embedding 전에 검증한다.
2. `interface-guides`와 `financial-knowledge`를 분리된 logical/physical collection으로 저장한다.
3. Markdown 구조 청킹과 Gemini embedding 경계, PostgreSQL/pgvector store를 구현했다.
4. Dense·BM25 후보를 RRF로 결합하고 결과에 전체 provenance와 검색 점수를 연결했다.
5. 평가 통과 뒤 transaction으로 active pointer를 전환하고 retired 버전을 30일 보관한다.
6. 승인 문서 24개와 58개 gold query를 deterministic·임시 pgvector에서 검증했다. 인터페이스 Recall@5/MRR@5는 0.950/0.912, 금융 지식은 1.000/0.880이며 provenance 100%, collection 혼입과 false evidence는 0건이다.

실제 Gemini 평가는 배포환경의 `GOOGLE_API_KEY`와 `DATABASE_URL`을 주입해 다시 실행한다.
계산기 정합화는 F09-T07 별도 작업이며 완료 전 `financial-knowledge` 운영 승격은 명령이
거부한다. 이 제한은 인터페이스 승격과 비활성 후보 build·평가를 막지 않는다.
