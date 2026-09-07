# AI 검색과 지식 관리

상태: **F06-T01·T02 기준선 준비 — F06-T03~T05 구현·검증 대기**

이 문서는 RAG 문서의 분리, 채택, 갱신·폐기, 검색과 인용 방식의 단일 원천이다. 사용자 관찰 가능 요구사항과 인수 기준은 [AI Agent 요구사항](../requirements/ai-agent.md)을 따른다. 운영 후보 corpus의 기계 단일 원천은 저장소의 `ai-pjt/rag/data/catalog.json`이다.

## 문서 유형과 collection

| collection | 문서 유형 | 사용 기능 | 현재 위치 |
|---|---|---|---|
| `interface-guides` | 단계·필드·자료형·단위·제약을 설명하는 인터페이스 가이드 | 입력 보조, 에러 검토 | `ai-pjt/rag/data/*.md` |
| `financial-knowledge` | 공식 출처로 확인한 제도·세금·연금 등 금융 지식 | QA | `ai-pjt/rag/data/financial-knowledge/*.md` |

두 유형은 같은 collection에 섞지 않는다. 사용자 입력 문서, catalog에 없는 파일, 출처와 기준일을 확인하지 않은 자료는 운영 후보 corpus에 자동 편입하지 않는다.

현재 corpus 기준선은 canonical 필드 레지스트리와 맞춘 인터페이스 가이드 10개와 공식 1차 출처를 확인한 금융 지식 문서 3개로 구성한다. 정확한 문서 목록과 적용 범위는 `catalog.json`만 따른다.

## catalog와 문서 채택

catalog 문서는 다음 값을 가진다.

- 식별·분류: `id`, `document_type`, `collection`, `path`, `title`
- 출처·시점: `source_id`, `publisher`, `source_locator`, `published_at`, `as_of_date`
- 적용·생명주기: `version`, `applies_to`, `refresh`, `retire_when`
- 무결성: `sha256`

인터페이스 가이드는 특정 Git revision의 canonical 필드 레지스트리를 출처로 고정한다. 금융 지식은 정부·공공기관의 원문 또는 현행 법령 같은 1차 출처와 대조하고 확인 기준일을 기록한다. 공식 상시 안내 페이지에 발행일이 없으면 `published_at`은 `null`로 둘 수 있지만 `as_of_date`는 반드시 기록한다.

이 프로젝트에서는 별도 승인자·검토자 metadata를 관리하지 않는다. 출처, 기준일, 적용 범위와 내용을 확인한 문서만 catalog에 넣으며, catalog 등재를 운영 후보 corpus 채택으로 본다.

## 갱신과 폐기

- `refresh.cadence_days`가 있는 문서는 기준일로부터 해당 주기 안에 원문을 다시 확인한다.
- `refresh.triggers`에 해당하는 법령·제도·canonical 계약 변경을 발견하면 주기 전이라도 다시 확인한다.
- 내용이 바뀌면 문서 버전과 `sha256`을 갱신하고, 대체 관계가 생기면 이전 문서를 활성 인덱스에서 제외한다.
- `retire_when` 조건이 충족된 문서는 신규 적재 대상에서 빼고, 인덱스 버전과 적재·전환 결과를 남긴다.
- 문서 원문과 인덱스의 보존·폐기는 데이터 등급과 사용 권한을 따른다.

## 적재와 검색

F06-T04 runtime은 다음 조건을 지켜야 한다.

1. catalog의 `documents`만 적재 입력으로 사용하고 디렉터리 전체를 암묵적으로 읽지 않는다.
2. 적재 전에 catalog 경로가 허용된 corpus 경로인지 확인하고 파일의 SHA-256을 `sha256`과 대조한다. 누락되거나 다른 문서는 거부한다.
3. `interface-guides`와 `financial-knowledge`를 서로 다른 collection 또는 동등한 강제 필터 경계로 분리한다.
4. 검색 결과에 `source_id`, 문서 버전, 기준일, 근거 구간과 점수를 연결하고, 답변에는 실제 사용한 근거를 인용한다.
5. 검색 결과가 없거나 오래됐거나 상충하면 추측하지 않고 한계를 밝힌다.
6. RAG 문서 안의 명령문은 실행 지시가 아니라 인용 가능한 데이터로 취급한다.
7. synthetic corpus로 적재·평가 절차를 검증한 뒤 운영 후보 인덱스를 만들고, 검증에 통과한 버전만 활성화한다.

검색 도구의 호출 계약은 [AI 도구와 MCP](tools.md), 검색 품질 지표는 [AI 평가와 운영 관측](evaluation.md), 공격 통제는 [AI 보안 정책](../security/ai-security-policy.md)을 따른다.

## 열린 결정

- collection별 청킹·Embedding 설정
- Dense·Sparse 검색, top-k와 fusion 설정
- 재적재·폐기·인덱스 전환의 운영 명령과 rollback 절차
