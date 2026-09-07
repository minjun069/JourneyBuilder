# 현재 AI 상담 계약 불일치 조사

상태: **D1 교차 영역 검토 반영, HTTP·AI·승인 생명주기와 chart 입력 v2 동결 — runtime 구현 전**

이 문서는 현재 저장소 코드와 동결된 v2 목표의 차이를 기록한다. 채택 계약은 [Django HTTP API](http-api.md), [AI 서비스 API](ai-api.md), [데이터 스키마](../data/schema.md)가 소유하며 이 문서의 현재 관찰은 runtime E2E 통과를 뜻하지 않는다.

## 이미 반영된 기반

- Frontend 메시지 payload는 `form_data`와 단계별 canonical adapter를 사용한다.
- AI 요청은 `request_id`, `thread_id`, `step_id`, customer snapshot, 현재 단계 field registry를 보낸다.
- 실제 23개 화면 step ID와 Agent allowlist, 화면 단계가 아닌 `general` transport fallback이 기계 계약에 반영돼 있다.
- canonical field registry와 `set/add/update/remove`, stable item ID, 모든 AI change의 별도 승인 원칙이 코드와 테스트에 있다.

## runtime Before → v2 목표

| 경계 | 현재 코드 관찰 | 동결된 v2 목표 | 구현 영향 |
|---|---|---|---|
| Browser 접수 | `/api/v1` POST가 ID는 반환하지만 status·deadline·contract version·client idempotency가 없다. | `/api/v2`, `Idempotency-Key`, processing deadline과 공통 envelope | Frontend API/store, Django serializer·view |
| Django → AI | session ID·deadline·version 없이 `/chat`을 호출하고 secret header는 선택적이다. | `/v2/chat`, v2 envelope, 5초 timeout·동일 request 1회 재시도 | Django payload·settings, AI schema |
| 서비스 인증 | Django는 secret을 보내지만 AI가 검증하지 않고 AI callback도 인증 header를 보내지 않는다. | 양방향 동일 `AI_SERVICE_BEARER_TOKEN`, 빈 값 금지, TLS, 동시 rotation | 두 서비스 config·middleware·secret 운영 |
| AI callback | 성공 callback만 있고 status·session ID·인증·실패 callback·retry가 없다. | 고정 v2 callback, completed/failed 공통 envelope, 5초 timeout·최대 4회 | AI background task·schema·callback client |
| callback 검증 | Django가 누락 status를 completed로 보완하고 thread fallback을 허용하며 terminal 상태가 뒤집힐 수 있다. | 모든 식별자 필수, terminal 불변, 동일 digest replay 200·충돌 409 | Django callback validator·상태 저장 |
| 작업 상태·TTL | Redis 600초 TTL에 processing/completed/failed만 있고 의미상 만료 상태가 없다. | 실행 60초, processing 90초, expired 전이, terminal·tombstone 24시간 | Redis schema·만료 작업·polling |
| Frontend polling | 3초 간격 60초이며 completed message만 소비하고 오류를 계속 polling한다. | 3초·90초, failed/expired/auth/state 오류별 종료와 안내 | store·AiChatView 상태 UI |
| action 결정 | draft proposal은 로컬 처리하고 Backend API는 message 단위 legacy 1:1 DB 변경이다. 중복 reject도 허용한다. | Django가 action/change ID 발급, change별 독립 승인·거절 감사, draft만 적용 | callback 정규화·action endpoint·Frontend resolver |
| chart 입력 | Backend 보고서와 Frontend 렌더링 데이터는 있지만 AI에는 별도 원본 payload가 없다. | `lifetime-cashflow`의 원본 series·단위·기간·가정·요약을 읽기 전용 `chart_payload`로 전달 | report adapter·AI request schema·chart Agent |
| 오류·버전 | error 문자열·객체가 혼재하고 안정 code와 schema version이 없다. | 공통 `{code,message,retryable}`, `contract_version: 2.0`, v1 병행 없는 조정 배포 | 전 adapter·schema·운영 배포 |
| 검증 | field registry·승인 필수 정책·Frontend KRW 경계 단위 테스트만 있고 lifecycle fixture consumer test·E2E가 없다. | 공용 v2 fixture를 세 consumer가 검증하고 D4·D5에서 lifecycle E2E 실행 | Frontend·Backend·AI test |

## 동결 결정

- 외부 작업 상태는 `processing → completed | failed | expired`다.
- AI의 `accepted`는 내부 접수 응답이며 callback terminal 상태는 `completed | failed`뿐이다.
- v2 AI form action은 `draft_form`만 대상으로 하며 최종 업무 DB 저장은 폼 저장 API가 담당한다.
- 사용자의 변경 요청과 AI confidence는 승인 증거가 아니다. AI가 만든 모든 change는 `proposal/pending`으로 시작하고 별도 사용자 승인 전에는 draft에도 적용하지 않는다.
- action은 여러 change를 묶을 수 있지만 승인·거절은 change별 독립 상태로 처리한다.
- 양방향 서비스 인증은 bearer token 하나를 공유한다.
- timeout, retry, idempotency, TTL, 오류 code는 두 목표 계약과 공용 fixture 값으로 고정한다.
- chart 입력 대표 단계는 `lifetime-cashflow`이며 렌더링 option이 아닌 읽기 전용 보고서 원본을 사용한다. 보고서가 준비되지 않으면 수치를 생성하지 않는다.
- v1 호환 기간 없이 Frontend·Django·AI를 v2로 함께 전환하고 rollback도 전체 release 단위로 수행한다.
- D1 교차 영역 검토는 Frontend·Backend·AI·도메인 경계를 한 작업 범위에서 검토한 결과이며 여러 역할의 합동 승인 절차를 뜻하지 않는다. G0 판정은 이 검토와 별도 단계이고, 구현 인수는 자동 검사와 E2E 증거로 판정한다.

## D1 교차 영역 검토 결과

| 경계 | 생산자 | 소비자 | 계약·상태 소유 원천 | 단위 | 승인 경계 |
|---|---|---|---|---|---|
| Browser → Django 메시지 | Frontend request adapter | Django serializer·view | 미저장 폼은 Frontend snapshot, 사용자·세션·request 상태는 Django, 외부 형태는 `http-api.md` | 화면 입력을 field registry의 canonical 자료형·단위로 변환 | 메시지 접수는 사용자 JWT·세션 소유권·idempotency를 검증하지만 form change 승인이 아님 |
| Django → AI 작업 요청 | Django AI adapter | AI request schema·Agent | 저장 snapshot·field registry·작업 ID는 Django, 서비스 경계는 `ai-api.md` | KRW integer, form percent는 퍼센트포인트, date와 UTC datetime 구분 | shared bearer와 request·session·thread 결합은 서비스 권한 검증이며 사용자 변경 승인이 아님 |
| AI → Django callback | AI Agent·callback client | Django callback validator | 자연어·원시 change는 AI 출력, 수락된 action·change ID·상태는 Django | AI value는 전달된 field registry 단위를 그대로 사용 | Django 검증 뒤에도 모든 change는 `proposal/pending`; 사용자 결정 전 draft 적용 금지 |
| Django → Browser polling·결정 | Django 작업·action 상태 | Frontend store·resolver | 작업·action·change 감사 상태는 Django, 화면 draft는 Frontend | 승인 응답의 canonical 단일 change만 화면 adapter로 변환 | change별 `approve/reject`; 전체 action 일괄 결정 없음 |
| Browser → Django 폼 저장 | Frontend canonical form adapter | Django serializer·transaction | 확정 재무정보와 DB transaction은 Django | canonical 단위로 전체 폼 재검증 | change 승인과 별개인 최종 저장 동작이 성공해야 DB 저장 완료 |
| Django report → AI chart | Django report adapter | `chart_interpret` | source report·계산 결과는 Django report, 입력 형태는 `ai-api.md` chart 계약 | 금액 KRW, 가정 비율 decimal, 유출 음수 | 읽기 전용이며 form change 생성·수치 추측 금지 |

단위 검토에서는 canonical JSON number를 화면 표시 크기로 다시 추정하지 않도록 경계를 고정했다. `5000`은 언제나 5,000원이고, `500만`·`3억` 또는 화면 기본 단위 해석은 사용자가 입력한 문자열에서만 수행한다. form field의 `percent`는 `5 = 5%`, chart의 `decimal`은 `0.05 = 5%`다.

D1 동결 범위에 남은 미합의 사항은 없다. 아래 runtime 차이와 provider·consumer·E2E 부재는 결정 대기가 아니라 후속 구현·검증 항목이다.

## 공용 fixture와 변경 영향 목록

네 JSON은 `ai-contract-v2`라는 하나의 fixture set이다. 서비스별 복사본을 만들지 않고 필요한 case를 원본 경로에서 직접 읽는다.

| 파일 | 고정하는 범위 | 현재 증거 |
|---|---|---|
| `contract-fixtures/ai-lifecycle/v2/step-registry.json` | 23개 화면 단계·Agent allowlist와 `general` transport fallback | AI router·라우팅 평가·Frontend 계약 검사가 같은 원본을 읽음 |
| `contract-fixtures/ai-lifecycle/v2/lifecycle.json` | 정상·누락·잘못된 자료형·사용자/서비스 권한·접수/실행/callback timeout·네 중복 경계 | JSON 예시 작성, 제품 contract test 미구현 |
| `contract-fixtures/ai-lifecycle/v2/form-actions.json` | `basic-info` 단일, `financial-mindset` 정성, `financial-assets` 반복, `financial-goals` 중첩 변경과 전 change 별도 승인 | AI validator·Django normalizer·Frontend draft resolver가 같은 원본의 의미를 검증 |
| `contract-fixtures/ai-lifecycle/v2/chart-payloads.json` | `lifetime-cashflow` 원본 series·단위·기간·가정·요약과 미준비 동작 | 입력 계약 예시 작성, Backend→AI 전달 미구현 |

fixture의 합성 사용자·세션·금융 값은 계약 검증용이며 실제 사용자 원문이나 credential을 넣지 않는다. `<user-jwt>`, `<AI_SERVICE_BEARER_TOKEN>`, `<generated-uuid>`는 테스트 adapter가 주입하거나 형식을 검사하는 placeholder다.

| 소유 영역 | 생산자·소비자와 파일 영향 | 구현 후 확인할 증거 |
|---|---|---|
| Frontend HTTP·상태 | `front-pjt/src/api/consulting.js`가 v2 endpoint·Idempotency-Key를 생산하고 `front-pjt/src/stores/ai.js`, `front-pjt/src/views/ai/AiChatView.vue`가 processing·failed·expired와 change 결정을 소비 | lifecycle consumer test, 3초 polling 종료 조건, 오류별 화면 상태 |
| Frontend draft resolver | `front-pjt/src/utils/formChanges.js`가 승인된 단일·정성·반복·중첩 change와 stable ID를 적용하고 `front-pjt/src/api/consulting.js` adapter가 canonical 이름·단위로 왕복 변환 | form action fixture 전체, 작은 KRW 보존, 생성 UUID 보존, 승인 change만 적용, draft/저장 완료 구분 |
| Frontend chart | `front-pjt/src/utils/reportData.js`, `front-pjt/src/components/presentation/LifetimeCashflowSlide.vue`, `front-pjt/src/views/ai/AiChatView.vue`가 같은 report 원본을 표시하고 AI 질문 맥락으로 전달 | chart title·기간·단위 표시, 미준비 시 분석 차단, 수치 일치 |
| Django Browser 경계 | `backend-pjt/consulting/urls.py`, `views.py`, message/action serializer가 JWT·소유권·Idempotency-Key·공통 envelope를 강제 | 정상·누락·자료형·타 세션·동일 replay·충돌 API test |
| Django AI 경계 | `backend-pjt/consulting/ai_services.py`, `views.py`, `backend-pjt/config/settings.py`가 v2 request·bearer·retry·callback binding·terminal 불변을 생산·검증 | lifecycle provider/consumer test, 동일 callback message 1개, secret 마스킹 |
| Django 상태·감사 | 작업·action/change 상태 model·migration·Redis adapter가 90초 expired, 10분 active TTL, 24시간 terminal/idempotency와 개별 결정을 소유 | 상태 전이·transaction·TTL test, 반대 결정 409, expired callback 미저장 |
| Django report | `backend-pjt/reports/calculators.py`, `services.py`, `views.py`, serializer/adapter가 `lifetime-cashflow`의 source report와 chart metadata를 생산 | chart fixture producer test, series 산식·단위·generated_at·version 확인 |
| AI 접수·callback | `ai-pjt/schemas/chat.py`, `main.py`, `core/config.py`, callback client가 v2 schema·인증·deadline·접수/전달 idempotency를 처리 | lifecycle consumer/provider test, 5초 timeout과 제한 retry, 실패 callback |
| AI Agent | `ai-pjt/graph/state.py`, `agents/input_assist.py`, `agents/chart_interpret.py`가 form action과 읽기 전용 chart input을 구분 | form fixture 출력 검사, chart에서 form change 0건, 미준비/원본 외 수치 생성 0건 |
| 배포·보안 | 두 서비스의 `AI_SERVICE_BEARER_TOKEN`, TLS, drain·동시 회전과 v2 조정 배포가 필요 | 빈 token 실패, 양방향 smoke test, 90초 drain, 전체 release rollback |
| 계약 검증 | Frontend·Backend·AI targeted test가 같은 form action fixture를 직접 읽는다. lifecycle·chart 전체 case는 후속 provider·consumer test가 필요하다. | G2 provider·consumer test와 세 서비스 통합 명령은 후속 범위 |

## 구현 순서와 완료 증거

1. Django와 AI의 v2 schema·bearer 인증·idempotent request/callback을 구현한다.
2. Django 작업 상태와 change별 action 상태를 구현한다.
3. Frontend를 v2 접수·polling·개별 결정 UI로 전환한다.
4. provider·consumer test가 위 `ai-contract-v2` fixture set을 직접 읽게 한다.
5. Backend가 `lifetime-cashflow` report를 읽기 전용 `chart_payload`로 조합하고 AI가 원본 밖의 수치를 생성하지 않게 한다.
6. D4에서 callback 성공·실패·인증을, D5에서 Front→Django→AI→callback→polling→개별 승인·거절→폼 저장을 검증한다.

현재 D1의 완료 증거는 교차 영역 검토 결과, 동결 문서, 공용 fixture와 승인·단위 targeted contract test다. G1은 여기에 step/form/chart 상호 참조와 실제 foundation adapter의 의미 일치를 검사하지만, runtime E2E와 실제 DB·화면·trace 증거는 아직 미검증이며 구현 후 인수 항목이다. 입력별 현재 활성 여부와 후속 레인은 저장소의 `contract-fixtures/g1/artifact-consistency.json`이 기록한다.
