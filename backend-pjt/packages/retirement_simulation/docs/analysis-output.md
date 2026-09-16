# 은퇴 시뮬레이션 결과·오류 JSON 변환

작성일: 2026-09-09. `feat/ai-retirement-analysis`의 4번 작업, 시작 커밋 `4615527`.
[통합 호출·입출력 규격](analysis-contract.md)의 성공/오류 출력과 숫자 제한을 구현했다.
코드는 [analysis_output.py](../analysis_output.py), 오류 코드·경로 허용 목록은
[analysis_errors.py](../analysis_errors.py)에 있다. 외부 의존성·BE/FE/Agent 연결은 추가하지 않았다.

## 1. 공개 함수와 책임

```text
analysis_to_dict(analysis: RetirementAnalysisResult) -> dict[str, object]
analysis_error_to_dict(error: RetirementAnalysisError) -> dict[str, object]
```

두 함수 모두 패키지 최상위에서 import할 수 있다. 반환값은 **JSON 문자열이 아니라 dict**다.
`analysis_to_dict()`는 이미 끝난 분석을 변환하며 시뮬레이션·경로 생성·DB/파일/네트워크 조회를
하지 않는다. JSON 인코딩과 HTTP 응답 생성은 호출자 책임이다.

- 성공 출력: 계약/매핑 버전, 모드, 단위, 모든 안내, 기준 프로필, 배치 메타데이터, 기준 MC,
  각 변경 정의·적용 프로필·MC 결과·차이를 명시적 필드 목록으로 변환한다.
- `num_paths`, 성공/고갈 개수·확률, 최종 잔액·차이 같은 **property도 포함**한다.
- 빈 비교 목록은 `[]`, 미변경 필드는 세 키의 null, 명시적 0은 0으로 남긴다.
- 요청 순서·변경 정의의 기간 순서·이미 정렬된 적용 프로필·모든 나이의 0개 고갈 행을 유지한다.
- 확률은 fraction, 확률 차이는 이미 계산된 pp다. 다시 100을 곱하지 않는다.
- 매 호출의 모든 dict/list는 새 객체다. 같은 출력 내에서도 수정 가능한 컨테이너를 공유하지 않는다.
- `asdict()`, `__dict__`, `repr`, 원본 snapshot/개인정보를 출력 매핑에 사용하지 않는다.

배치와 하위 실행 출처는 구분한다. 배치는 `generated`, What-if의 하위 기준/변경 결과는
`provided`다. 설정 시드는 `metadata.assumptions.seed`, 실제 사용 시드는 `effective_seed`다.
변동성 0이면 실제 시드/RNG는 null이다. Python 실행 환경은 결과에 기록된 값을 쓰며 변환 시점의
환경으로 다시 만들지 않는다. 시각·실행 ID·준비율·금융 API 출처를 새로 만들어 넣지 않는다.

## 2. 숫자와 모델 정합성

출력 정수의 허용 범위는 **−9,007,199,254,740,991 ~ +9,007,199,254,740,991**이다.
실수도 유한해야 하며 절댓값이 같은 상한 이하여야 한다. 경계값은 포함한다.

- 정수 필드는 실제 Python int만 허용한다. bool·실수·문자열·Decimal을 정수로 바꾸지 않는다.
- 실수 허용 필드는 int/float만 받는다. NaN/±Infinity·범위 초과는 `unsafe_json_number`다.
- 지원하지 않는 자료형은 `invalid_analysis_result`다. 예를 들어 int 나이 필드의 float NaN은
  먼저 자료형 오류가 되며, float 잔액 필드의 NaN은 수치 오류다.
- 금액 반올림·절삭·문자열 변환·null/0 대체는 없다. 기존 float 값과 음수 잔액·차이를 유지한다.
- 이 제한은 JSON 호환성을 위한 출력 규칙이다. 입력/계산의 큰 정수·큰 시드 지원을 줄이지 않는다.
  사용되지 않은 시드·은퇴 후 적립액도 출력에 포함되면 검사한다. 계산 성공이 JSON 성공은 아니다.

지원 대상은 패키지의 **구체적인 불변 모델 클래스와 tuple 컬렉션**이다. dict를 모델처럼 받거나,
임의 subclass의 property/copy 동작을 실행하지 않는다. 정상 공개 생성자가 list를 받는 경우에는
이미 tuple로 변환돼 있어 사용할 수 있다. 강제로 바꾼 list·누락 필드·잘못된 타입·순환 구조는 거절한다.

변환 중 기존 모델의 순수 검증을 얕은 복사본에 적용해 나이/금액/기간·개수·분위수·모드·변경 적용·
메타데이터·저장된 차이를 재확인한다. 이때 적용 프로필과 차이의 **정합성 재확인**은 있지만
현금흐름/MC/What-if 엔진이나 RNG를 다시 호출하지 않는다. 불일치한 원본을 정렬/재계산한 값으로
교체해서 성공시키지 않는다. 수동 조립 모델의 구조 검증이 실제 계산·소유권·입력 완전성의 증명은 아니다.

검증은 다음 순서에서 발견한 첫 오류로 종료한다. 뒤쪽 오류 때문에 앞부분만 반환하지 않는다.

1. 최상위 타입/버전·매핑 버전·안내·기준 프로필.
2. 시나리오 컬렉션 수·모드에 맞는 계산 타입·배치 가정/메타데이터.
3. 기준 MC의 하위 메타데이터·나이별 행·부족액·모델 정합성·파생 property.
4. 요청 순서대로 각 변경 정의·적용 프로필·ID·공유 기준·변경 MC·차이.
5. 전체 묶음 정합성 후 새 dict 반환.

각 필드에서는 구조/자료형 → 출력 숫자 범위 → 기존 도메인 정합성 순서다.
출력에 따로 노출하지 않는 하위 공통 메타데이터의 불일치는 해당 `execution`의 모델 오류로 다룬다.
호출자는 변환 중 모델을 강제로 수정하지 않아야 하며, 일반 로그에 결과나 traceback 지역 변수를
덤프해서는 안 된다. 출력 프로필도 재무정보이므로 실제 서비스 인증·소유권 검사는 별도로 필요하다.

## 3. 실행 가능한 합성 예시

Miniforge 환경을 활성화하고 저장소의 `backend-pjt/packages/`에서 다음 Python 예시를 실행할 수 있다.
0% 가정은 손계산용이지 운영 시장 전망/기본값이 아니다.

```python
import json
from retirement_simulation import (
    RetirementAnalysisError, analyze_retirement, analysis_to_dict, analysis_error_to_dict,
)

saved = {
    "basic_info": {"age": 60, "retirement_age": 60, "end_age": 60},
    "financial_assets": [{"asset_type": "예금/적금", "current_value": 10_000, "monthly_savings": 0}],
    "pension_assets": [],
    "goal_periods": [{"phase": "after", "from_age": 60, "to_age": 60, "items": [{"amount": 1_000}]}],
    "real_estate_assets": [], "liabilities": [], "goal_milestones": [],
}
assumptions = {
    "annual_return_mean": 0, "annual_return_volatility": 0,
    "annual_inflation_rate": 0, "num_paths": 2, "seed": 7,
}
# 실제 서비스는 검증된 원천/없음 확인 사실과 서버 가정을 전달해야 한다.
analysis = analyze_retirement(
    saved, assumptions, confirmed_empty_sections=("pension_assets",),
    scenarios=[{"scenario_id": "lower_expenses", "changes": {"retirement_expenses": [
        {"from_age": 60, "to_age": 60, "monthly_amount": 800},
    ]}}],
)
payload = analysis_to_dict(analysis)
encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False)
assert json.loads(encoded) == payload
assert payload["baseline"]["final_balance_percentiles"]["p50"] == -2_000
assert payload["scenarios"][0]["result"]["final_balance_percentiles"]["p50"] == 400
assert payload["scenarios"][0]["delta"]["success_probability_delta_pp"] == 100

# 큰 시드는 내부 계산에서 허용돼도 출력에서 실패한다. 일부 결과를 먼저 전송하지 않는다.
try:
    internal = analyze_retirement(
        saved, assumptions | {"seed": 10**30}, confirmed_empty_sections=("pension_assets",),
    )
    analysis_to_dict(internal)
except RetirementAnalysisError as error:
    error_payload = analysis_error_to_dict(error)
    assert error_payload["error"]["code"] == "unsafe_json_number"
    assert error_payload["error"]["field_name"] == "metadata.assumptions.seed"
    assert error_payload["error"]["reason_code"] is None
    json.dumps(error_payload, allow_nan=False)
else:
    raise AssertionError("unsafe output seed must fail")
```

기준 모드가 필요하면 `scenarios`를 생략하면 된다. 실제 통합 호출에는
[통합 실행 가이드](analysis-execution.md)의 입력·실행 오류 규칙도 적용된다.
위 예시는 네트워크 응답을 보내거나 고객 데이터를 저장하지 않는다.

## 4. 오류 출력

실행 오류 5개에 다음 2개를 추가했다. 두 코드 모두 stage=`output`, reason_code=null이다.

| code | 고정 message | field_name |
|---|---|---|
| `invalid_analysis_result` | `Analysis result is invalid.` | `analysis` 또는 허용된 출력 객체/필드 경로 |
| `unsafe_json_number` | `Analysis contains an unsafe JSON number.` | 허용된 **숫자 필드** 경로만 |

예: `baseline_profile.liquid_assets`, `metadata.assumptions.seed`,
`scenarios[1].delta.asset_deltas[0].balance.p50`. 알 수 없는 키나 시나리오 ID를 경로에 넣지 않는다.
잘못 수동 생성한 오류 코드/reason/경로는 기존 `ModelValidationError`로 거절한다.

`analysis_error_to_dict()`는 기존/신규 7개 오류를 `{contract_version, error: {...}}`로 변환한다.
code·stage·field_name·reason_code·고정 message만 포함한다. args·추가 속성·cause/context·notes·
traceback·원문 문자열을 직렬화하지 않는다. 수동 변경된 오류도 재검증하며, 임의 예외·잘못된
오류 객체는 `ModelValidationError`로 거절한다. 이를 다시 오류 JSON으로 감싸는 재귀 처리는 없다.
예상하지 못한 프로그램 오류·메모리 부족·중단은 그대로 전파한다. HTTP status·timeout·재시도는 미결정이다.

## 5. 검증과 이 브랜치의 남은 순서

[기본 출력 테스트](../tests/test_analysis_output.py)는 계약 전체 JSON과 실제 엔진 결과의 일치,
property/단위/안내/출처 보존·컨테이너 격리·직렬화 왕복·7개 오류 출력을 검사한다.
[경계 테스트](../tests/test_analysis_output_edges.py)는 큰 정수/시드·비유한 값·float 경계와
누락/변조 모델·잘못된 차이·전체 실패·원본 불변·예상 밖 오류 전파를 검사한다.

저장소 루트에서 실행한다.

```bash
conda run -n retirewise-ai python -S -B -m unittest discover -s backend-pjt/packages/retirement_simulation/tests -p 'test_analysis_output*.py' -v
conda run -n retirewise-ai python -B -m unittest discover -s backend-pjt/packages/retirement_simulation/tests -q
conda run -n retirewise-ai python -S -B -m unittest discover -s backend-pjt/packages/retirement_simulation/tests -q
```

2026-09-09, Miniforge `retirewise-ai` / CPython 3.13.15, 4번(`4bcd307`) 당시 검증 결과:

- 신규 **55개**(기본 출력 26개·경계 29개) 통과. 기존 507개를 포함한 전체 **562개**가
  일반·`-S` 실행과 `-S` 역순 실행에서 모두 통과했다.
- 계약 JSON 예시 3개를 파싱하고 실제 통합 실행→출력 함수의 전체 성공/오류 payload와 대조했다.
  `allow_nan=False` 직렬화·역직렬화, 기준 −2,000원→변경 400원·+100pp 검산도 통과했다.
- 이 가이드·통합 실행 가이드·변경 입력 가이드의 Python 예시 3개를 실제 실행했다.
- Python **38개 파일** 구문, 모듈 로컬 Markdown 링크 **247개**·anchor **5개**·코드 블록을 확인했다.
- 기존 Python 파일 중 export·출력 오류 코드/경로·import 경계 테스트 3개를 제외한 **32개**는
  시작 커밋 `4615527`과 byte 단위 동일하다. 기존 엔진·모델·입력/변경 매퍼·CLI는 수정하지 않았다.
- 변경 **13개 파일**은 모두 `backend-pjt/packages/retirement_simulation/` 내부다. BE·FE·Agent·컨텍스트·
  매뉴얼·공통 설정·의존성·배포·Jira는 변경하지 않았다. 성능/서비스 검증 완료를 뜻하지 않는다.

4번 이후 **5번 합성 성능 검증·6번 통합 샘플/인수인계도 완료**했다.
[측정 방법·원시 결과·후보 한도](analysis-performance.md), [통합 CLI·확대 회귀](analysis-samples.md),
[BE 인수인계](be-handoff.md)에 기록했다. 다음은 브랜치 마무리다.

| 순서 | 작업·완료 기준 | 권장 커밋 |
|---|---|---|
| 5 — 완료 | 합성 입력으로 경로 수·기간·변경안 수별 실행/변환/인코딩 시간·메모리 측정. 후보 한도 근거·환경·한계를 문서화 | `perf: 은퇴 시뮬레이션 실행 성능 검증` |
| 6 — 완료 | 저장 입력→통합 실행→성공/오류 JSON을 사용하는 합성 CLI·사용 예시, 확대 회귀·BE 인수인계·제약 문서 | `feat: 은퇴 통합 분석 샘플 추가` |
| 브랜치 마무리 | 전체 검증·변경 범위/문서 재확인·최신 master 충돌 확인. 필요 시 승인 범위 내 동기화 후 재검증 | 필요한 변경이 있을 때만 별도 커밋 |
| 요청 시 | 이지석 인증 계정 확인 후 푸시·사용자 MR 양식으로 생성. 실제 병합은 별도 요청/사용자 처리 | 원격 작업은 명시 요청 시에만 |

현재 경로 100,000개·변경안 10개 상한은 운영 성능 보장이 아니다. API 동시성·인증·패키지 배포·
보고서/What-if UI는 이 브랜치 밖의 후속 서비스 연결 작업이며 BE·FE 공통 코드는 수정하지 않는다.
