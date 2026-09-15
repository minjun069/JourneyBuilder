# ADR-0004: Frontend 대화형 AI 응답 채널을 v2 SSE로 통일

- 상태: Accepted
- 결정일: 2026-09-13
- 관련 작업: F07-T03 / W2-L07
- 보완 결정: ADR-0002, ADR-0003

## 배경

ADR-0002는 일반 상담에 v2 SSE를 도입하면서 초기 장애 전환과 비교 검증을 위해
callback + polling을 남겼고, ADR-0003은 Elicit 전체 대화를 SSE로 옮겼다. 이후
Frontend에서 polling을 실제로 호출하는 흐름은 저장·명시 요청의 Error Review만
남았다.

Error Review는 형식상 유효한 draft에 대한 비차단 자문이다. 결과가 없거나 timeout·
오류가 발생해도 저장을 계속하며, 브라우저 연결과 독립적으로 결과를 반드시 완료·복구해야
한다는 제품 요구사항은 없다. 반면 기존 SSE endpoint는 `request_mode=error_review`,
`review_trigger`, terminal 검증·저장과 멱등성 계약을 이미 지원한다.

## 결정

1. 일반 상담, Elicit `start`·`resume`·continuation, Error Review의 `save`·
   `user_request`는 모두
   `POST /api/consulting/v2/consultations/{session_id}/messages/stream/`을 사용한다.
2. Error Review는 `start`·`delta`를 사용자 결과로 반영하지 않고 검증된
   `done.message`의 구조화 review만 화면에 적용한다. `error`나 terminal event 없는
   EOF는 기존 비차단 저장 정책에 따라 검토 실패로 처리한다.
3. Frontend의 callback 비동기 접수, 결과 GET, 3초 polling helper와 자동 polling
   fallback을 제거한다. SSE 실패 재시도는 새 `Idempotency-Key`의 새 요청이다.
4. Django·AI의 callback + polling endpoint와 terminal 상태 저장은 기존 호환·운영
   회귀를 위해 당장 제거하지 않지만, 현재 Frontend 제품 흐름은 이를 호출하지 않는다.
5. 브라우저 연결과 독립적인 완료·재시도·DLQ가 필요한 장기 작업은 대화형 fallback이
   아니라 RabbitMQ 영속 작업 계약으로 분리한다.

## 이유

- 같은 화면의 대화형 요청이 하나의 취소·오류·멱등성 상태 머신을 사용한다.
- Error Review의 비차단 성격에는 연결 독립적 polling 완료 보장이 필요하지 않다.
- 사용되지 않는 Frontend polling 분기와 주기적 GET을 제거해 이중 수명주기를 없앤다.
- Error Review의 action 분리, stale current value 확인과 기존 저장 검증은 transport와
  무관하게 유지된다.

## 결과와 제약

- Error Review의 모델 출력은 현재 graph 종료 뒤 하나의 `delta`와 `done`으로 전달될
  수 있다. 점진적 token 표시가 목적이 아니라 채널 통일이 목적이다.
- 화면 이탈로 SSE가 취소되면 진행 중 검토도 실패로 종결한다. 저장 trigger는 검토 없이
  기존 저장을 계속하고 명시 요청은 사용자가 다시 요청할 수 있다.
- 60초 AI 실행 deadline보다 긴 120초 stream read idle 경계를 회귀 검사한다. 장기
  실행을 위해 timeout을 계속 늘리지 않는다.
- callback + polling API 제거 여부는 외부 소비자와 운영 rollback 의존성을 확인하는
  별도 호환성 변경으로 다룬다.

## 완료 기준

- Error Review 두 trigger가 SSE fixture와 동일한 endpoint를 사용한다.
- Frontend 제품 코드에 callback polling helper와 결과 GET 호출이 없다.
- `done`, `error`, terminal 없는 EOF에서 기존 Error Review 표시·비차단 저장 동작이
  유지된다.
- Frontend 계약 검사와 production build가 통과한다.
