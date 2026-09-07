# AI 도구와 MCP

상태: **P10 provider-neutral 도구·출력검증 foundation 확정 — F09 production graph binding·은퇴차액 계산 대기**

이 문서는 MCP·Function Calling 도구의 책임과 실행 정책의 단일 원천이다. 서비스 간 HTTP payload는 [AI 서비스 API 계약](../contracts/ai-api.md), 권한과 감사 통제는 [AI 보안 정책](../security/ai-security-policy.md)을 따른다.

## P10에서 준비된 도구 계약

| 도구 | 책임 | 부작용 |
|---|---|---|
| search_financial_knowledge | 승인된 금융 문서 검색 | 없음 |
| get_form_field_spec | 현재 단계에서 쓰기 가능한 canonical 필드·자료형·단위·제약 조회 | 없음 |
| get_dashboard_metric | 검증 완료된 현재 세션 보고서 지표 조회 | 없음 |
| get_retirement_scenario | 검증 완료된 현재 세션 은퇴 시나리오 조회 | 없음 |
| list_available_reports | 현재 세션 보고서의 버전·상태 목록 조회 | 없음 |
| get_step_completion_status | 금융 원문 없이 섹션 완료 상태와 보고서 준비 여부 조회 | 없음 |

기계 계약은 `ai-pjt/tools/catalog.py`의 여섯 `FunctionToolDefinition`과
`contracts.py`·`registry.py`가 소유한다. 현재 handler는 runtime이 주입한 합성·snapshot
adapter만 소비하며 Agent graph에서는 아직 호출하지 않는다. Agent 이름 allowlist는
`contract-fixtures/ai-lifecycle/v2/step-registry.json`의 등록 Agent와 자동 대조한다.

`validate_form_update`는 Function tool이 아니라 모든 Agent 뒤에 놓이는 결정론적 출력
검증 node다. `ai-pjt/tools/output_validation.py`가 D1 form action의 단일·반복·중첩 값,
canonical field, 단위와 operation을 검사한다. `calculate_retirement_gap`은 F09-T04에서
Backend 공식·가정의 원천을 연결한 뒤 추가하며 현재 registry에 존재한다고 주장하지 않는다.

## 공통 계약

각 도구는 다음을 명시한다.

- 버전이 있는 입력·출력 스키마와 오류 코드
- 호출 가능한 Agent와 권한 scope
- timeout·재시도·idempotency 정책
- 가명화된 요청자, 도구·버전, 인자 데이터 등급, 결과 상태, 승인 여부의 감사 필드

LLM이 만든 도구명·인자·설명을 신뢰하지 않고 allowlist와 코드 검증을 적용한다. timeout·부분 실패·권한 거부는 모델과 사용자 응답에 명시적으로 전달한다.

P10 foundation은 도구별 schema, Agent allowlist, timeout, 공통 오류 envelope와 raw 인자·결과를
제외한 audit event를 구현했다. 실제 request·step·deadline·검색기·보고서 snapshot 주입과
Agent graph 연결은 F09-T01~T05의 production 범위다.

## 부작용 경계

현재 여섯 도구는 조회·검증만 수행한다. 화면 변경 기능을 추가하더라도 저장하지 않고
제안만 생성한다. 쓰기·외부 전송·금융 데이터 변경 도구는 별도의 요구사항, 사용자 승인,
최소 권한, 감사, rollback 계약 없이는 추가하지 않는다.

## 남은 구현

- MCP 서버 배포 경계와 인증 방식
- `search_financial_knowledge`의 승인 index adapter
- form field·완료 상태·보고서 조회 adapter와 Agent graph binding
- `calculate_retirement_gap`의 Backend 공식·가정 단일 원천과 결정론적 구현
