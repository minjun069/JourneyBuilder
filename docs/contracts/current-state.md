# 현재 AI 상담 계약 불일치 조사

상태: **D1 조사 완료 — 계약 동결 전**

이 문서는 현재 저장소 코드에서 관찰한 Frontend·Django·AI 경계의 불일치를 기록한다. 목표 형식은 [Django HTTP API](http-api.md), [AI 서비스 API](ai-api.md), [데이터 스키마](../data/schema.md)가 소유한다. 이 문서의 Before는 채택 계약이 아니며, runtime E2E를 실행하기 전까지는 코드 정적 조사 결과다.

## 사용 방법

- 계약·구현 작업 전에는 `NEED='현재 계약 상태'`로 이 문서와 경로 계약 문서를 함께 읽는다.
- 동결 전에는 이 표의 After를 구현 확정안으로 취급하지 않는다. 열린 선택지는 해당 목표 계약 문서에 담당자·기한과 함께 남긴다.
- 실제 E2E로 확인하고 계약을 동결하면 행을 제거하거나 현재 관찰로 갱신한다. 변경 이력은 Git이 관리한다.

## 코드 기준 Before → After

| 경계 | Before: 현재 코드 관찰 | After: 목표 계약 | 구현 영향 |
|---|---|---|---|
| Browser 메시지 | [ai.js](../../front-pjt/src/stores/ai.js)가 `form`을 전송하지만 Django는 `form_data`만 읽어 현재 폼이 비어 있다. | `form_data`와 단계별 canonical adapter로 고정한다. | Frontend adapter·serializer |
| 접수 응답 | Django 메시지 POST는 `request_id`만 반환하고 `processing` 상태를 반환하지 않는다. | 202 응답에 안정적인 작업 상태를 포함한다. | view·Frontend 상태 |
| Django → AI | [ai_services.py](../../backend-pjt/consulting/ai_services.py)는 session ID, callback URL, chart context 없이 5개 필드만 보낸다. | request ID·thread·session·callback URL·chart context를 검증한다. | request schema·설정 |
| AI callback | [main.py](../../ai-pjt/main.py)는 고정 주소에 status·session ID·인증 헤더 없이 callback한다. Django는 status 누락 시 completed로 기본 처리한다. | 요청의 허용 callback URL, 공통 envelope, 인증·재시도 규칙을 적용한다. | AI callback·Django 검증 |
| polling·오류 | Django polling은 상태·오류를 반환하지만 Frontend는 message와 pending changes만 소비한다. | processing·completed·failed·expired·unauthorized를 화면 상태로 표시한다. | polling adapter·화면 |
| 제안·승인 | AI는 `update: {field: value}`를 만들고 Django는 1:1 section의 legacy field만 승인한다. Frontend 승인·거절 호출은 없다. | `FormUpdateProposal`과 set/add/update/remove, 반복·중첩 재검증, 승인 후 저장을 적용한다. | AI output·Django transaction·UI |
| 거절 멱등성 | 승인 재요청은 409이나 이미 거절된 제안은 다시 거절할 수 있다. | 승인·거절 모두 한 번만 처리하고 이후 요청은 409으로 통일한다. | action view·E2E fixture |

## 다음 계약 동결 순서

1. 실제 step ID registry와 canonical field registry를 확정한다.
2. 세 대표 단계의 정상·오류·경계 fixture를 만든다.
3. callback·상태·승인·거절을 같은 fixture로 3자 검토한다.
4. 목표 계약 문서와 provider·consumer schema를 같은 변경에서 갱신한다.
