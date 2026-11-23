# AI 평가와 운영 관측

상태: **현재 과업별 모델 계약에 대한 mock·경계 회귀 적용 — 실제 provider 품질은 별도 평가**

이 문서는 평가셋, 품질·운영 지표, 버전 비교와 rollback 방식의 단일 원천이다. 수치형 인수 기준은 [AI Agent 요구사항](../requirements/ai-agent.md) §5, 현재 실행 가능한 제품 검사는 [프로젝트 실행 안내](../../README.md)를 따른다.

## 평가셋

평가 항목은 입력, 기대 결과, 허용 변형, 검토자, 데이터·프롬프트·모델·인덱스·도구 버전을 함께 기록한다.

- 다섯 Agent의 정상 라우팅과 모호·미지원 요청의 실패 안내
- 3~5턴 후속 질문, 정정, 생략 표현, 세션 격리
- 단일 필드·정성 텍스트·반복 목록·중첩 목록 입력
- 단순 검색, 개인화, 복합 질문, 근거 없음, 오래되거나 상충하는 문서
- 도구 정상·timeout·잘못된 인자·권한 거부
- 프롬프트 인젝션, 간접 인젝션, 데이터 유출, 미승인 변경

`contract-fixtures/ai-lifecycle/v2/form-actions.json`은 입력 보조 평가셋의 첫 공용 계약 fixture다. Frontend·Backend·AI targeted test가 같은 case를 읽어 AI 출력에는 승인 상태와 서버 소유 ID가 없고, Django 정규화 이후 모든 change가 `proposal/pending`에서 시작하는지 확인한다. 이 검사는 승인·단위 경계의 정적 계약 증거이며 실제 SSE, 화면 상호작용, DB 저장 E2E나 품질 baseline을 대신하지 않는다.

현재 mock 회귀는 실제 graph·실행 계획 compiler·입출력 경계를 통과시켜 계약을 검사한다.
Planner의 요청 설명은 조건·값·단위·마스킹 토큰을 보존하고, 코드는 task ID·의존성·binding을
구성해야 한다. QA는 계획→전체 검색→답변→동일 근거 평가의 기본 모델 3회와 제한 재시도를,
Input Assist는 추출→코드 검증→필요시 의미 검토→안내의 2~3회를 확인한다.

Error Review는 모델에 전달한 populated field instance를 기준으로 탐지·오탐을 집계한다.
실패한 검토·미전달 container를 분모에서 빼고 동일 대상의 중복 경고는 한 번 집계한다.
confidence 구간이나 표시용 threshold는 사용하지 않는다. Chart는 모델에 전달한 표시값·단위와
출력 검증값의 일치를, Elicit은 현재 대화에 대한 자연스러운 응답을 검사한다.
대화 기억은 사용자·AI·이전 요약과 금융값·숫자·토큰을 포함하되 같은 scope와 근거를 검증한다.
Aggregator는 단일 성공 task도 다듬고 수치·조건·승인·저장 안내를 보존해야 한다.

`ai-pjt`에서 다음 명령으로 현재 모델 계약·호출 예산·평가 산식을 검사한다.

```bash
python -m unittest tests.test_qa_refactor tests.test_input_assist_refactor tests.test_explanation_memory_refactor -v
python -m unittest discover -s evals/tests -v
```

mock 통과는 실제 provider의 응답 품질을
입증하지 않는다. 실제 모델 평가는 별도 승인과 고정 버전으로 실행하고 산출 경로를 지정한다.

## 지표

| 영역 | 지표 |
|---|---|
| 계획·라우팅 | 의미 중심 요청의 종류·조건 보존, 코드 실행 계획·Agent 매핑 정확도, 모호·미지원 판정 |
| 문맥 | 멀티턴 과업 성공률·정정 반영률·세션 유출 건수 |
| 입력 보조 | 필드·값 exact match, 단위·자료형 오류율, 승인·수정·거절률 |
| 에러 검토 | field instance별 탐지·오탐 건수와 비율, ref·현재값·행 ID 복원, 수정 후보 유효성, 실패 뒤 저장 지속 |
| 검색 | Recall@k, MRR@k, context precision |
| 답변 | 근거 충실도, 관련성, 내부 검색 metadata 비노출, unsupported claim 비율, 최종 다듬기의 조건·수치 보존 |
| 운영 | 성공·실패율, p50/p95 지연, 호출·토큰·비용, SSE terminal 완료율 |
| AX | 폼 완료 시간, 완료 턴 수, 수동 수정률, 사용자 과업 완료율 |
| 보안 | 공격 차단률, 치명적 우회, PII 노출, 권한 밖 도구 호출 |

## 관측과 재현

- request_id와 가명화된 thread_id로 Django·AI·MCP trace를 연결한다.
- Agent route·노드, 검색 문서 ID·점수, 도구명·상태, 모델·프롬프트·인덱스 버전, 토큰·비용, SSE terminal 상태를 기록한다.
- 원본 사용자 메시지와 금융정보는 기본 trace에서 제외하고 보안 정책에 따라 마스킹한다.
- 변경 전후를 같은 평가셋으로 비교하고 기준 미달이면 배포하지 않는다.
- 모델·프롬프트·인덱스·도구 버전을 독립적으로 rollback할 수 있어야 한다.

## 기준 변경

초기 인수 기준은 요구사항의 수치를 사용한다. 1차 baseline 뒤 작업자가 기준을 바꿀 때는 baseline, 변경 이유와 영향을 남긴다. 같은 수치를 이 문서나 검증 문서에 복제하지 않는다.

## 열린 결정

- 비용·지연·품질 baseline과 운영 SLO
- trace 저장소·보존기간·대시보드
