# AI 동작·관측 평가

현재 프롬프트 개선 기준은 F24다. `tests/test_qa_refactor.py`,
`tests/test_input_assist_refactor.py`, `tests/test_request_plan_refactor.py`,
`tests/test_explanation_memory_refactor.py`가 요청 조건·질문별 근거·현재값·코드 소유 실행
정보·대화 기억·최종 다듬기를 검증한다. 정상 Agent 호출 상한은 QA 3회,
직접 입력 변경 2회, 의미 검토가 필요한 변경 3회다. Planner와 최종 다듬기는 별도다.
실제 provider 품질 평가는 mock 회귀와 구분하며, 새 모델 실행 없이 과거 보고서를
현재 release의 품질 증거로 사용하지 않는다.

이 디렉터리는 AI 평가 지표를 실행 가능한 측정 계약으로 옮긴다. 제품 인수
기준과 SLO는 별도 합의가 소유하며, 여기서는 임계값이나 provider 가격표를
확정하지 않는다.

자동입력 시나리오 계약은 `schemas/ai-evaluation-scenario-v1.schema.json`이다.
원본의 `step_id`·`프로세스`·`대화`·`사용자`·`입력값` 구조를 유지한다.
`data/community-retirement-eval-v1.example.json`은 사용자 초안의 intro 2턴 발췌다.
입력값은 해당 턴의 변경분이고 `{}`이면 기존 값을 유지한다. 평가기준은 별도이며
이 스키마에 포함하지 않는다. 입력 처리 규칙은 공유
[AI 평가 문서](/mnt/c/Users/SSAFY/.codex/projects/S15P21B109/docs/ai/evaluation.md)의
「버전 비교용 시나리오 데이터 계약」을 따른다.

JSON Schema 검사는 서비스 모델·DB를 import하지 않는다. `jsonschema`가 설치된
Python에서 실행한다. 별도 작성 환경의 의존성은 `evals/requirements-schema.txt`다.

```bash
# ai-pjt 디렉터리에서 실행
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s evals/schema_tests -v
```

스키마는 Draft 2020-12다. 원본 24단계·43턴을 그대로 검증한다.
한글 항목의 실제 필드 매핑과 버전별 AI 요청 변환은 자동입력 스크립트가 담당한다.

### 버전별 시나리오 실행

평가 스크립트에 넣을 결과만 필요하면 아래 한 줄로 백그라운드 실행한다.
입력 전체를 검증한 뒤 실행하고 터미널은 즉시 반환한다.

```bash
bash /home/ssafy/S15P21B109/ai-pjt/evals/start_scenarios.sh
```

기본 입력은 `evals/data/torifinance-case-v1`, 출력은
`test-results/torifinance-YYYYMMDD-HHMMSS/{vanilla,sep20,master}/case-*.results.json`이다.
두 위치는 스크립트의 첫째·둘째 인자로 바꾼다. **기존 results.json의 배열·턴 구조와
필드는 유지한다.** 평가 시 `counseling_judge.py --input <결과 파일>
--candidate-version master --validate-only`처럼 버전을 지정한다.

진행 상태와 사례별·버전별 토큰, 턴 소요 시간 합계, 비용은 출력 폴더 옆의
`<출력 폴더>.log` 한 파일에서 확인한다. 마지막 버전별 `state`가 `finished`이면
정상 종료, `finished_with_errors`이면 실패/관측 미확인이 포함된다. 종료 표시가
없으면 실행 중이거나 프로세스가 중단된 상태다. `elapsed_ms`는 턴별 지연 합계로,
병렬 실행 전체의 벽시계 시간이 아니다. 비용 미확인은 `null`로 유지하고
`reported_cost_subtotal_usd`는 확인된 사용량·가격의 소계만 나타낸다.

내부 관측·입력 복사·중간 기록은 임시 디렉터리에서 처리하고 정상적인 프로세스
종료 시 정리한다. 최종 결과에는 JSON만 남기고 비교 보고서·CSV는 만들지 않는다.
실패 턴도 보존하며, 사례 실행 중 강제 종료된 경우 완료된 턴만 있을 수 있으므로
파일 존재만으로 전체 완료를 판단하지 않는다. 기존 출력 폴더 재사용과 자동 재실행은
허용하지 않는다. 이 동작은 `scenario_suite.py --results-only`로도 사용할 수 있다.

중단한 실행을 이어갈 때는 새 출력 폴더와 함께 `--reuse-run <이전 내부 실행 폴더>`를
지정한다(여러 번 지정 가능). 같은 입력 hash·모델 설정을 검사하고 완료된 사례를
우선 재사용하며, 미완료 사례는 가장 긴 저장 기록 다음 턴부터 이어간다. 기록된
실패/timeout도 그대로 보존한다. Vanilla는 전송 문맥, graph는 해당 버전의 결정론적
문맥 저장 노드로 복원하며 output schema는 바꾸지 않는다. 중단 당시 응답을 기록하지
못한 요청은 다시 호출될 수 있다. 병렬 실행 수는 `--workers 24`까지 지정할 수 있다.

실행 설정 기본값은 기존 `/tmp/torifinance-run-config.json`과 저장소 루트의 `.env`다.
`SCENARIO_CONFIG`, `SCENARIO_ENV_FILE`, `SCENARIO_PYTHON`, `SCENARIO_WORKERS`로
바꿀 수 있다. `/tmp` 설정과 9월 20일 Python 환경이 삭제되면 해당 경로를 먼저
복구하거나 바꿔야 한다. 스크립트는 누락된 경로를 모델 호출 전에 검사한다.
현재 master는 실행 시점의 작업 디렉터리 코드이며 자동 pull/checkout하지 않는다.

`scenario_runner.py`를 대상 worktree의 Python으로 실행한다. runner와 스키마는
master의 절대 경로를 사용하므로 과거 worktree에 복사하지 않는다. 실행 방법·범위의
단일 원천은 공유 `docs/ai/evaluation.md`의 「시나리오 실행기」 절이다.

```bash
python /home/ssafy/S15P21B109/ai-pjt/evals/scenario_runner.py \
  --worktree /home/ssafy/S15P21B109 \
  --input /home/ssafy/S15P21B109/ai-pjt/evals/data/community-retirement-case-v1.input.json \
  --output /tmp/ai-evaluation/validation --dry-run
```

실측은 `--dry-run`을 빼고 `--env-file` 및 호스트에서 접근 가능한 RAG DB 설정을
제공한다. 기본은 Langfuse `benchmark` 환경이며 `--max-turns 1`로 연결 smoke를
수행할 수 있다. `--repeat`는 대화 상태를 매회 초기화한다. `--runtime-preflight`는
모델 호출 없이 graph와 활성화된 검색 의존성을 조립한다. `--no-langfuse`는 원격
전송만 끄며 모델 호출을 끄지 않는다. `--no-readback`은 수집 확인을 생략한다.
기존 출력 디렉터리는 덮어쓰지 않는다. 실제 실행에는 모델 호출 비용이 발생한다.

Langfuse의 비동기 수집이 늦어 확인되지 않은 경우 같은 `--output`에
`--readback-only`를 지정하면 모델 재호출 없이 원격 결과·비용·보고서만 갱신한다.
이 경우에만 기존 산출물을 갱신한다. exporter 오류는 `manifest.json`의
`export_failures`에 이벤트 종류별로 남기며, 일부 관측이 거절되어도 최종 응답과
로컬 usage는 보존한다. 원격 측정은 `langfuse.json`의 trace별 measurements로 확인한다.
조회 API가 HTTP 429를 반환하면 제한된 횟수만큼 대기 후 재시도하며, 이미 검증된
trace는 재사용한다. 비용은 사용량과 모델 가격이 모두 확인된 경우에만 총액으로 표시한다.
Langfuse 모델 가격 등록 후 기존 trace도 다시 조회하려면 `--readback-only --refresh-readback`을 사용한다.

시나리오 폴더의 세 방식 비교는 `scenario_suite.py`로 실행한다. `--config` JSON은
`vanilla`, `sep20`, `master` 각각에 대상 `python`, `worktree`, `database_url`을
지정한다. Vanilla도 master의 Python/설정을 사용하지만 DB·도구를 호출하지 않는다.
비밀 설정 파일은 산출물에 복사하지 않는다.

```bash
python evals/scenario_suite.py \
  --dataset evals/data/torifinance-case-v1 \
  --config /absolute/path/evaluation-runtimes.json \
  --env-file /absolute/path/.env \
  --output /absolute/path/results --workers 3
```

세 방식은 `gemini-2.5-flash-lite`, temperature 0으로 고정한다. 모든 사례를 먼저
검증한 다음 독립 프로세스에서 실행하고 원격 관측 조회는 직렬화한다. `--resume`은
입력 hash가 같은 완료 사례만 재사용하며 중간에 끊긴 사례를 자동 재호출하지 않는다.
산출물은 `README.md`, `metrics.csv`, `turns.csv`, `all-results.jsonl`, `summary.json`,
`validation.json`, `comparisons/` 및 방식/사례별 원본 기록이다. 응답 품질 점수는 매기지 않는다.
긴 응답을 포함한 원문은 JSONL로 확인하고, 표 계산 분석에는 본문을 제외한 `metrics.csv`를 사용한다.

실행 프로세스가 끝난 후 `--readback-only`는 전체 로컬 이벤트를 trace·call ID로
재대조해 지연 이벤트의 턴 귀속을 보정한다. 원격 검증도 호출 ID와 usage를 비교하고,
동일 호출의 중복 관측은 비용에서 한 번만 센다. 사용량·가격 미확인 호출은 0원으로
간주하지 않는다. 실패 요청은 ERROR 관측이 있으면 응답 본문 없이도 관측 완료일 수 있다.
이 명령은 누락 관측을 서버에 복원하지 않으며, export 오류까지 해결했다는 뜻도 아니다.

호스트 실행은 Compose의 환경 주입을 받지 않는다. GMS 환경에서는
`GOOGLE_GEMINI_BASE_URL=https://gms.ssafy.io/gmsapi/generativelanguage.googleapis.com`도
설정한다. 환경변수가 `--env-file`보다 우선한다. `DATABASE_URL`은 평가용 DB를
가리켜야 하며 공식 인덱스 두 개가 필요하다. `AI_COUNSELING_RAG_ENABLED=true`인
master는 추가로 호환되는 `counseling-candidate` 인덱스가 필요하다. 시작 시 읽기
전용으로 확인하며 실행기가 DB 테이블이나 인덱스를 생성·수정하지 않는다.

동일 runner의 `--worktree`와 실행 Python만 바꾸면 9월 12일·20일에도 사용할 수 있다.
세 버전을 같은 Python 프로세스에 import하지 않는다. 입력 전체가 검증된 뒤
선택한 턴만 호출되며 모든 사용자 발화는 `request_mode=chat`으로 전달한다.
UI 진입·Error Review 자동 요청, 영속 저장, 비동기 요약은 측정 범위에서 제외한다.

`results.json`/`turns.jsonl`은 최종 출력·사용량·턴별 상태, `observations.jsonl`은
마스킹된 관측, `langfuse.json`은 원격 조회 확인과 비용, `report.md`는 읽기용 결과다.
`prepared.json`에는 전체 시나리오의 턴별 누적 입력이 저장된다. 보고서 fixture가
없으면 차트 수치를 만들지 않으며 준비되지 않은 보고서에 대한 실제 응답을 수집한다.
산출물은 입력·출력 원문을 포함하므로 Git에 커밋하지 않는다.

```bash
PYTHONDONTWRITEBYTECODE=1 python -m unittest evals.tests.test_scenario_runner -v
```

`build_graph`는 관측 adapter를 필수로 받는다. Planner·계획 검증·binding·task·artifact·
dependency·최종 다듬기와 Agent/tool 호출은 같은 요청의 typed event로 기록한다.
`MaskedInstrumentationSink`가 직렬화 전에 관측 데이터를 마스킹한다. 설정에 따라
structured log와 Langfuse exporter를 사용하며, exporter 실패가 상담 결과를 바꾸지 않는다.
현재 전송 계약은 v2 SSE다.

통합 누출 회귀는 등록된 task를 실제 executor 경계까지 실행하고 model input,
checkpoint, v2 SSE, application log와 trace exporter의 최종 직렬화 값을 검사한다.
실패·dependency blocked·악성 출력도 포함한다. 허용된 업무값 이외의 금지 원문은
해당 sink에 노출되면 안 된다. 과거 전송 방식의 보관된 평가 결과는 현재 release의
완료 증거와 구분한다.

## F25 출처 기반 상담 평가 준비

`data/counseling-frameworks-v1.json`은 업무 수행·설명과 기술 활용·대화·고객 체감·
재무적 안녕을 별도 축으로 둔다. 한국FPSB6단계와 CFP Board7단계는 합치지 않고,
현재 진단 범위는 전자의1~3단계에 대응시킨다. 공식 인증이나 종합 점수는 산출하지 않는다.
`data/counseling-case-plan-v1.json`은 RAG의120개 상황과80개 행동 사례에 필요한
관찰 항목·자료·적용 불가 사유를 연결한다. 짧은 답변을 전체 업무 준수로 판정하지 않는다.

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest evals.tests.test_counseling_protocol evals.tests.test_counseling_diversity -q
PYTHONDONTWRITEBYTECODE=1 python3 -m evals.counseling_protocol \
  --report /tmp/f25-counseling-protocol.json \
  --markdown /tmp/f25-counseling-protocol.md \
  --template /tmp/f25-counseling-review-template.json
```

검사는 출처·framework·criterion 참조, 사례 누락, 서비스 범위, 평가 상태를 확인한다.
출력의 검증 성공은 **구조 검증**이며 모델·고객·공식 척도 평가가 아니다. 검토 서식의
실제 응답·실행 정보·검토자·관찰 근거는 비어 있다. 기대 예시를 실제 응답으로 넣지 않는다.
같은 사례와 검증 데이터로 대조군/병합 RAG baseline/고객 이해 문단 추가/생성 단계 RAG를
비교할 때 사용하며, 네 변형을 일괄 실행하는 평가기는 현재 연결하지 않았다.
병합 baseline의 opt-in 서비스 경로는 [상담 RAG 실행 안내](../rag/README.md)에 있다.
그 경로의 mock 통합 검증은 아래 서식의 실제 모델·고객 평가 결과를 채우지 않는다.

원래 54개 상황·26개 대화의 내용과 ID를 보존하고 66개 상황·54개 대화를 추가했다.
새 사례의 `variation`은 같은 질문/다른 배경, 같은 재무/다른 고민, 이해 수정,
개입 축소의 비교 방향·묶음·조건을 명시한다. 같은 질문 묶음의 질문과 같은 재무 묶음의
재무 조건을 고정해 차이를 비교하며, 실제 24단계와 `general` 모두 최소4개 상황을 연결한다.
`variation.background`와 `financial_state`는 모든 변형에 공통으로 제공할 합성 시작
맥락이다. 고객 진술을 검증된 수치로 올리지 않으며, `previous_understanding`만 문단
변형의 합성 초기 상태로 추가한다. 기대 답변·체크포인트는 모델 입력에서 제외한다.

새 대화는 3턴 이상이며 `checkpoints`가 각 사용자 턴의 이해 초기화·수정·유지·철회,
검색 필요성, 금지 추론, 다음 턴 재사용 기대를 소유한다. `retain`은 의미상 유지이지
문자열 일치가 아니다. 기대 문단은 채점 참고이며 다음 턴의 모델 입력으로 주입하지 않는다.
각 체크포인트는 해당 턴까지의 정보만으로 검토한다. 정정 이후 금지 조건을 정정 전 턴에
소급하거나 폐기된 가설을 후속 턴의 보존 조건으로 유지하지 않는다.
실제 응답과 관찰된 문단을 이어 실행하고, 고정 후속 발화의 전제가 맞지 않으면 의존하는
후속 턴을 건너뛴 사유를 남긴다. 기존 assistant 발화는 합성 입력 문맥일 뿐 새 모델 출력이 아니다.

검토 서식은 **80대화×4변형=320개** 단위이며 `turn_reviews`에 실제 응답·이해 전후·
스크립트 전제·관찰 증거를 남길 빈 칸을 제공한다. 문단이 없는 세 변형에서는 문단 갱신
검사를 해당 없음으로 두고 응답의 정정 반영은 계속 관찰한다. 모든 결과는 현재 미실행이다.
`counseling_diversity.py`는 원본 보존·대응 조건·단계·턴 참조를 검사하며 의미 품질을 채점하지 않는다.
합성 다양성·사례 수는 고객 대표성의 증거가 아니며 후속 실제 사용의 빈도와 실패로 보완한다.

13개 프로젝트 관찰 항목은 원전의 AI용 공인 점수표가 아니다. A.14 도구 선택·신뢰성은
응답 외의 기술 검증 근거가 필요하고, MITI 참고 협력·공감 관찰은 공식 코딩 점수가 아니다.
고객 체감은 고객 응답이 필요하며, CFPB 척도는 검증된 도구와 조사 조건 없이 채점하지 않는다.
무응답·미관찰·해당없음을0점으로 합치지 않는다. 비용·지연과 기존 AX 지표도 별도다.

원문 확인 결과, 과거 한국FPSB 주소가 연결되는 현재 IFPK 고객 체크리스트는13문항이다.
출처의 당시 발행명·현재 호스트·확인한 개정일·권리를 registry에 기록했다. 매뉴얼 전문을
공용 RAG에 적재하지 않는다. 적용·실측 경계의 단일 원천은 공유 `docs/ai/evaluation.md`다.

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

route·검색·도구·모델·공통 출력 검증은 `OperationTraceEvent`로 측정한다.
검색 이벤트는 승인된 문서 ID와 점수만 가지며 query·excerpt는 기록하지 않는다.
토큰은 모델 이벤트에만 기록하고, 비용은 고정 가격표가 아니라 해당 실행에
적용된 provider 가격 버전이 계산한 `cost_microusd`를 받는다.

`RuntimeTraceEvent` 1.1은 request·thread correlation과 task의 `depends_on`,
dependency 차단의 `blocked_by`, typed artifact 식별자만 기록한다. artifact 식별자는
TaskResult schema를 바꾸지 않고 `request_id:producer_task_id:artifact_type`으로 만든다.
task 실행 event는 executor 종류·이름과 최종 응답 owner를 제한된 token으로
기록하며, 실제 Agent·tool 호출은 같은 trace의
`OperationTraceEvent` 계약을 사용한다.
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

production masker 뒤의 가명화된 structured event로 baseline을 측정한다. downstream
trace 저장소가 일시적으로 실패하면 해당 export만 폐기하고 raw event로 우회하지
않으며, 업무 graph 결과에는 실패를 전파하지 않는다.

F10 runtime baseline은 request 처리와 planner·binding·task·artifact·dependency·
aggregation span과 Agent·tool operation을 하나의 닫힌 cohort로
검증한다. 시작만 있고 종료가 없거나 같은 span의 terminal이 중복된 입력은 보고서를
만들지 않는다. 저장소에 고정한 합성 baseline은 임계값이나 SLO가 아니라 현재 측정
형태와 산출 재현성을 확인하는 기준이다.

```bash
PYTHONPATH=. python -m evals.runtime_observability_report \
  --output evals/artifacts/f10-runtime-observability-baseline.json
```

## F07-T04·F08-T04 Agent 통합 평가

`agent_baseline.py`는 production Agent prompt·graph를 그대로 실행하고, F06-T06이
승격한 `financial-knowledge__official-catalog-2-1-0`과
`interface-guides__official-catalog-2-1-0` active pointer를 먼저 확인한다. QA의
질의 embedding과 각 Agent의 LLM 호출을 애플리케이션 호출 1회로 계수하며,
중단된 선행 시도는 `--prior-model-calls`로 누적한다. fixture와 러너 모두
총 200회를 상한으로 강제한다.

Error Review의 false positive는 실제 검토에 성공한 정상 입력의 field instance를
분모로 계산하고, 같은 항목의 중복 경고는 한 번만 집계한다. 신뢰도 점수·등급과
표시 여부에 따른 이중 집계는 사용하지 않는다. 경험적 오탐 허용률은 별도로 정하며,
합격·불합격은
`contract-fixtures/ai-lifecycle/v2/agent-evaluations.json`이 가리키는 명확한 계약과
결정론적 회귀 테스트만 판정한다.

실제 실행은 DB credential·GMS key를 실행 환경에만 주입한 뒤 `ai-pjt`에서
다음과 같이 실행한다. 보고서에는 credential을 기록하지 않는다.

```bash
PYTHONPATH=. python -m evals.agent_baseline \
  --prior-model-calls 0 \
  --output /tmp/current-agent-model-evaluation.json
```

## Prompt image candidate gate

`agent_baseline.py` 결과의 `runtime`에는 배포 이미지에 고정된 prompt release ID,
현재 registry manifest와 model provider·name·temperature가 포함된다. 실제 모델·보안·
Planner 계약 평가 결과를 아래 정규화된 비교 artifact로 모은 뒤, 같은 corpus와 model
설정으로 실행한 baseline/candidate 이미지를 다음 명령으로 판정한다.

```json
{
  "corpus_id": "prompt-candidate-v1",
  "model_config": {"provider": "gemini", "name": "...", "temperature": 0.0},
  "prompt_release": "2026-09-21.1",
  "prompt_manifest": {
    "release": "2026-09-21.1",
    "prompts": [{"id": "planner.plan", "version": "4.0.0", "sha256": "..."}]
  },
  "quality_metrics": {
    "planner_contract_pass_rate": 0.95,
    "critical_safety_pass_rate": 1.0,
    "grounded_answer_rate": 0.0,
    "form_action_exact_rate": 0.0,
    "error_review_false_positive_rate": 0.0,
    "average_model_calls": 0.0,
    "qa_normal_max_calls": 3,
    "input_direct_max_calls": 2,
    "input_review_max_calls": 3,
    "request_conditions_preserved_rate": 1.0,
    "question_coverage_rate": 1.0,
    "plain_language_pass_rate": 1.0
  }
}
```

```bash
PYTHONPATH=. python evals/prompt_release_gate.py \
  --baseline evals/artifacts/prompt-baseline.json \
  --candidate evals/artifacts/prompt-candidate.json
```

두 artifact의 release는 서로 달라야 하며 manifest가 빠지면 실패한다. 보안 차단은
100%, Planner 계약 통과율은 95% 이상이어야 하고 grounded answer는 2%p 초과 하락할 수 없다.
form field/value exact match와 false positive는 악화되면 안 되며, provider call budget은
baseline보다 최대 1회 또는 25% 중 큰 값까지만 허용한다. 실패한 candidate는 현재
prompt manifest를 포함한 새 배포 이미지로 승격하지 않는다.

F24의 기본 Agent 호출 상한은 QA 3회·직접 입력 변경 2회·의미 검토 변경 3회이며
평균 호출 수가 낮아도 각 상한을 넘으면 실패한다. 요청 조건 보존·부분 질문 누락 없음·
쉬운 표현은 평가셋의 모든 사례에서 충족해야 한다. 후자의 세 rate는 실제 답변을 정해진
사례별 기준으로 검토한 결과이며 mock PASS만으로 채우지 않는다.
