# 은퇴 시뮬레이션 통합 호출·입출력 규격

작성일: 2026-09-09. 기준: 입력 어댑터 MR !45 병합(`e54d773`) 이후 최신 master `09554bd`.
상태: **통합 분석 브랜치 0번 준비·1번 규격 문서화·2번 변경 입력 매퍼·3번 통합 실행·
4번 결과/오류 JSON 변환·5번 합성 성능 측정/후보 한도·6번 통합 CLI/확대 회귀/인수인계 완료.
실제 서비스 연결·운영 검증은 후속 작업이다.**
이 문서는 후속 모듈 구현 기준이며 BE·FE가 승인한 HTTP 계약이나 서비스 배포 완료 선언이 아니다.

## 1. 목적·완료 범위

BE가 이미 읽은 저장형 snapshot과 명시적 가정을 받아 기존 MC/What-if를 실행하고,
결과를 브라우저에 전달 가능한 자료형으로 변환하는 **독립 Python 호출 경계**를 준비한다.
기존 [입력 어댑터](input-adapter.md), [MC 엔진](monte-carlo-engine.md),
[What-if 엔진](what-if-engine.md)을 재사용하며 금융 계산식을 복제하지 않는다.

- 최초 0·1번(`debddd4`)은 문서만 변경했다. 후속 2번은 변경 입력 매퍼·export·테스트와
  [사용/검증 가이드](what-if-adapter.md)를 추가했다. 기존 모델·계산식은 변경하지 않았다.
- 후속 3번은 [통합 실행·불변 결과·실행 오류](analysis-execution.md)와 전용 테스트를 추가했다.
- 후속 4번은 [결과·오류 JSON 변환](analysis-output.md)과 숫자/정합성/격리 검증을 추가했다.
- 후속 5번은 계산 호출과 분리된 [합성 성능 측정 도구·결과·후보 한도](analysis-performance.md)를 추가했다.
- 후속 6번은 [통합 CLI·사용 예시·확대 회귀](analysis-samples.md)와 [BE 인수인계](be-handoff.md)를 추가했다.
- 변경 위치는 `ai-pjt/retirement_simulation/` 안으로 제한한다. 외부 의존성은 추가하지 않는다.
- Agent·라우팅·컨텍스트·재무상담 매뉴얼, BE/FE·DB·공통 설정·Docker·CI·Jira는 수정하지 않는다.
- DB/파일/네트워크/환경변수 조회, 현재 시각 읽기, 로그 출력, 결과/원본 저장, 캐시를 계산 호출에 넣지 않는다.
- `customer_info` 조립·`current_form` 병합·소유권 검사는 재구현하지 않는다.
- 실제 배포는 **BE에서 패키지 직접 호출**이다. AI 서버 진입점이나 별도 HTTP 서버를 만들지 않는다.

원천 상세 규칙은 [저장 입력 매핑 규격](input-mapping-rules.md), 화면/배포/역할 경계는
[서비스 연결 설계](service-integration-design.md)를 따른다. 아래 `v1`은 모듈 계약 버전이고
프로젝트의 기존 `/v1`, `/v2` HTTP 경로·AI 계약 버전과 관계없다.

## 2. 공개 인터페이스와 구현 상태

`map_what_if_scenarios`, `analyze_retirement`, `RetirementAnalysisResult`, `RetirementAnalysisError`는
구현되어 패키지에서 import할 수 있다. 두 JSON 변환 함수와 출력 전용 오류 코드도
**4번에서 구현·검증 완료**했다. 6번의 [통합 CLI](analysis-samples.md)는 이를 호출하는 별도 합성
개발 도구이며 공개 함수 규격을 바꾸지 않는다. HTTP 진입점이나 stub은 추가하지 않았다.

```text
map_what_if_scenarios(values) -> tuple[WhatIfScenario, ...]

analyze_retirement(
    saved_data, assumption_values, *,
    scenarios=(), confirmed_empty_sections=(),
) -> RetirementAnalysisResult

analysis_to_dict(analysis: RetirementAnalysisResult) -> dict[str, object]
analysis_error_to_dict(error: RetirementAnalysisError) -> dict[str, object]
```

| 경계 | 책임·반환 |
|---|---|
| `map_what_if_scenarios` — 구현 완료 | 저장 원천과 별도로 받은 변경 dict 목록을 기존 불변 시나리오로 변환. 계산/난수 생성 없음 |
| `analyze_retirement` — 구현 완료 | 원천·가정·변경 검증, 적용 후 프로필 구성, 기존 엔진 1회 호출. 성공하면 불변 결과 묶음 |
| `analysis_to_dict` — 구현 완료 | 엔진 재실행 없이 명시적 출력 매핑·숫자/정합성 검증. 매 호출마다 새 dict/list 반환 |
| `analysis_error_to_dict` — 구현 완료 | 이 계층의 검증된 오류만 안전한 코드/경로/고정 설명으로 변환. 임의 예외 직렬화는 하지 않음 |

`RetirementAnalysisResult`는 frozen/slots/keyword dataclass로 구현했다.

| 내부 필드 | 내용·불변 조건 |
|---|---|
| `mapped_input` | 기존 `ProfileMappingResult`. 기준 프로필·모든 안내·매핑 버전 보존 |
| `scenario_definitions` | `tuple[WhatIfScenario, ...]`. 요청 순서와 유일한 식별자 유지 |
| `scenario_profiles` | `tuple[RetirementProfile, ...]`. 각 변경을 원래 기준에 적용한 프로필 |
| `calculation` | `MonteCarloResult` 또는 `WhatIfResult`. 아래 모드·기간·가정·시나리오와 일치 |
| `contract_version` | 생성 인자로 변경할 수 없는 `retirement-analysis-v1` |
| `mode` property | 시나리오 0개면 `baseline`, 1~10개면 `what_if` |

시나리오 0개일 때 두 시나리오 tuple은 비어 있고 MC 결과만 허용한다. 1~10개일 때는
두 tuple과 비교 결과의 수·순서·ID가 같아야 한다. 적용 프로필은 각 정의를 기준에 적용한
결과와 같아야 하며 계산 기간·가정·실행 메타데이터도 일치해야 한다. 새 진입점은 생성 경로만
사용한다. 직접 수익률 경로를 주입하는 테스트/고급 호출은 기존 엔진 인터페이스에 남긴다.
수동 결과 모델 생성이 실제 실행·snapshot 소유권·입력 완료를 증명하지는 않는다.

## 3. 입력과 What-if 변환

### 3.1 호출 인자

| 인자 | 필수/기본·규칙 |
|---|---|
| `saved_data` | 필수 `Mapping`. `customer_info["saved_data"]`에 대응. 기본정보와 기존 6개 목록 요구 |
| `assumption_values` | 필수 `Mapping`. 평균·변동성·물가·경로 수·시드 5개 모두 명시. 기존 어댑터의 unknown/null/단위 규칙 유지 |
| `scenarios` | list/tuple, 기본 `()`. 0~10개. `None`, dict 단독, generator, 기존 모델 객체 목록은 거절 |
| `confirmed_empty_sections` | 기존 어댑터 규칙의 list/tuple, 기본 `()`. 원천이 실제로 없다는 확인 사실만 전달 |

별도 `mode` 인자는 받지 않는다. 시나리오 목록이 비면 기준 MC, 비어 있지 않으면 기준+What-if다.
`[{'scenario_id': 'unchanged', 'changes': {}}]`는 기준 모드가 아니라 명시적 무변경 비교다.
`scenarios=None`을 빈 목록으로 취급하지 않는다. 필수 Python 인자 누락/미지원 keyword는
`TypeError`이며 데이터 검증 오류와 구분한다. 외부 HTTP의 실행 의도는 연결부에서 이 인자로 바꾼다.

금융자산 합산·원 단위 정수·연금 일정·생활비 coverage·범위 안내는 기존 입력 어댑터를 그대로
호출한다. `saved_data`의 기존 허용 추가 필드와 변경 dict의 엄격한 필드 규칙은 서로 다르다.
없음 확인·운영 가정·경로 수·시드는 서비스가 검증해서 전달해야 하며 브라우저 입력을 신뢰하지 않는다.

### 3.2 변경 목록 규격

각 행은 `scenario_id`, `changes` 두 키가 모두 필요한 `Mapping`이다. 추가 키는 거절한다.
ID는 기존 `WhatIfScenario`의 ASCII `[a-z][a-z0-9_-]{0,63}` 규칙을 따르고 `baseline`은 예약어다.
대소문자/공백을 자동 보정하지 않으며 목록 전체에서 유일해야 한다. 자유 입력 제목·고객 ID가 아니다.

`changes`는 `Mapping`이며 아래 세 키만 허용한다. 빈 dict는 무변경이다.

| 변경 필드 | 생략/`None` | 명시적 값 |
|---|---|---|
| `monthly_savings` | 기준 유지 | 0 이상 원 단위 정수. 증액분이 아닌 변경 후 월 총액 |
| `retirement_age` | 기준 유지 | 18~100세 정수. 원래 기준의 현재/은퇴 나이와 추가 정합성 검사 |
| `retirement_expenses` | 기준 전체 일정 유지 | 기간 행 list/tuple로 전체 교체. 빈 목록은 `missing_value` 오류 |

생활비 행은 `from_age`, `to_age`, `monthly_amount`만 허용하며 모두 필수다. 나이는 1~120세,
시작≤종료, 월액은 0 이상 정수다. bool·문자열·소수·null 금액을 변환하지 않는다.
유효한 입력 순서는 변경 정의에 유지하고 실제 적용 프로필은 기존 모델이 나이순으로 정렬한다.
모든 구간 중복과 변경 후 은퇴 연도 공백은 최종 적용 단계에서 검사한다. 기간을 합치거나 평균 내지 않는다.

`map_what_if_scenarios`는 구조·필드·개별 값·ID·목록 개수만 검증한다. 기준 의존 검증은
`create_what_if_profile()`에 위임하고 `analyze_retirement()`가 계산 전에 모든 변경안을 적용한다.
이미 과거에 은퇴한 나이의 변경은 거절하지만 동일 나이 명시는 무변경이다. 조기 은퇴로 새로
포함되는 연도는 생활비 전체 일정에 있어야 한다. 연금 이동·과거 재무정보 수정·시나리오 누적 적용은 없다.

새 매퍼는 기존 `InputMappingError`의 안전한 코드·경로 원칙을 재사용한다.
예: 누락 `missing_field`, 구조 `invalid_type`, 범위/ID 문법/개수 `invalid_value`,
빈 생활비 `missing_value`, 중복 시나리오 ID `duplicate_id`, 추가 키 `unknown_field`.
경로는 `scenarios[0].changes.monthly_savings`처럼 고정 키와 배열 위치로 만들고, 알 수 없는
키 이름·원천 ID·입력값을 넣지 않는다. 추가 키 오류는 해당 객체 경로까지만 표시한다.
새 코드가 필요해지면 기존 어댑터를 임의 변경하기 전에 이 규격과 관련 회귀 범위를 먼저 검토한다.

구현된 매퍼의 다중 오류 검증 순서·기존 엔진 호출 예시·회귀 결과는
[What-if 변경 입력 가이드](what-if-adapter.md)에 정리했다.

## 4. 통합 실행과 재현성

검증/실행 순서는 다음으로 고정한다. 여러 오류가 있으면 이 순서의 첫 오류만 반환한다.

1. `map_retirement_profile(saved_data, confirmed_empty_sections=...)` 1회.
2. `map_simulation_assumptions(assumption_values)` 1회.
3. `map_what_if_scenarios(scenarios)` 1회.
4. 각 변경안을 원래 기준에 독립 적용하여 전체 프로필 검증. 하나라도 실패하면 경로 생성/계산하지 않음.
5. 0개면 `calculate_monte_carlo()` 1회, 1~10개면 `calculate_what_if()` 1회.
6. 결과 묶음 반환. 호출자가 `analysis_to_dict()`로 전체 변환 성공을 확인한 뒤 JSON으로 인코딩한다.

What-if 실행 전에 별도 MC를 호출하거나 변경안마다 새로운 시장 경로를 만들지 않는다.
기존 What-if 엔진 내부의 순수 프로필 재검증은 허용하지만 경로 생성·기준 계산은 배치당 1회다.
원본·전역 난수 상태는 유지하고 입력 dict를 결과에 보관하지 않는다. 모든 경로와 모든 변경안을
반영한 결과만 성공이다. 일부 경로/시나리오를 빼고 성공률을 다시 계산하거나 부분 JSON을 반환하지 않는다.

통합 호출은 자동으로 입력의 동시 수정이나 DB snapshot 일관성을 보장하지 않는다. 호출자는
매핑 중 원천을 변경하지 않아야 한다. 소유권·같은 시점 읽기·최신 보고서와의 일치는 BE 책임이다.
시각·실행 ID·`baseline_key`·사용자/상담 ID는 이 모듈이 만들지 않는다. 따라서 같은 유효 입력·
설정·코드·환경은 같은 계산 및 출력 내용을 재현할 수 있다. 서비스가 필요로 하는 시각/식별자는
연결부에서 별도 envelope로 결합하며 금융값의 단순 해시를 공개 식별자로 사용하지 않는다.

## 5. JSON으로 변환 가능한 출력

`analysis_to_dict()`는 JSON 문자열이 아닌 Python dict를 반환한다. 명시적 allowlist로 모든 필드와
파생 property를 매핑한다. `asdict()`만 쓰거나 객체 `__dict__`/`repr`을 그대로 노출하지 않는다.
구체적인 모델/tuple만 지원하며, 복사본의 순수 정합성 검증이 원본을 보정하거나 엔진을 다시 실행하지 않는다.
정확한 타입·검증 순서·실행 예시·회귀 결과는 [출력 변환 가이드](analysis-output.md)를 따른다.
아래 최상위 필드는 항상 존재하며 JSON 키 순서는 소비 계약이 아니다.

| 필드 | 타입·원천 |
|---|---|
| `contract_version` | `"retirement-analysis-v1"`. 신규 모듈 호출/출력 계약 버전 |
| `mode` | `"baseline"` 또는 `"what_if"` |
| `mapping_version` | `mapped_input.mapping_version` (`retirement-input-v1`) |
| `units` | `amount="KRW"`, `monthly_amount="KRW/month"`, `probability="fraction"`, `probability_delta="pp"`, `annual_rate="fraction"`, `age="years"` |
| `notices` | 모든 안내를 원래 순서대로 `{code, field_name}` 목록으로 변환. 생략/중복 제거/문구 임의 생성 없음 |
| `baseline_profile` | 기준 프로필의 나이 3개·유동자산·월 적립·생활비·연금 일정. 원천 행 ID/상품명/고객 정보 없음 |
| `metadata` | 배치 실행 메타데이터. 아래 필드 명시적 매핑 |
| `baseline` | 기준 MC 요약. What-if도 동일 배치에서 계산한 기준을 포함 |
| `scenarios` | 기준 모드는 `[]`. 비교 모드는 입력 순서의 시나리오 결과 목록 |

프로필 필드는 기존 `RetirementProfile`과 동일한 이름을 쓴다. 생활비 행은
`{from_age, to_age, monthly_amount}`, 연금 행은 `{start_age, end_age, monthly_amount}`다.
생활비는 현재 나이 기준 월 현재가치, 연금은 입력된 명목 월액, 자산 출력은 명목 원이다.
새 JSON이 원본 입력/수령액 추정을 대신하거나 BE 쓰기 payload가 되는 것은 아니다.

### 5.1 배치 메타데이터와 계산 요약

`metadata` 필드:

- `current_age`, `end_age`, `num_paths`: 실제 계산 기간·표본 수.
- `assumptions`: 기존 설정 5개 모두. `seed`는 **설정한** 시드다.
- `effective_seed`, `path_source`, `return_model`, `rng_algorithm`: 배치 `MonteCarloMetadata`의 실제 값/property.
- `mc_model_version`, `what_if_model_version`: 각각 `retirement-mc-v1`, 비교일 때 `retirement-what-if-v1`(기준 모드는 `null`).
- `python_version`, `python_implementation`: 엔진이 기록한 실행 환경.

새 진입점의 배치 `path_source`는 `generated`다. 변동성 0이면 `return_model="constant"`,
`effective_seed=null`, `rng_algorithm=null`이며 설정 시드는 따로 남는다.
가정의 금융/시장 출처·갱신 버전은 값이나 모델 버전으로 추정하지 않는다. 그 출처를 선택·보존하는
서버 설정은 후속 연결 작업이며, 메타데이터에 임의의 출처나 `demo`를 운영값으로 주입하지 않는다.

기준 및 각 변경 결과의 MC 요약 필드는 모두 동일하다.

| 필드 묶음 | 정확한 필드·형태 |
|---|---|
| 개수 | `num_paths`, `success_count`, `depletion_count`, `never_depleted_count` |
| 확률 | `success_probability`, `depletion_probability` (0~1) |
| 나이별 잔액 | `asset_percentiles`: `{age, balance: {p10, p50, p90}}` 목록 |
| 최초 고갈 분포 | `first_depletion_counts`: `{age, count}` 목록. 0개 나이도 생략하지 않음 |
| 최종 결과 | `final_balance_percentiles`, `final_shortfall_percentiles`: 각각 `{p10, p50, p90}` |
| 하위 실행 출처 | `execution`: `{path_source, effective_seed}`. 각 MC 결과의 실제 메타데이터에서 읽음 |

나이별 두 목록은 현재~종료 연도를 모두 포함하고 오름차순이다. 성공 개수·확률·최종 잔액처럼
property인 필드도 반드시 포함한다. 공통 가정/모델/환경은 배치에 한 번만 표시하고 일치 여부를 검증한다.
기준 MC 단독의 `execution.path_source`는 `generated`, What-if의 기준/변경 자식은 모두
`provided`이고 자식 시드는 `null`이다. 배치의 생성 시드를 자식 결과에 거짓으로 복사하지 않는다.

### 5.2 변경 결과와 차이

각 `scenarios[]`는 `scenario_id`, `changes`, `profile`, `result`, `delta` 다섯 필드를 갖는다.
`changes`는 위 세 변경 키를 항상 포함하고 미변경 필드는 `null`로 정규화한다. 명시적 0은 유지한다.
빈 변경 dict와 모든 필드가 null인 입력은 같은 의미로 출력되며, 입력의 생략 여부 자체는 보관하지 않는다.
`profile`은 실제 적용된 전체 프로필, `result`는 MC 요약이다. 기준 결과를 변경안마다 복사하지 않는다.

`delta`는 기존 `WhatIfScenarioResult`의 다음 필드/property를 그대로 변환한다.

- `success_count_delta`, `depletion_count_delta`, `never_depleted_count_delta`.
- `success_probability_delta_pp`, `depletion_probability_delta_pp`.
- `asset_deltas`: `{age, balance: {p10, p50, p90}}` 목록.
- `first_depletion_deltas`: `{age, count}` 목록. 음수 개수를 허용한다.
- `final_balance_delta`, `final_shortfall_delta`: `{p10, p50, p90}`.

차이는 모두 변경−기준이다. 확률은 fraction으로 전달하지만 차이는 이미 ×100한 pp이므로
화면에서 다시 100을 곱하지 않는다. 분위수 차이에 정렬 조건을 추가하지 않는다. 이는 두 분포의
동일 분위수 차이지 경로별 차이의 분위수가 아니다. 부족액 차이 음수는 부족 감소를 뜻한다.
고갈이 없는 경우는 나이별 0개·미고갈 개수로 표현하고 대표 고갈 나이나 신뢰구간을 새로 만들지 않는다.

### 5.3 숫자·직렬화·개인정보 경계

v1 출력의 모든 정수는 JavaScript 안전 정수 범위 `[-9007199254740991, 9007199254740991]` 안이어야 한다.
실수도 유한해야 하며 절댓값이 같은 상한을 넘으면 거절한다. 이는 자원/금융상품 한도가 아니라
출력 호환성의 보수적 제한이다. `NaN`, `Infinity`, 범위 초과를 null/0/문자열로 바꾸거나 잘라내지 않는다.
금액은 표시용 반올림 없이 기존 float 값을 유지한다. 이 범위 검사가 모든 소수 금액의 정확한
10진수 표현을 보장하는 것은 아니며 엔진의 binary64 정밀도를 확대하지 않는다.

이 제한은 **출력 계층**에 적용한다. 기존 입력/계산 함수의 정수 범위와 큰 시드 지원을 변경하지 않는다.
큰 정수 자산·변경액·시드가 내부 계산까지 성공해도 JSON 변환은 `unsafe_json_number`로 실패할 수 있다.
따라서 서비스는 계산 결과를 먼저 일부 전송하지 말고 전체 변환 성공을 확인해야 한다.
운영 입력의 사전 제한과 성능 보호는 후속 연결 작업이다. 로컬 합성 측정의 후보 한도는 운영 계약이 아니다.

JSON 자료형은 dict/list/str/int/float/bool/None만 허용하고, bool은 금액·개수·나이·시드를 대신하지 않는다.
dict 키는 문자열이고 순환 참조/객체 자체를 출력하지 않는다. `json.dumps(payload, allow_nan=False)`가
성공해야 하며, 정상 범위의 수치 의미는 JSON round-trip 후에도 유지돼야 한다.
매번 새 컨테이너를 반환해 반환 dict를 바꿔도 내부 불변 결과와 다음 변환 결과는 바뀌지 않아야 한다.
출력 profile도 재무정보이므로 후속 BE 인증·소유권 검사를 생략하거나 일반 로그에 덤프하지 않는다.

## 6. 오류 계약

`RetirementAnalysisError`는 `Exception`을 상속하는 통합 계층의 오류다. 실행 오류 5개와
출력 오류 2개 및 각 허용 경로 검증을 구현했다. 기존 독립 함수의 예외 규격은 바꾸지 않는다.
검증된 `code`, `stage`, `field_name`, `reason_code`, 고정 `message`를 갖고, 입력 원문/임의 객체를 보관하지 않는다.
생성 인자는 `code`, `field_name`, keyword 전용 `reason_code=None`이며, stage/message는
허용 코드에서 결정한다. 새 결과 모델을 잘못 직접 생성한 경우는 기존 `ModelValidationError`로 거절한다.
통합 함수는 알려진 오류만 아래와 같이 변환하고 예외 chaining의 원문 표시를 억제한다.

| 발생 위치 | `code` / `stage` | `reason_code`·경로 |
|---|---|---|
| 저장 입력 변환 | `input_mapping_failed` / `input` | 기존 `InputMappingError.code`, 기존 안전 경로 유지 |
| 가정 변환 | `assumption_mapping_failed` / `assumptions` | 기존 코드, `assumptions...` 경로 |
| 변경 목록 변환 | `scenario_mapping_failed` / `scenarios` | 기존 코드, `scenarios...` 경로 |
| 기준 의존 변경 적용 | `scenario_application_failed` / `scenarios` | `invalid_profile`, `scenarios[index].changes.<허용 필드>` |
| MC/What-if 수치 계산 | `calculation_failed` / `calculation` | `null`, 안전한 계산 위치 |
| 출력 대상 모델 정합성 실패 | `invalid_analysis_result` / `output` | `null`, `analysis` 또는 허용된 출력 경로 |
| 비유한/범위 초과 숫자 | `unsafe_json_number` / `output` | `null`, 허용된 숫자 출력 필드 경로 |

변경 후 은퇴 나이가 계획 종료를 넘어서 모델의 `end_age` 검증에 실패한 경우, 오류는
변경할 수 없는 `changes.end_age`가 아니라 `scenarios[index].changes.retirement_age`로 매핑한다.
생활비 공백/중복은 `scenarios[index].changes.retirement_expenses`로 연결하고, 알 수 없는
모델 경로는 해당 `scenarios[index].changes` 객체 경로로 축약한다.

`message`는 코드별 고정 영문 설명으로 구현한다. `str(error)`나 임의의 모델 오류 문구를 HTTP로
전달하지 않는다. 계산 오류의 stage/field가 허용 목록에 없으면 `field_name="calculation"`으로
축약한다. 알려진 위치도 고정 키와 배열 index로만 구성하고 시나리오 ID·고객 값은 넣지 않는다.
수치 오류의 path index/age 등 상세 진단 객체를 이 v1 오류 JSON에 복사하지 않는다.

현재 실행 오류의 정확한 위치 allowlist·전체 실패 처리·검증 결과는
[통합 실행 가이드](analysis-execution.md)에 있다. 출력 오류의 코드·숫자/객체 경로 구분과
직렬화 시 재검증은 [출력 변환 가이드](analysis-output.md)에 있다.

`analysis_error_to_dict()`의 출력 형태는 다음 JSON 예시다. 성공 payload와 함께 반환하지 않는다.

```json
{
  "contract_version": "retirement-analysis-v1",
  "error": {
    "code": "input_mapping_failed",
    "stage": "input",
    "field_name": "pension_assets",
    "reason_code": "empty_confirmation_required",
    "message": "Retirement input mapping failed."
  }
}
```

다른 코드의 고정 message는 순서대로 `Simulation assumption mapping failed.`,
`What-if scenario mapping failed.`, `What-if scenario application failed.`,
`Retirement calculation failed.`, `Analysis result is invalid.`, `Analysis contains an unsafe JSON number.`이다.
오류 모델 생성/변환 시 코드·stage 조합, reason의 허용 코드/필요 유무, 경로를 검증한다.
잘못 수동 생성한 오류를 안전한 것으로 직렬화하지 않는다. 임의 예외를 이 함수에 넘기면
기존 `ModelValidationError`로 거절한다. 예상하지 못한 프로그래밍 오류·메모리 부족·중단은 성공 응답으로
감추지 않고 전파한다. 외부의 최종 비식별 500 처리·timeout·인증 오류·재시도 정책은 BE 책임이다.

HTTP status·라우트·응답 envelope·작업 ID·스트리밍은 이 오류 계약으로 확정하지 않는다.
현재 통합 함수에서는 실행 오류 5개, 성공 결과의 출력 변환에서는 출력 오류 2개가 발생할 수 있다.
`analysis_error_to_dict()`는 7개 모두 지원한다. 기존 순수 함수는 기존 예외를 유지한다.

## 7. 합성 입출력 예시와 검산

아래 첫 JSON은 구현된 `analyze_retirement`에 전달할 호출 인자 묶음이다. 현재 BE 요청 본문이
아니며 반환 JSON도 아니다. 설정은 손계산용 0%이고 시장 전망/운영 기본값이 아니다.

```json
{
  "saved_data": {
    "basic_info": {"age": 60, "retirement_age": 60, "end_age": 60},
    "financial_assets": [{"asset_type": "예금/적금", "current_value": 10000, "monthly_savings": 0}],
    "pension_assets": [],
    "goal_periods": [{"phase": "after", "from_age": 60, "to_age": 60, "items": [{"amount": 1000}]}],
    "real_estate_assets": [], "liabilities": [], "goal_milestones": []
  },
  "assumption_values": {
    "annual_return_mean": 0, "annual_return_volatility": 0,
    "annual_inflation_rate": 0, "num_paths": 2, "seed": 7
  },
  "confirmed_empty_sections": ["pension_assets"],
  "scenarios": [{
    "scenario_id": "lower_expenses",
    "changes": {"retirement_expenses": [{"from_age": 60, "to_age": 60, "monthly_amount": 800}]}
  }]
}
```

기준은 `10000 − 12×1000 = −2000`, 변경은 `10000 − 12×800 = 400`이다.
성공확률은 0→1, 차이는 +100pp, 최종 잔액 차이 +2400원·부족액 차이 -2000원이다.
Python 환경 필드만 실행 환경에 따라 달라지는 **실제 전체 payload**는 다음과 같다.

```json
{
  "contract_version": "retirement-analysis-v1",
  "mode": "what_if",
  "mapping_version": "retirement-input-v1",
  "units": {
    "amount": "KRW", "monthly_amount": "KRW/month", "probability": "fraction",
    "probability_delta": "pp", "annual_rate": "fraction", "age": "years"
  },
  "notices": [
    {"code": "partial_financial_scope", "field_name": "saved_data"},
    {"code": "single_portfolio_assumption", "field_name": "financial_assets"},
    {"code": "pre_retirement_cashflow_not_recalculated", "field_name": "financial_assets"},
    {"code": "confirmed_empty_pensions", "field_name": "pension_assets"}
  ],
  "baseline_profile": {
    "current_age": 60, "retirement_age": 60, "end_age": 60,
    "liquid_assets": 10000, "monthly_savings": 0,
    "retirement_expenses": [{"from_age": 60, "to_age": 60, "monthly_amount": 1000}],
    "pension_incomes": []
  },
  "metadata": {
    "current_age": 60, "end_age": 60, "num_paths": 2,
    "assumptions": {
      "annual_return_mean": 0.0, "annual_return_volatility": 0.0,
      "annual_inflation_rate": 0.0, "num_paths": 2, "seed": 7
    },
    "effective_seed": null, "path_source": "generated", "return_model": "constant",
    "rng_algorithm": null, "mc_model_version": "retirement-mc-v1",
    "what_if_model_version": "retirement-what-if-v1",
    "python_version": "3.13.15", "python_implementation": "CPython"
  },
  "baseline": {
    "num_paths": 2, "success_count": 0, "depletion_count": 2, "never_depleted_count": 0,
    "success_probability": 0.0, "depletion_probability": 1.0,
    "asset_percentiles": [{"age": 60, "balance": {"p10": -2000.0, "p50": -2000.0, "p90": -2000.0}}],
    "first_depletion_counts": [{"age": 60, "count": 2}],
    "final_balance_percentiles": {"p10": -2000.0, "p50": -2000.0, "p90": -2000.0},
    "final_shortfall_percentiles": {"p10": 2000.0, "p50": 2000.0, "p90": 2000.0},
    "execution": {"path_source": "provided", "effective_seed": null}
  },
  "scenarios": [{
    "scenario_id": "lower_expenses",
    "changes": {
      "monthly_savings": null, "retirement_age": null,
      "retirement_expenses": [{"from_age": 60, "to_age": 60, "monthly_amount": 800}]
    },
    "profile": {
      "current_age": 60, "retirement_age": 60, "end_age": 60,
      "liquid_assets": 10000, "monthly_savings": 0,
      "retirement_expenses": [{"from_age": 60, "to_age": 60, "monthly_amount": 800}],
      "pension_incomes": []
    },
    "result": {
      "num_paths": 2, "success_count": 2, "depletion_count": 0, "never_depleted_count": 2,
      "success_probability": 1.0, "depletion_probability": 0.0,
      "asset_percentiles": [{"age": 60, "balance": {"p10": 400.0, "p50": 400.0, "p90": 400.0}}],
      "first_depletion_counts": [{"age": 60, "count": 0}],
      "final_balance_percentiles": {"p10": 400.0, "p50": 400.0, "p90": 400.0},
      "final_shortfall_percentiles": {"p10": 0.0, "p50": 0.0, "p90": 0.0},
      "execution": {"path_source": "provided", "effective_seed": null}
    },
    "delta": {
      "success_count_delta": 2, "depletion_count_delta": -2, "never_depleted_count_delta": 2,
      "success_probability_delta_pp": 100.0, "depletion_probability_delta_pp": -100.0,
      "asset_deltas": [{"age": 60, "balance": {"p10": 2400.0, "p50": 2400.0, "p90": 2400.0}}],
      "first_depletion_deltas": [{"age": 60, "count": -2}],
      "final_balance_delta": {"p10": 2400.0, "p50": 2400.0, "p90": 2400.0},
      "final_shortfall_delta": {"p10": -2000.0, "p50": -2000.0, "p90": -2000.0}
    }
  }]
}
```

같은 입력에서 `scenarios=[]`로 호출하면 `mode="baseline"`, `scenarios=[]`,
`metadata.what_if_model_version=null`이다. 기준 계산 수치는 같지만
`baseline.execution.path_source`는 `generated`다. 위 필드명은 기계 계약이며
화면용 한국어 라벨이나 준비율을 이 payload에 추가하지 않는다.

## 8. 구현·검증 순서와 완료 상태

| 브랜치 작업 | 완료 기준 | 권장 커밋 |
|---|---|---|
| 0 — 완료 | 최신 master fast-forward·기존 테스트·새 브랜치 | 별도 커밋 없음, 기준은 이번 문서에 기록 |
| 1 — 완료 | 호출·입력/결과/오류·단위/범위 규격과 검산 | `docs: 은퇴 시뮬레이션 통합 호출 규격 정의` |
| 2 — 완료 | 변경 dict 매퍼·구조/값/ID/없음·원본 불변 테스트 | `feat: 은퇴 What-if 변경 입력 어댑터 구현` |
| 3 — 완료 | 불변 결과 묶음·실행 오류·MC/비교 실행·공통 경로/전체 실패 테스트 | `feat: 은퇴 시뮬레이션 통합 실행 구현` |
| 4 — 완료 | 전체 출력·오류 dict 매핑, property/숫자/메타데이터/격리·JSON round-trip 검증 | `feat: 은퇴 시뮬레이션 출력 변환 구현` |
| 5 — 완료 | 합성 경로/기간/변경안별 시간·메모리 측정과 초기 연동 후보 한도 근거 | `perf: 은퇴 시뮬레이션 실행 성능 검증` |
| 6 — 완료 | 통합 CLI·사용 예시·확대 회귀·BE 인수인계 | `feat: 은퇴 통합 분석 샘플 추가` |

각 구현 단위는 테스트·관련 문서 갱신·검증 후 커밋한다. 푸시·MR 생성·병합은 별도 요청 시 진행한다.
출력 변경이 필요하면 이 문서와 예시·테스트를 함께 수정한다. 배포 후 호환성을 깨는 변경은 계약
버전을 올리고 소비자와 조율해야 하며 계산 모델/매핑/가정 버전을 계약 버전 하나로 대체하지 않는다.

2~6번 테스트에는 0/1/10/11개 변경안, 생략/null/0/빈 일정, 조기/과거 은퇴, 무변경·은퇴 후 적립,
알 수 없는 키/ID·큰 금액/시드·NaN/Infinity, 속성 누락·환경/시드 출처·실패 시 전체 폐기,
호출 순서/공통 난수·원본과 출력 dict 격리·비밀 입력 비노출을 포함한다. 손계산과 위 예시를
기대값으로 삼되 샘플 수치를 코드에 상수로 넣어 계산 결과를 대신하지 않는다.

다음은 브랜치 최종 변경 검토·최신 master 충돌 확인이다. 서비스 연결 담당자는
[BE 인수인계](be-handoff.md)의 공개 호출 예시·입력 책임·미완료 체크리스트를 참고한다.

현재 엔진의 100,000개 경로·10개 변경안 상한은 운영 보장이 아니다. 5번의
[성능 기록](analysis-performance.md)은 작은 사례부터 측정한 14개 합성 결과와 초기 연동 후보 한도다.
API 동시성·작업 취소·동기/비동기·재시도는 BE 환경 재측정 후 연결 전에 정한다.
측정 수치는 운영 기본 가정·응답시간 약속이나 이 호출 계약의 입력 상한 변경이 아니다.

## 9. BE 연결 전에 남는 결정

- 단일 소스 패키징·BE 이미지 포함·호출 담당, HTTP URL·상태/envelope·인증·상담 소유권.
- 동일 시점 snapshot·기준 식별자/만료·실행 시각·보고서와 계산 기준의 일치.
- 금융자산/연금 없음 확인 근거, 미지원 부채·부동산·일회성 목표가 있는 부분 분석의 허용/보류.
- 평균/변동성/물가의 실제 출처·설정 버전·갱신 정책. 외부 금융 API 도입은 필수가 아니며 미결정.
- 실제 준비율의 정의·원천·표시 정책, 변경 후 준비율의 별도 가상 재계산 여부.
- 운영 자원·timeout·동시성·중복 요청, FE의 버튼·조건 입력·차트·경고 UX.

이 항목은 [서비스 연결 설계의 D1~D7](service-integration-design.md)의 확인 사항이다.
문서 작성이나 합성 검증만으로 다른 영역의 승인/합의가 되지는 않는다. 실제 서비스 연결 완료와
이 브랜치의 독립 모듈 완료를 구분하며 다른 팀원의 공통 코드를 임의 수정하지 않는다.

## 10. 0·1번 작업의 검증 기록

2026-09-09, Miniforge `retirewise-ai` / CPython 3.13.15, `debddd4`의 과거 검증 기록이다.
후속 2번의 구현·검증 결과는 [변경 입력 가이드](what-if-adapter.md)에 별도로 기록한다.

- 작업 시작은 깨끗한 `feat/ai-retirement-input-adapter`의 `7b5cb16`이었다.
- fetch 후 MR !45 병합을 포함한 `origin/master=09554bd` 확인. 기존 로컬 master `cbc18d8`을
  fast-forward하고 그 기준에서 `feat/ai-retirement-analysis` 생성. rebase·강제 갱신·브랜치 삭제 없음.
- master의 다른 팀 변경은 이미 병합된 이력으로 가져왔을 뿐 이번 기능 변경에 포함하지 않는다.
  시뮬레이션 모듈은 이전 기능 HEAD `7b5cb16`과 byte 단위 동일함을 분기 전에 확인했다.
- 최신 master 기준과 문서 작성 후 모두 **404개 테스트**가 일반·`-S` 실행에서 통과했다.
  이 숫자는 신규 통합 기능 테스트가 아니다.
- 당시 문서의 신규 함수/오류/결과 모델은 export에 없었으며 실제 구현·서비스 테스트를 수행했다고 표시하지 않았다.
- JSON 예시 3개 파싱·엄격한 JSON round-trip·출력 수치 범위 확인. 기존 입력/What-if 모델과 엔진으로
  예정 전체 payload의 필드·파생 property·안내·실행 메타데이터를 대조했고 손계산 -2000→400원·+100pp와 일치했다.
  이 검증용 일회성 변환은 제품용 통합 함수/출력 어댑터 구현이 아니다.
- 기준 MC와 What-if 배치의 기준 수치는 같고 하위 실행 출처는 다름을 확인했다. 없음 확인을 제거한
  입력의 기존 매핑 오류도 오류 예시의 reason/경로와 일치했으며 원본은 유지됐다.
- 모듈 로컬 Markdown 링크 **208개**와 그중 anchor **5개**, 변경 문서의 제목/코드 블록/공백을 확인했다.
- 런타임·테스트를 포함한 기존 Python **29개 파일**은 모두 `09554bd`와 byte 단위 동일하고 구문 검사도 통과했다.
  변경은 모듈 내부 Markdown **4개 파일**뿐이다.

저장소 루트에서 기존 모듈 검증을 재실행한다.

```bash
conda run -n retirewise-ai python -B -m unittest discover -s ai-pjt/retirement_simulation/tests -q
conda run -n retirewise-ai python -S -B -m unittest discover -s ai-pjt/retirement_simulation/tests -q
```
