# RAG pipeline

`rag.pipeline`은 승인된 운영 후보 문서와 분리된 synthetic corpus로 수집, 청킹,
embedding, upsert, 버전형 재색인, 검색 평가를 재현한다.

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

F06-T03 adapter는 catalog의 승인·출처·버전·적용 범위·라이선스·민감도와 파일
SHA-256을 pipeline의 `DocumentMetadata`로 변환해야 한다. 이 변환과 운영 store가
구현되기 전에는 catalog 등재를 실제 운영 인덱싱 완료로 보지 않는다.

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

`IndexedRetriever.retrieve()`는 활성 logical collection을 해석하고 검색 결과마다
`source_id`, 문서 버전, 근거 구간 offset, 점수와 chunk ID를 반환한다. F06 runtime
adapter와 F07 QA는 이 결과를 검색·인용 경계로 사용한다. 운영 vector database 연결은
`IndexStore` protocol 구현으로 교체한다. 현재 catalog는 F06-T01·T02 감사와 도메인
승인을 통과했지만, F06-T03 adapter와 F06-T04 인덱싱·연결 및 F06-T05 검색 평가를
통과하기 전에는 운영 적재 완료로 판정하지 않는다.

## 테스트

`ai-pjt`에서 실행한다.

```bash
python3 -m unittest discover -s rag/tests -v
```
