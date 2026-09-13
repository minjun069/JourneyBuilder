# ADR-0003: Elicit 전체 대화의 응답 채널을 v2 SSE로 통일

- 상태: Accepted
- 결정일: 2026-09-12
- 관련 작업: F08-T02 / W2-L09
- 선행 결정: ADR-0002

## 배경

ADR-0002는 일반 AI 상담의 기본 채널을 v2 SSE로 정하고 callback + polling을 초기
장애 전환과 비교 검증을 위해 유지했다. 이후 추가된 Elicit 기능은 활성 질문의 일반
답변에는 SSE를 사용하지만 UI 동의 `start`와 QA 뒤 `resume` control만 공통
callback + polling helper를 호출했다.

이 분기는 Elicit 상태나 보안·승인 계약에 필요한 구분이 아니다. 같은
`financial-mindset` 대화 안에서 control 종류에 따라 응답 지연·취소·오류 표시가
달라지고, polling fallback과 일반 대화 채널의 책임도 흐려진다.

## 결정

1. Elicit의 UI 동의 `start`, QA 뒤 `resume`, 활성 질문의 continuation은 모두
   `POST /api/consulting/v2/consultations/{session_id}/messages/stream/`을 사용한다.
2. `elicit_action=start|resume`, 현재 form snapshot, 사용자 JWT, 세션 소유권,
   `contract_version`, `Idempotency-Key`와 Elicit checkpoint 의미는 유지한다.
3. Frontend callback + polling helper는 `elicit_action`을 생산하지 못하게 한다.
4. Elicit 응답은 일반 상담과 동일한 `start -> delta* -> done | error` 상태 머신,
   저장·취소·멱등성 규칙을 따른다.
5. callback + polling 경로는 Error Review와 명시적 장애 전환 범위에 남긴다.

## 이유

- 한 Elicit 대화의 모든 사용자 turn이 같은 transport와 화면 상태를 사용한다.
- 질문 생성 결과를 첫 응답 조각부터 표시하고 화면 이탈 취소를 upstream까지
  전파할 수 있다.
- 정성정보 수집 상태와 transport 상태를 분리해 polling이 Elicit 진행 조건으로
  오해되는 것을 막는다.
- 기존 SSE endpoint가 `elicit_action`과 terminal FormChangeAction을 이미 처리하므로
  Backend·AI 계약을 추가하지 않고 Frontend 분기만 제거할 수 있다.

## 결과와 제약

- Elicit 시작·재개 중에는 `processing` polling 표시 대신 SSE streaming 상태를
  표시한다.
- 검증된 `done`에서만 Assistant 메시지와 선택 FormChangeAction을 화면에 반영한다.
- SSE 실패 후 callback + polling으로 자동 전환하지 않는다. 재시도는 새
  `Idempotency-Key`를 사용하는 새 SSE 요청이다.
- Error Review와 운영 fallback의 callback + polling 계약은 이 결정으로 제거하지
  않는다.

## 완료 기준

- `runElicitAction(start|resume)`이 일반 메시지와 같은 SSE consumer를 사용한다.
- polling helper가 `elicit_action`을 만들 수 없다.
- 공용 SSE fixture가 Elicit 세 동작의 채널을 `sse`로 고정한다.
- Frontend SSE consumer test와 production build가 통과한다.
