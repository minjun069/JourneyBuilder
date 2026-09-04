# RAG 자료 현재 문제와 D6 인수 범위

상태: **D6 corpus 정비 완료 — runtime 반영은 P09 대기**

이 문서는 기준 커밋 `ef3f8b7`에서 관찰한 RAG 자료와 검색 코드의 문제, D6에서 처리한 범위, P09에 남기는 작업을 기록한다. 채택된 데이터 계약을 변경하지 않으며 `backend-pjt/consulting/field_registry.py`를 읽기 전용 단일 원천으로 사용한다.

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

### runtime 경계 미구현

- `ai-pjt/rag/markdown_retriever.py`는 `rag/data/{step_id}.md` 평면 경로와 `markdown_nodes` 테이블을 고정 사용한다.
- `ai-pjt/rag/retriever.py`는 하나의 `settings.rag_collection`을 사용하며 문서 유형이나 catalog 기준으로 검색 대상을 나누지 않는다.
- QA 인용은 node metadata의 `source`만 표시하고 `source_id`, 문서 버전, 기준일, 근거 구간을 전달하지 않는다.
- 운영 적재, 재색인, 활성 인덱스 전환과 검색 품질 평가는 아직 확인되지 않았다.

## D6에서 처리한 범위

- 기존 10개 파일은 평면 경로에 유지하여 현재 직접 조회 코드를 깨뜨리지 않았다.
- 인터페이스 가이드에서 출처 없는 외부 통계·수익률·권장값을 제거하고 canonical 필드명·자료형·단위·제약으로 정리했다.
- 공식 1차 출처를 확인한 금융 지식 문서를 `ai-pjt/rag/data/financial-knowledge/`에 분리했다.
- `ai-pjt/rag/data/catalog.json`에 collection, 문서 원본, 기준일, 버전, 적용 범위, 갱신·폐기 조건과 내용 hash를 기록했다.

## P09에 남기는 작업

1. catalog만 적재 입력으로 사용하고 디렉터리 전체를 암묵적으로 적재하지 않는다.
2. `interface-guides`와 `financial-knowledge`를 서로 다른 collection 또는 동등한 강제 필터 경계로 분리한다.
3. catalog 경로와 내용 hash가 다른 문서는 적재를 거부한다.
4. 검색 결과에 `source_id`, 문서 버전, 기준일, 근거 구간과 점수를 연결한다.
5. 교체·폐기 문서를 활성 인덱스에서 제외하고 재색인 결과와 활성 인덱스 버전을 남긴다.
6. synthetic corpus로 적재·평가 명령을 먼저 검증한 후 이 corpus를 운영 후보 인덱스에 적재한다.

`docs/ai/retrieval.md`의 추적 여부와 runtime 코드 변경은 이 커밋의 범위가 아니다.
