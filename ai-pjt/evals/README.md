# P11 관측·AX baseline

이 디렉터리는 AI 평가 지표를 실행 가능한 측정 계약으로 옮긴다. 제품 인수
기준과 SLO는 별도 합의가 소유하며, 여기서는 임계값이나 provider 가격표를
확정하지 않는다.

현재 단계는 P11 합성 이벤트와 F10-T01의 sink-independent
`InstrumentationPort`·`FakeInstrumentationSink`가 준비된 상태다. 실제
planner·task·dependency·artifact producer 연결은 F10-T02가 소유한다. F11 마스킹
구현이 production 직렬화 경계에 연결되기 전에는 Langfuse·LangSmith exporter를
활성화하지 않는다.

## 이벤트와 측정 단위

- 측정 단위는 가명화된 `attempt_id`이며 실제 producer는 하나의 폼 단계 시도를
  `session + step + attempt` 경계로 생성한다.
- `started`는 단순 화면 진입이 아니라 첫 사용자 입력 또는 첫 AI 도움 요청에
  기록한다.
- `valid_completed`는 Backend의 canonical schema·serializer 검증과 저장이 모두
  성공한 경우에만 기록한다.
- `abandoned`와 proposal `no_response`는 합의된 관찰 기간이 닫힌 뒤 기록한다.
- `active_interval`은 idle 구간을 제외해 합산한다. idle 판정 시간은 실제 baseline
  전에 Frontend·Backend owner가 확정한다.
- `user_turn`은 사용자 발화만 세며 assistant·system·tool 호출은 포함하지 않는다.

route·검색·도구·모델·공통 출력 검증·callback은 `OperationTraceEvent`로 측정한다.
검색 이벤트는 승인된 문서 ID와 점수만 가지며 query·excerpt는 기록하지 않는다.
토큰은 모델 이벤트에만 기록하고, 비용은 고정 가격표가 아니라 해당 실행에
적용된 provider 가격 버전이 계산한 `cost_microusd`를 받는다.

`RuntimeTraceEvent`는 request·thread·plan correlation과 task의 `depends_on`,
dependency 차단의 `blocked_by`, typed artifact 식별자만 기록한다. artifact 식별자는
TaskResult schema를 바꾸지 않고 `plan_id:producer_task_id:artifact_type`으로 만든다.
이벤트에는 사용자 메시지, 고객 snapshot, 도구 인자나 artifact 값 같은 자유 형식
payload를 넣지 않는다. P11 이벤트와 runtime 이벤트는 같은 `InstrumentationPort`로
전달할 수 있고, fake sink의 `baseline_events`는 기존 baseline 입력만 분리한다.

## AX 산식

| 지표 | 산식·판정 |
|---|---|
| 유효 폼 완료율 | 유효 완료 attempt / 시작 attempt |
| 활성 작업 완료 시간 | 유효 완료 attempt의 active interval 합계 p50·p90 |
| 완료 사용자 턴 | 유효 완료까지 발생한 user turn 수 p50·p90 |
| 중도 이탈률 | 관찰 기간이 닫힌 abandoned attempt / 시작 attempt |
| 저장 전 재작업률 | 저장 전 다시 수정된 change / 적용된 AI change |
| 제안 outcome | proposal별 approved·modified·rejected·no_response의 상호 배타 비율 |
| 적용 후 되돌림률 | 저장 뒤 되돌린 change / 적용된 AI change |
| 잘못된 제안 되돌림률 | reason이 wrong_suggestion인 되돌림 / 적용된 AI change |
| 수동 전환율 | manual_transition이 발생한 attempt / 시작 attempt |
| 결정론적 검증 차단률 | blocked output-validation / 전체 output-validation |
| 유효 완료 1건당 AI 비용 | AI cohort 전체 비용(실패·이탈 포함) / AI cohort 유효 완료 |

유용성·신뢰·인지된 노력 감소는 완료 사용자만이 아니라 이탈 사용자를 포함해
표본 수집한다. 전반적 만족도는 AI 효과와 UI·서비스 효과를 분리하기 어려워 이
계약에 포함하지 않는다. AI/미사용 차이는 무작위 배정 또는 동질 cohort가 확인된
실험에서만 해석하며, baseline은 두 cohort의 완료율·이탈률·활성 시간을 나란히
산출할 뿐 인과 효과를 주장하지 않는다.

## 실행

저장소의 `ai-pjt`에서 다음 mock 단위 테스트를 실행한다.

```bash
python3 -m unittest discover -s evals/tests -v
```

F10-T02에서 실제 producer가 이 포트를 사용하도록 연결하고, F11의 production
masker와 exporter 직전 sentinel 검사를 통과시킨 후 실제 baseline을 측정한다.

## F07-T04·F08-T04 Agent 통합 평가

`agent_baseline.py`는 production Agent prompt·graph를 그대로 실행하고, F06-T06이
승격한 `financial-knowledge__official-catalog-2-1-0`과
`interface-guides__official-catalog-2-1-0` active pointer를 먼저 확인한다. QA의
질의 embedding과 각 Agent의 LLM 호출을 애플리케이션 호출 1회로 계수하며,
중단된 선행 시도는 `--prior-model-calls`로 누적한다. fixture와 러너 모두
총 200회를 상한으로 강제한다.

Error Review의 false positive는 정상 입력에 존재하는 canonical field instance를
분모로 계산하고 low·medium·high 건수와 field별 비율을 모두 남긴다. low는
실제 사용자 표시에서 숨기므로 raw와 visible 비율을 따로 보고한다. 허용률은
아직 두지 않으며, 모델 결과는 합격 기준이 아닌 baseline이다. 합격·불합격은
`contract-fixtures/ai-lifecycle/v2/agent-evaluations.json`이 가리키는 명확한 계약과
결정론적 회귀 테스트만 판정한다.

실제 실행은 DB credential·GMS key를 실행 환경에만 주입한 뒤 `ai-pjt`에서
다음과 같이 실행한다. 보고서에는 credential을 기록하지 않는다.

```bash
PYTHONPATH=. python -m evals.agent_baseline \
  --prior-model-calls 0 \
  --output evals/artifacts/f07-f08-production-model-baseline.json
```
