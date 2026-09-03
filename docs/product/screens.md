# 화면 흐름과 표기 정책

상태: **AI 상담 v2 상호작용 정책 승인 — 전체 화면 정책 미정**

## 정보 구조

AI 상담 화면은 상담 단계, 대화, 현재 폼 또는 분석 차트, AI 작업 상태를 함께 보여준다. 전체 내비게이션과 화면 목록은 추가 합의가 필요하다.

## 사용자 흐름

### 질문과 답변

1. 사용자가 현재 단계에서 질문한다.
2. 화면은 사용자 메시지를 표시하고 AI 작업 상태를 `processing`으로 표시한다.
3. 답변 완료 시 Agent 응답과 필요한 인용을 표시한다.
4. 실패 시 성공 답변을 만들지 않고 재시도·직접 입력·지원 범위 안내 중 가능한 행동을 보여준다.

### AI 폼 변경

1. AI가 canonical 필드, operation, confidence, 사용자 요청 여부를 반환한다.
2. 사용자가 변경을 명시적으로 요청했고 confidence가 0.90 이상이면 현재 미저장 폼에 즉시 반영한다.
3. proposal의 change가 여러 개면 각 change를 별도 카드 또는 독립 control로 표시하고 사용자가 하나씩 승인하거나 거절한다.
4. 화면 단계가 응답 단계와 다르거나 안정 ID 대상을 찾지 못하면 direct change도 proposal로 낮춰 개별 결정 흐름을 사용한다.
5. 승인·거절은 Django에 change별로 기록한다. 승인 응답에 포함된 change만 Frontend 공통 resolver로 draft에 적용하고 거절 change는 적용하지 않는다.
6. 같은 결정을 다시 보내면 기존 결과를 유지하고, 반대 결정 충돌은 사용자에게 이미 처리된 상태로 표시한다.
7. draft 반영과 change 승인은 저장 완료가 아니다. 사용자가 작성 완료를 눌러 Django serializer 검증과 폼 저장이 성공한 뒤에만 저장 완료로 표시한다.
8. v2 AI action은 확정 재무정보를 직접 수정하지 않는다.

### 정성정보 유도

- 한 번에 한 질문만 표시한다.
- 이미 답한 내용과 남은 항목을 구분한다.
- 턴 상한 또는 사용자 중단 시 강요하지 않고 다음 행동을 안내한다.

### 차트 분석

- AI 설명과 함께 근거가 된 차트명, 기간, 단위, 핵심 수치를 표시할 수 있어야 한다.
- AI는 렌더링 option이나 화면 이미지를 해석하지 않고 Django가 제공한 읽기 전용 `chart_payload` 원본만 사용한다.
- 데이터가 없거나 계산이 완료되지 않으면 임의 분석을 표시하지 않는다.

## 화면별 계약

| 상태 | 화면 동작 |
|---|---|
| 빈 상담 | 시작 방법과 지원 범위 안내 |
| processing | 3초 간격 polling, 중복 전송 방지, 90초 processing deadline 표시 |
| completed QA | 답변, 인용, 기준일, 한계 표시 |
| completed proposal | change마다 기존·제안 값, 이유, 독립 승인·거절 버튼과 action 진행 상태 표시 |
| failed retryable | 새 Idempotency-Key로 다시 접수하는 안내와 버튼 |
| failed non-retryable | 직접 입력·지원 범위·문의 안내 |
| expired/not found | 새 요청 필요 안내 |
| unauthorized | 데이터 없이 인증·권한 안내 |

구현 순서는 `basic-info`, `financial-mindset`, `financial-assets`에서 단일·정성·반복 데이터의 제안 UI를 먼저 검증하고 나머지 단계로 확장한다. 최종 지원 범위는 세 단계로 제한하지 않는다.

Frontend·Backend·AI가 공유할 생명주기·form action·chart 예시는 [`AI lifecycle v2 fixture set`](../contracts/fixtures/ai-lifecycle/v2/lifecycle.json), 인수인계 절차는 [`서비스 계약 변경·인수인계`](../engineering/contract-handoff.md)를 따른다.

## 공통 표기와 접근성

- 금액·수익률·나이·기간은 값과 단위를 함께 표시한다.
- 금융 지식 답변은 출처와 기준일을 사용자가 확인할 수 있게 한다.
- AI 제안과 저장 완료 상태를 색상만으로 구분하지 않는다.
- 승인·거절 버튼은 키보드로 접근 가능하고 의미 있는 label을 가진다.
- streaming·typing 효과가 실제 처리 상태와 저장 완료를 오해하게 하지 않는다.
- 투자 실행·수익 보장으로 오해될 표현을 사용하지 않는다.

## 열린 결정

- 사용자가 제안 값을 수정한 뒤 승인하는 흐름
- citation 상세 표시와 외부 원문 연결 방식
