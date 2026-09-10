# P13 데이터 분류·마스킹 테스트 준비

- 작업 레인: P13 — D11 데이터 분류·마스킹 준비
- 목표와 지금 필요한 이유: production 경계가 공개되기 전에 합성 데이터로 마스킹 누락을 판정할 공통 계약을 준비한다.
- 기준 commit SHA: `f0fa13d35788515cd1a8e5b079545c9c32f8d3c0`
- 입력 계약·fixture: 외부 계약 비의존 합성 sentinel. D1 공용 fixture는 사용하지 않는다.
- 쓰기 파일: `ai-pjt/tests/security/**`, 테스트 패키지 표시용 `ai-pjt/tests/__init__.py`
- 수정 금지 파일: AI production 코드와 기존 보안 계약 구현
- mock 경계: 모델 입력·출력, 애플리케이션 로그, trace exporter에 전달되기 직전의 직렬화 문자열
- 산출물: `masking_contract.py`의 분류·sentinel·누락 판정 함수와 그 self-test
- 후속 활성화: W1-L12의 F11-T01에서 production masker core에 연결하고 W3-L05의 masking-safe 관측 통합에 사용한다.

## 실행

저장소 루트에서 다음을 실행한다.

```bash
cd ai-pjt
python3 -m unittest tests.security.test_masking_contract -v
```

테스트는 실제 개인정보를 포함하지 않는다. `.invalid` 예약 도메인, 명백한 P13 표식, 무효 전화번호처럼 합성임을 식별할 수 있는 값만 사용한다.

## 현재 판정 범위

현재 계약은 다음 원문 노출을 탐지한다.

| 데이터 분류 | 모델 입력·출력 | 애플리케이션 로그·trace |
|---|---|---|
| 직접 식별정보 | 원문 금지 | 원문 금지 |
| 준식별정보 | 과업별 allowlist 결합 전에는 판정하지 않음 | 원문 금지 |
| 프로젝트 고위험 금융정보 | 과업별 allowlist 결합 전에는 판정하지 않음 | 원문 금지 |
| 운영정보 | 원문 식별자 금지 | 가명 식별자로 대체하기 전 원문 금지 |

`프로젝트 고위험 금융정보`는 내부 보호 등급이며 개인정보보호법 제23조의 법정 `민감정보`와 같은 뜻이 아니다.

## F11에서 연결할 항목

1. 실제 모델 request·response, callback, 일반 로그, trace export 직전 값을 문자열로 직렬화해 `assert_no_raw_sentinels`에 전달한다.
2. Agent별 필드 allowlist가 없는 준식별·금융정보가 모델 입력으로 전달되지 않는지 별도 테스트한다.
3. 마스킹 실패나 exporter 오류를 성공으로 우회하지 않고 외부 전송을 닫는지 검증한다.
4. 부분 노출, 구분자 변형, 인코딩처럼 단순 literal 탐지를 벗어나는 회귀는 P14에서 준비한 F13 공격 회귀셋과 결합한다.

이 준비 테스트의 통과는 production 마스킹 완료를 뜻하지 않는다. 현재 증명하는 것은 합성 사례의 분류가 일관되고, passthrough 누락이 각 sink에서 실패한다는 사실까지다.

## F12-T01 서비스 인증·도구 보안 공격 계약

- 기능·세부 작업: `F12-T01`
- 실행 레인·시작 조건: `W1-L13`, 통과한 `G1` 기준선
- 목표: bearer, callback, 재전송, 소유권과 task·dependency·binding·artifact·도구 권한 위조를 후속 구현이 소비할 하나의 합성 계약으로 고정한다.
- 입력: `contract-fixtures/ai-lifecycle/v2/lifecycle.json`의 기존 HTTP·callback 사례와 F12 전용 합성 graph/tool 사례
- 쓰기 영역: `ai-pjt/tests/security/service-tool-security-cases.json`, `service_tool_security_*.py`, 이 README
- 수정하지 않는 영역: AI·Backend production transport, graph, tool runtime과 P14 공격 corpus
- 직접 후속: `M2-L17`에서 F12-T02/T03 production adapter가 service auth·callback·graph replay 사례를 소비하고, `M4-L02` 이후 F12-T04가 task·binding·artifact·tool 권한 사례를 소비한다.

전용 파일의 책임은 다음과 같다.

- `service-tool-security-cases.json`: 공격 입력, 허용·차단 결과, HTTP status·reason code, 반드시 발생하거나 발생하지 않아야 할 부작용, 후속 작업 소유자를 기록한다.
- `service_tool_security_contract.py`: suite 구조와 기존 lifecycle fixture 참조가 어긋나지 않는지 검증한다. production 모듈은 import하지 않는다.
- `test_service_tool_security_contract.py`: 필수 공격군, 정상 허용 대조군, callback graph 비재실행, 소유권 실패 폐쇄, 합성 credential 사용을 self-test한다.

저장소 루트에서 다음처럼 실행한다.

```bash
cd ai-pjt
python3 -m unittest tests.security.test_service_tool_security_contract -v
```

이 테스트의 통과는 F12 보안 통제 구현 완료를 뜻하지 않는다. T01에서 증명하는 것은 공격 계약이 완전하고 기존 lifecycle 계약과 정렬됐다는 사실까지다. 실제 HTTP·checkpoint·graph·tool handler 호출 결과는 각 `downstream_owner`가 같은 case를 production adapter에 연결해 검증한다.
