# AI 평가와 운영 관측

상태: **P06 라우팅·P11 관측·P14 보안 foundation 적용 — production 평가와 실제 baseline 대기**

이 문서는 평가셋, 품질·운영 지표, 버전 비교와 rollback 방식의 단일 원천이다. 수치형 인수 기준은 [AI Agent 요구사항](../requirements/ai-agent.md) §5, 현재 실행 가능한 제품 검사는 [프로젝트 실행 안내](../../README.md)를 따른다.

## 평가셋

평가 항목은 입력, 기대 결과, 허용 변형, 검토자, 데이터·프롬프트·모델·인덱스·도구 버전을 함께 기록한다.

- 6개 Agent의 정상·모호·오류 라우팅
- 3~5턴 후속 질문, 정정, 생략 표현, 세션 격리
- 단일 필드·정성 텍스트·반복 목록·중첩 목록 입력
- 단순 검색, 개인화, 복합 질문, 근거 없음, 오래되거나 상충하는 문서
- 도구 정상·timeout·잘못된 인자·권한 거부
- 프롬프트 인젝션, 간접 인젝션, 데이터 유출, 미승인 변경

`contract-fixtures/ai-lifecycle/v2/form-actions.json`은 입력 보조 평가셋의 첫 공용 계약 fixture다. Frontend·Backend·AI targeted test가 같은 case를 읽어 AI 출력에는 승인 상태와 서버 소유 ID가 없고, Django 정규화 이후 모든 change가 `proposal/pending`에서 시작하는지 확인한다. 이 검사는 승인·단위 경계의 정적 계약 증거이며 실제 callback, 화면 상호작용, DB 저장 E2E나 품질 baseline을 대신하지 않는다.

현재 실행 가능한 foundation은 P06의 60개 라우팅 평가 입력, P11의 typed 합성 event·AX
산식, P14의 25개 mock-only 공격 case다. P06은 production pure router가 아직 없어
기대 라벨과 allowlist의 자체 일관성만 검사하고, P11은 실제 producer·exporter를 내보내지
않으며, P14는 실제 guardrail이 아니라 mock oracle을 검사한다. 각각 W1-L02, W1-L11,
W1-L14에서 production 경계에 연결한다.

## 지표

| 영역 | 지표 |
|---|---|
| 라우팅 | Agent 정확도·macro F1·fallback 적정률 |
| 문맥 | 멀티턴 과업 성공률·정정 반영률·세션 유출 건수 |
| 입력 보조 | 필드·값 exact match, 단위·자료형 오류율, 승인·수정·거절률 |
| 검색 | Recall@k, MRR@k, context precision |
| 답변 | 근거 충실도, 관련성, 인용 정확도·포함률, unsupported claim 비율 |
| 운영 | 성공·실패율, p50/p95 지연, 호출·토큰·비용, 재시도·callback 완료율 |
| AX | 폼 완료 시간, 완료 턴 수, 수동 수정률, 사용자 과업 완료율 |
| 보안 | 공격 차단률, 치명적 우회, PII 노출, 권한 밖 도구 호출 |

## 관측과 재현

- request_id와 가명화된 thread_id로 Django·AI·MCP trace를 연결한다.
- Agent route·노드, 검색 문서 ID·점수, 도구명·상태, 모델·프롬프트·인덱스 버전, 토큰·비용, callback 상태를 기록한다.
- 원본 사용자 메시지와 금융정보는 기본 trace에서 제외하고 보안 정책에 따라 마스킹한다.
- 변경 전후를 같은 평가셋으로 비교하고 기준 미달이면 배포하지 않는다.
- 모델·프롬프트·인덱스·도구 버전을 독립적으로 rollback할 수 있어야 한다.

## 기준 변경

초기 인수 기준은 요구사항의 수치를 사용한다. 1차 baseline 뒤 작업자가 기준을 바꿀 때는 baseline, 변경 이유와 영향을 남긴다. 같은 수치를 이 문서나 검증 문서에 복제하지 않는다.

## 열린 결정

- 비용·지연·품질 baseline과 운영 SLO
- trace 저장소·보존기간·대시보드
