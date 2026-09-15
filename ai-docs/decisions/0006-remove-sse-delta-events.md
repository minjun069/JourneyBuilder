# ADR-0006: SSE 부분 응답 이벤트 제거

- 상태: Accepted
- 결정일: 2026-09-15
- 관련 작업: F04-T04 / W2-L03
- 보완 대상: ADR-0002~ADR-0005의 `delta` 이벤트 계약

## 배경

현재 AI 서비스는 graph와 post-output 검사를 모두 마친 최종 `response`를 하나의
`delta`로 보낸 직후 같은 내용을 `done.output.response`로 다시 보낸다. 실제 token
streaming이 아니므로 사용자는 검증되지 않은 중간 결과를 더 빨리 받지 못하고,
FastAPI·Django·Frontend는 중복 payload와 부분 응답 누적 분기만 유지한다.

## 결정

1. AI→Django와 Django→Browser의 정상 상태 머신을 `start -> done`으로 고정한다.
2. 실패 상태 머신은 `start -> error`로 유지한다.
3. `delta`를 허용 이벤트 목록, provider, proxy와 Frontend consumer에서 제거한다.
   수신한 `delta`는 protocol 위반으로 실패 처리한다.
4. AI는 graph 실행, 출력 검증과 terminal masking을 모두 마친 뒤 답변 전체를
   `done.output.response`로 한 번만 보낸다.
5. Django는 부분 문자열을 누적하거나 누락된 `done.output.response`를 복구하지 않는다.
   검증된 `done`을 transaction으로 저장한 뒤 Browser에 직렬화된 `done.message`만 보낸다.
6. Frontend는 `start` 이후 처리 중 표시만 유지하고, 검증된 `done.message`를 받은
   시점에 답변 버블과 action을 한 번에 표시한다.

## 호환성과 배포

기존 v2의 `delta*`는 0개일 수 있어 `start -> done`이 이미 유효했다. endpoint,
payload 필드와 `contract_version: "2.0"`은 바꾸지 않는다. 새 consumer는 `delta`를
거부하므로 AI provider를 먼저 배포하고 Django, Frontend 순으로 전환한다. rollback은
세 서비스를 이전 release로 함께 되돌린다.

## 결과

- 사용자는 AI 절차가 완료되고 저장 가능한 terminal이 확정된 답변만 받는다.
- 답변 본문의 wire 전송과 Frontend 상태 분기가 한 번으로 줄어든다.
- SSE는 인증, 취소 전파, 멱등 terminal receipt와 `done | error` 구분을 위해 유지한다.
- terminal 없는 EOF, 빈 `done.output.response`와 `delta` 수신은 성공으로 복구하지 않는다.

## 완료 기준

- 공용 fixture의 허용 이벤트가 `start`, `done`, `error`뿐이다.
- AI와 Django 정상 응답이 `start -> done`이고 `delta`를 생성하지 않는다.
- Django와 Frontend가 `delta`를 protocol 위반으로 거부한다.
- Frontend는 `done.message` 전에는 AI 답변 버블을 표시하지 않는다.
- provider·consumer 계약 검사와 세 서비스의 관련 회귀가 통과한다.
