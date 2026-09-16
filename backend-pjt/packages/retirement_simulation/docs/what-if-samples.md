# What-if 샘플 실행·인수인계 가이드

상태: **브랜치 5번 완료 — 합성 입력의 실제 비교 CLI·출력 검증·사용 문서.**

[`what_if_samples.py`](../what_if_samples.py)는 내장 합성 고객 3종과 명시적 변경안
11개를 실제 `calculate_what_if`에 전달한다. 사례별로 공통 시장 경로를 한 번 생성하고
기준안과 모든 변경안을 같은 경로에 적용한다. 결과를 미리 정한 숫자로 대신하지 않는다.
계산 엔진·공개 모델·BE/FE·Agent·컨텍스트·공용 의존성·실행 설정은 변경하지 않았다.

이 CLI는 **내장 합성 조건 시연용**이다. 고객 파일·DB·폼 입력·외부 API·LLM을 읽거나
실제 고객 보고서·기존 BE 준비율·최적안 추천을 만들지 않는다. CLI의 문자열 출력은
외부 요청·응답 규격이 아니다. 내부 호출 규격은 [비교 계산 가이드](what-if-engine.md)에 있다.

## 1. Miniforge에서 실행

저장소 루트라면 `cd backend-pjt/packages`로 이동한 후 실행한다.
기존 `retirewise-ai` 환경에 추가 패키지·API key·DB 기동이 필요 없다.

```bash
# 3개 사례, 사례마다 1,000개 공통 경로, 시드 42
conda run -n retirewise-ai python -S -B -m retirement_simulation.what_if_samples

# 은퇴 전 사례 하나의 기준·변경·나이별 차이까지 출력
conda run -n retirewise-ai python -S -B -m retirement_simulation.what_if_samples --case working_with_phased_expenses --details

# 작은 경로 수로 빠르게 확인하고 같은 명령으로 재현
conda run -n retirewise-ai python -S -B -m retirement_simulation.what_if_samples --num-paths 200 --seed 42

# 도움말
conda run -n retirewise-ai python -S -B -m retirement_simulation.what_if_samples --help
```

환경을 활성화했다면 `python -m retirement_simulation.what_if_samples`로도 실행할 수 있다.
`-S`는 site-packages 없이 동작하는지 확인하는 선택 옵션이다.

| 옵션 | 기본값 | 의미 |
|---|---|---|
| `--case NAME` | 세 사례 모두 | 선택한 사례와 그 사례의 모든 변경안만 실행 |
| `--num-paths N` | 1,000 | 사례별 공통 경로 수. 정수 1~100,000 |
| `--seed SEED` | 42 | 사례별 로컬 RNG 시드. 0 이상 정수 |
| `--details` | 꺼짐 | 기준·변경안별 나이별 분위수·최초 고갈 개수와 기준 대비 차이 추가 |
| `--help` | — | 계산하지 않고 도움말만 출력 |

축약 옵션, 임의 금액·은퇴 나이·시장 가정·파일 입력 옵션, JSON 출력은 제공하지 않는다.
내장 사례 외의 비교는 내부 Python API로 명시적 모델을 만들어 호출한다.
`--num-paths` 상한은 실행 시간·메모리 보장이 아니다. 큰 설정 전에 작은 경로 수로 확인한다.

## 2. 기준 조건과 변경안

[`build_sample_cases`](../samples.py)의 프로필과
[`build_monte_carlo_cases`](../monte_carlo_samples.py)의 시연 가정을 재사용한다.
`build_what_if_cases(num_paths=..., seed=...)`는 새 불변 `WhatIfSampleCase` tuple을
반환하며, 생성만으로 경로를 만들거나 계산하지 않는다. 이 컨테이너는 외부 요청 검증 모델이 아니다.

| 내장 사례 | 기준 조건 | 변경안 |
|---|---|---|
| `working_with_phased_expenses` | 40~90세, 은퇴 60세, 금융자산 1억원, 월 적립 50만원. 생활비 60~74세 200만원·75~90세 170만원, 연금 65~90세 월 100만원 | 변경 없음, 적립 75만원, 58세 조기 은퇴, 62세 은퇴, 생활비 감소 |
| `already_retired_without_pension` | 68~90세, 이미 65세 은퇴, 금융자산 8천만원, 적립·연금 없음. 생활비 65~90세 월 160만원 | 변경 없음, 생활비 120만원, 은퇴 후 적립 변경의 효과 없음 |
| `pension_start_gap` | 59~90세, 은퇴 60세, 금융자산 6천만원, 월 적립 50만원. 생활비 60~90세 월 200만원, 연금 65~79세 월 110만원 | 변경 없음, 적립 0원, 65세 은퇴 |

구체적인 식별자와 **절대 변경값**은 다음과 같다. 같은 식별자가 다른 사례에 있어도
별개 비교이며, 모든 안은 직전 안이 아닌 해당 사례의 기준안에서 출발한다.

| 사례 | `scenario_id` | 지정한 변경값 |
|---|---|---|
| 모든 사례 | `unchanged` | 없음. 모든 결과 차이가 0 |
| 은퇴 전 | `save_more` | 월 적립 **총액** 750,000원 |
| 은퇴 전 | `retire_earlier` | 은퇴 58세와 전체 생활비 58~74세 2,000,000원·75~90세 1,700,000원 |
| 은퇴 전 | `retire_later` | 은퇴 62세. 기존 생활비 일정 유지 |
| 은퇴 전 | `lower_expenses` | 전체 생활비 60~74세 1,800,000원·75~90세 1,500,000원 |
| 이미 은퇴 | `lower_expenses` | 전체 생활비 65~90세 1,200,000원 |
| 이미 은퇴 | `savings_no_effect` | 월 적립 총액 500,000원. 이미 은퇴했으므로 실제 적립·계산 효과 없음 |
| 연금 공백 | `stop_saving` | 월 적립 총액 **0원**. 미지정과 다름 |
| 연금 공백 | `retire_later` | 은퇴 65세. 연금 일정은 그대로 65~79세 |

생활비는 해당 사례의 **현재 나이 구매력 기준 월 금액**이며 구간 양 끝을 포함한다.
조기 은퇴의 새 연도는 전체 일정에 명시했다. 은퇴를 늦춰 비활성화된 생활비 구간은
입력에서 삭제·이동하지 않는다. 금융자산·현재/종료 나이·연금은 모든 안에서 고정한다.
상세 적용 의미는 [계산 규칙](what-if-rules.md)을 따른다.

공통 가정은 평균 `0.05`, 변동성 `0.10`, 물가 `0.02`다. 단순 연수익률의 산술 평균·
표준편차와 물가율을 소수 비율로 지정한 **시연용 값**이지 시장 추정·추천이나 서비스
기본값이 아니다. `SimulationAssumptions`의 세 가정은 계속 필수 입력이다.
서로 다른 세 사례는 독립 실행이며 같은 시드여도 기간이 달라 공통 경로로 정렬되지 않는다.

## 3. 출력 해석

기본 출력은 사례별 공통 가정·출처 → 기준 입력/결과 → 각 변경값·적용 후 입력/결과·차이 순서다.
적용 후 입력 표시는 공개 프로필 생성기를 다시 사용하며 계산 공식을 복제하지 않는다.
선택한 모든 사례가 계산된 뒤 출력하므로 입력·금액·부분 성공 결과를 먼저 출력하지 않는다.

| 출력 | 의미 |
|---|---|
| `common_market`, `assumptions`, `execution` | 기간·변경안 수·N·설정/실사용 시드·생성 출처·시장 가정·두 모델 버전·RNG·Python 정보 |
| `input`, `retirement_expenses`, `pension_incomes` | 기준 또는 변경 후 입력 전체. 생활비 현재가치와 적립/연금 명목 월 금액 구분 |
| `overrides` | 명시한 절대 변경값만 표시. 생략은 기준 유지, 생활비는 전체 교체 |
| `result_source` | 하위 엔진이 공통 경로를 전달받았으므로 `provided`, 유효 시드/RNG는 `None` |
| `success_probability`, `depletion_probability` | 0~1 원본 비율의 소수 6자리 및 퍼센트 3자리 표시. 경로 개수와 분모 N도 표시 |
| `final_balance`, `final_shortfall` | 각 안의 최종 명목 잔액·부족액 P10/P50/P90 |
| `*_probability_delta_pp` | 변경안 − 기준안의 확률 차이. 부호 있는 **pp**, 상대 증감률이 아님 |
| `*_count_delta` | 성공·고갈·미고갈 개수 차이. 부호 있는 정수 |
| `final_balance_delta`, `final_shortfall_delta` | 각 안의 같은 분위수끼리 뺀 명목 KRW. 금액은 소수 2자리, 차이는 부호 표시 |
| `first_depletion_counts`, `first_depletion_deltas` | 최초 고갈의 나이별 개수·부호 있는 차이. 요약은 0개를 생략하고 없으면 `none` |

`--details`에는 아래 두 표가 추가된다. 기준·각 변경안 결과마다 첫 표를 출력하고,
각 변경안마다 두 번째 차이 표를 출력한다. 전체 세 사례는 **526개 결과 행 + 420개
차이 행 = 946개 나이 행**이다. 한 경로의 원장이나 전체 수익률을 출력하는 옵션이 아니다.

```text
age | balance_p10 | balance_p50 | balance_p90 | first_depletion_count
age | balance_delta_p10 | balance_delta_p50 | balance_delta_p90 | first_depletion_count_delta
```

- 성공은 전 기간 연말 잔액이 한 번도 음수가 아님이다. 잔액 0은 고갈이 아니지만,
  중간 고갈 후 회복해 최종 부족액이 0이어도 실패 이력은 남는다.
- 금액·확률은 **표시만 반올림**한다. 작은 음수가 `-0.00`으로 보여도 고갈 개수를 바꾸지 않는다.
- 분위수 차이는 **경로별 차이의 분위수가 아니고 크기순일 필요가 없다**. 음수 부족액
  차이는 해당 부족액 분위수 감소다. 잔액 차이의 부호만 바꿔 부족액 차이를 만들지 않는다.
- 세 분위수는 전체 표본의 시점별 요약이지 단일 생애 경로·신뢰구간이 아니다.
  확률 0%/100%도 현실의 보장이나 BE 준비율이 아니다. 추천 순위·최적 조건은 계산하지 않는다.

### 실제 기본 실행 예시

2026-09-08, CPython 3.13.15, 기본 가정·`N=1000`·`seed=42`로 CLI를 실행한
첫 사례의 요약이다. 다른 설정에도 이 값이 고정 출력되는 것은 아니다.

| 안 | 성공 개수 / N | 성공확률 | 성공확률 차이 | 최종 잔액 P50 차이(원) |
|---|---:|---:|---:|---:|
| 기준 | 279 / 1,000 | 27.900% | — | — |
| `unchanged` | 279 / 1,000 | 27.900% | +0.000pp | +0.00 |
| `save_more` | 460 / 1,000 | 46.000% | +18.100pp | +288,680,004.91 |
| `retire_earlier` | 167 / 1,000 | 16.700% | -11.200pp | -176,531,603.87 |
| `retire_later` | 417 / 1,000 | 41.700% | +13.800pp | +243,263,764.30 |
| `lower_expenses` | 406 / 1,000 | 40.600% | +12.700pp | +230,260,870.69 |

예를 들어 `save_more`의 출력 일부는 다음과 같다. **합성 조건의 계산 결과이지 추천이 아니다.**

```text
success_probability_delta_pp=+18.100pp, depletion_probability_delta_pp=-18.100pp
final_balance_delta (nominal KRW): p10=+98,471,065.32, p50=+288,680,004.91, p90=+740,863,375.57
final_shortfall_delta (nominal KRW): p10=+0.00, p50=-288,680,004.91, p90=-98,471,065.32
```

이미 은퇴한 사례의 `lower_expenses`는 성공확률 차이 0pp이지만 최종 잔액 P50 차이는
`+141,779,957.79`원이다. 확률 차이가 0이라고 모든 지표가 같다고 해석하지 않는다.
반면 같은 사례의 `savings_no_effect`는 실제 모든 결과 차이가 0이다.

## 4. 오류와 종료 코드

| 종료 코드 | 의미 |
|---|---|
| 0 | 선택한 전체 사례 계산·출력 성공 또는 도움말 |
| 2 | 잘못된 CLI 옵션·사례명·타입·범위. stderr에 사용법/오류 출력, 계산하지 않음 |
| 1 | 내장 모델 검증 또는 What-if 수치 계산 실패. stderr에 오류, stdout 결과 없음 |

오류는 기준/변경안/시장 구분·단계·필드와 알려진 변경안·경로 인덱스·나이를 보존한다.
선택한 두 번째 사례가 실패하면 첫 사례도 출력하지 않고 후속 사례도 실행하지 않는다.
예상된 모델·수치 오류에는 원본 금액·고객 조건·traceback을 붙이지 않는다.
임의 예외·출력 장치 오류까지 같은 코드로 감춘다는 의미는 아니며, 서비스 로깅 정책도 아니다.

## 5. 검증과 완료 범위

신규 [`test_what_if_samples.py`](../tests/test_what_if_samples.py) **24개**를 추가했다.
기존 276개 + 신규 24개 = **전체 300개**가 Python 3.13.15에서 일반·`-S`·역순 실행을
통과했다. What-if 전용은 생성 38개·비교 32개·경계 25개·샘플 24개 = 119개다.

| 테스트 그룹 | 개수 | 주요 확인 |
|---|---:|---|
| `WhatIfSampleInputTests` | 5 | 기존 입력 재사용·가정, 새 불변 모델, 명시적 변경·연금 고정, 타입/범위, 생성 시 계산/RNG 미사용 |
| `WhatIfSampleOutputTests` | 12 | 실제 비교·공통 경로 생성 횟수, 입력/단위/전체 결과 대조, 946행, 재현성·선택, 효과 없음·손계산·회복·극소 음수·분위수 차이 순서 |
| `WhatIfSampleCliTests` | 7 | 옵션·상한 전달, 뒤쪽 사례/실제 수치/모델 오류 무출력, 도움말, 별도 `-S` 프로세스 기본·상세·오류 종료 |

출력 대조는 실제 엔진 결과를 사용한다. 손계산 검사는 0변동성 실제 실행과 테스트 안에서만
수익률 생성기를 통제한 공통 경로로 수행한다. 통제한 경로를 실제 난수 표본이라고 주장하지 않는다.
상한 옵션 검사는 계산을 가로채 전달값만 확인하며 100,000경로의 장기 CLI 부하를 실행하지 않는다.
계산의 독립 검산·자원 상한 검증 범위는 [4번 검증 기록](what-if-validation.md)을 참고한다.

저장소 루트에서:

```bash
conda run -n retirewise-ai python -B -m unittest discover -s backend-pjt/packages/retirement_simulation/tests -q
conda run -n retirewise-ai python -S -B -m unittest discover -s backend-pjt/packages/retirement_simulation/tests -q
conda run -n retirewise-ai python -S -B -m unittest discover -s backend-pjt/packages/retirement_simulation/tests -p test_what_if_samples.py -v
```

기존 `retirement_simulation.samples`와 `retirement_simulation.monte_carlo_samples`의 옵션·입력·가정·계산·
상세 행은 그대로 유지했다. 오래된 What-if 미구현 안내만 새 실행 명령으로 바꾸고
기존 회귀 테스트의 안내 기대값도 갱신했다. 이 CLI 때문에 계산 API를 변경하지 않았다.

이 브랜치의 0~5번 구현·검증·문서화는 완료했다. 다음 개발 범위는 독립 MVP 시연·성능
점검, 실제 입력/결과 adapter·외부 규격, 서비스 호출/표시 연결이다. 최대 동시 자원·
시장 가정 적합성·서비스 배포 완료를 뜻하지 않는다. 푸시·MR 생성·병합은 별도 요청 시에만 한다.
