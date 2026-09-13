# ADR-0002: 일반 AI 상담의 기본 응답 채널을 v2 SSE로 전환

- 상태: Accepted
- 결정일: 2026-09-08
- 관련 작업: S15P21B109-106

## 배경

현재 저장소에는 두 상담 경로가 공존한다.

- `/api/v2`의 callback + polling 경로는 사용자 요청 멱등성, 공통 오류 envelope,
  서비스 bearer 인증, 처리 deadline, action/change별 승인 계약을 제공한다.
- `/api/v1/.../messages/stream/` 경로는 AI 답변을 `start`, `delta`, `done`,
  `error` SSE 이벤트로 전달하지만 v2 수명주기 계약을 사용하지 않는다.

현재 Frontend는 v2 polling을 기본으로 호출하므로 SSE 구현이 사용자 화면에서
사용되지 않는다. 대화형 AI 응답은 첫 응답 조각을 즉시 표시하는 것이 중요하며,
수 분이 걸리는 진단·시뮬레이션·보고서 작업과 실행 특성이 다르다.

## 결정

1. 일반 AI 상담의 기본 응답 채널은 v2 SSE로 한다.
2. v2의 사용자 JWT, 세션 소유권, `contract_version`, `Idempotency-Key`, 서비스
   bearer 인증, 오류 envelope, action/change별 승인 의미를 유지한다.
3. 외부 endpoint는 `POST /api/consulting/v2/consultations/{session_id}/messages/stream/`, 내부
   AI endpoint는 `POST /api/ai/v2/chat/stream`으로 분리한다.
4. SSE 이벤트 상태 머신은 `start -> delta* -> done | error`로 고정한다.
5. 사용자 메시지는 스트림 시작 전에 저장하고 Assistant 메시지는 검증된 `done`
   처리 시 한 번만 저장한다.
6. 기존 v2 callback + polling은 초기 장애 전환과 비교 검증을 위해 유지한다.
7. 금융건강진단, 은퇴자금 시뮬레이션, 재무계획, PDF 생성은 SSE 연결 수명에
   종속시키지 않고 RabbitMQ 기반 영속 작업으로 구현한다.

## 이유

- 대화형 답변은 서버에서 브라우저로 연속 전달되는 단방향 흐름이므로 SSE가
  WebSocket보다 단순하다.
- 기존 v2 기능을 버리지 않고 transport만 스트리밍 경로로 확장하면 인증·멱등성·
  승인 계약을 한 번 더 만들 필요가 없다.
- polling fallback을 유지하면 SSE 배포 초기 장애 시 전체 release rollback 없이
  제한적으로 전환할 수 있다.
- 장기 작업은 ACK, 재시도, DLQ와 브라우저 연결과 독립된 수명주기가 필요하므로
  SSE가 아니라 RabbitMQ가 담당해야 한다.

## 결과와 제약

- Frontend, Django, FastAPI가 함께 소비하는 v2 SSE 계약과 fixture가 필요하다.
- 동일 idempotency key의 재요청은 사용자 메시지와 AI 실행을 중복 생성하면 안 된다.
- SSE가 시작된 뒤 발생한 오류는 HTTP status가 아니라 `error` terminal event로
  전달한다.
- Nginx buffering/cache를 끄고 ASGI async iterator를 유지해야 한다.
- resume/replay는 이번 범위에 포함하지 않으며 자동 재연결도 하지 않는다.

## 완료 기준

- 실제 채팅 화면이 v2 SSE를 기본으로 호출한다.
- 첫 `delta`가 `done` 전에 표시된다.
- v2 action/change별 승인 UI가 유지된다.
- polling fallback과 장기 작업 경로의 책임이 문서와 코드에서 구분된다.
