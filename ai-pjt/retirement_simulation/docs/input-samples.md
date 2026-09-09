# 저장 입력 연동 MC·What-if 샘플 가이드

상태: **입력 어댑터 브랜치 4번 샘플·사용 문서·검증 완료.**
내장 합성 저장 입력 → 실제 입력/가정 어댑터 → 기존 계산 엔진 → 터미널 출력까지 실행한다.
고객 파일·DB 조회, BE/API/FE 연결, JSON 응답·운영 설정·배포는 구현하지 않았다.
변경 범위는 `ai-pjt/retirement_simulation/` 안의 샘플·테스트·문서뿐이다.

구현은 [input_samples.py](../input_samples.py), 검증은
[test_input_samples.py](../tests/test_input_samples.py)에 있다. 원천 규격은
[입력 매핑 규칙](input-mapping-rules.md), 공개 변환 함수는
[입력 어댑터 가이드](input-adapter.md)를 참고한다.

## 1. 실행 순서와 옵션

Miniforge의 `retirewise-ai` 환경을 사용한다. 저장소 루트에서 한 번 디렉터리를 옮긴다.

```bash
cd ai-pjt
```

기본 실행은 3개 사례의 **기준 MC와 What-if 총 10개 변경안**이다.
What-if 엔진이 기준 MC도 계산하므로 기준안을 별도로 중복 계산하지 않는다.

```bash
conda run -n retirewise-ai python -S -B -m retirement_simulation.input_samples
```

기준 MC만 실행하거나, 특정 사례·상세 출력·경로 수·시드를 선택한다.

```bash
conda run -n retirewise-ai python -S -B -m retirement_simulation.input_samples --mode monte-carlo
conda run -n retirewise-ai python -S -B -m retirement_simulation.input_samples --case saved_working --details
conda run -n retirewise-ai python -S -B -m retirement_simulation.input_samples --case saved_pension_gap --num-paths 100 --seed 0
conda run -n retirewise-ai python -S -B -m retirement_simulation.input_samples --help
```

| 옵션 | 값·기본 동작 |
|---|---|
| `--mode` | `what-if`(기본) 또는 `monte-carlo`. `both`는 없으며 기본 모드가 기준/비교를 함께 출력 |
| `--case` | 아래 세 식별자 중 하나. 생략하면 모두 실행 |
| `--num-paths` | 사례별 1~100,000개, 기본 1,000개. 운영 성능이 검증된 한도가 아님 |
| `--seed` | 0 이상 정수, 기본 42. 0도 정상 값 |
| `--details` | 모든 나이의 잔액 분위수·최초 고갈 개수 및 What-if 차이 출력 |

금액·수익률을 임의 입력하거나 실제 고객 파일을 읽는 옵션은 없다. `--input`, `--output`,
옵션 이름 축약도 지원하지 않는다. CLI는 표준 출력만 사용하며 결과 파일을 저장하지 않는다.
Python 표준 라이브러리만 필요하고 Django·AI 서버·LLM·DB 기동은 필요 없다.

## 2. 합성 사례와 변경안

원천은 `customer_info["saved_data"]`에 대응하는 **필요 필드 발췌 dict**다. 완전한 BE 응답이나
POST payload가 아니다. 실제 serializer 필드 이름을 사용하지만 실제 저장 데이터는 읽지 않는다.
금액은 원, 생활비·적립·연금은 월액이며 기간 양 끝 나이를 포함한다.

| 사례 | 실제 어댑터 변환 결과 | 기준과 비교하는 변경안 |
|---|---|---|
| `saved_working` | 현재 40/은퇴 60/종료 90세. 금융자산 1.2억·월 적립 75만. 생활비 60~74세 200만, 75~90세 150만. 연금 65~90세 100만 | 무변경; 월 적립 총액 100만; 58세 조기 은퇴+전체 생활비 일정 교체; 월 생활비 180만/130만으로 전체 교체 |
| `saved_retired_no_pension` | 현재 65/은퇴 60/종료 70세. 금융자산 1억·적립 0·생활비 150만. 명시적으로 확인한 연금 없음 | 무변경; 생활비 120만; 월 적립 50만(이미 은퇴했으므로 계산 효과 없음) |
| `saved_pension_gap` | 현재 58/은퇴 60/종료 75세. 금융자산 3천만·월 적립 80만. 생활비 60~64세 180만, 65~75세 130만. 연금 65~74세 80만 | 무변경; 적립 중단(명시적 0); 62세 은퇴 |

첫 사례는 매핑 규격의 합성 JSON 및 기본 어댑터 테스트 fixture와 동일하다. 원금이 아닌
평가액 1억+2천만을 합산하고, 같은 생활비 구간의 150만+50만을 월 총액 200만으로 변환한다.
58세 조기 은퇴 변경은 58~74세 200만·75~90세 150만의 **전체 일정**을 함께 지정한다.

두 번째 사례에는 부동산 3억·임대료·보증금·담보대출, 부채 5천만, 일회성 지출 1천만이
추가로 있지만 계산에는 반영하지 않는다. `real_estate_not_modeled`, `liabilities_not_modeled`,
`milestones_not_modeled`를 출력해 제외 사실을 드러낸다. 결과는 순자산/전체 재무상태 분석이 아니다.
`confirmed_empty_sections=("pension_assets",)`는 이 합성 사례의 명시적 사실이다.
서비스에서 빈 목록이나 기존 BE 완료 상태만 보고 이 사실을 자동 생성해서는 안 된다.
현재 BE 기본정보 쓰기 검증은 과거 은퇴 나이를 허용하지 않지만 내부 모델은 이미 은퇴한 경우도
지원한다. 이 사례가 BE 저장 가능 여부를 증명하거나 기존 검증을 변경하지는 않는다.

세 번째 사례의 `pension_period="10년"`은 65~74세 수령이다. 함께 있는
`payment_period_years=20`은 납입기간이며 수령기간으로 사용하지 않는다. 80세부터 시작하는
별도 연금은 유효성 검증 후 계획 범위 밖으로 제외하고 `pension_outside_horizon`과
원천 경로 `pension_assets[1]`을 표시한다. 은퇴 나이를 바꿔도 연금은 자동 이동하지 않는다.

모든 변경은 직전 변경안이 아닌 **원래 기준 프로필**에 독립적으로 적용한다. 월 적립은 증액분이
아닌 변경 후 총액이다. 원본 snapshot을 수정·저장하거나 AI 컨텍스트에 가정을 반영하지 않는다.

## 3. 실행 경로와 출력 해석

`build_input_cases(num_paths=1000, seed=42)`는 매번 새 dict/list와 시나리오를 만들 뿐,
매핑·계산·난수 생성은 하지 않는다. `InputSampleCase`는 frozen fixture 컨테이너지만 내부
dict까지 불변인 요청 모델은 아니다. 변환 중 원천을 수정하지 않아야 하며 반환된 계산
프로필과 안내는 원본에서 분리된 frozen 모델/tuple이다.

선택된 사례마다 `map_retirement_profile()`과 `map_simulation_assumptions()`를 한 번씩 호출한다.
MC 모드는 `calculate_monte_carlo()`만, 기본 모드는 `calculate_what_if()`를 호출한다.
기본 모드는 사례별 공통 경로를 한 번 생성하고 기준안을 한 번 계산해 모든 변경안에 사용한다.
사례 간에는 서로 다른 기간의 실행이므로 동일한 시장 경로로 정렬된 고객 비교로 해석하지 않는다.
기존 What-if 샘플의 출력/옵션 helper만 재사용하며 계산 엔진이나 기존 CLI 동작은 바꾸지 않았다.

각 사례는 다음 정보를 출력한다.

1. 원천 목록별 행 수·합성 없음 확인, `mapping_version`, **모든 매핑 안내의 코드·경로·설명**.
2. 실제 매핑된 나이·자산·적립·생활비·연금, 변경안의 명시적 조건과 변경 후 프로필.
3. 실행 가정·경로 수·설정/실사용 시드·경로 출처·MC/What-if 모델 버전·RNG·Python 환경.
4. 성공/고갈 확률·경로 수·최초 고갈 나이별 개수, 최종 잔액/부족액 P10·P50·P90.
5. What-if의 기준 대비 확률 차이(pp)와 잔액/부족액 분위수 차이(명목 KRW).

시연 가정은 평균 `0.05`, 변동성 `0.10`, 물가 `0.02`로 **CLI에만 명시**했다. 보정된 시장
추정치·투자 권장값·서비스 기본값이 아니며 어댑터는 여전히 가정 5개를 모두 요구한다.
What-if 자식 결과의 경로 출처는 `provided`, 실사용 시드는 `None`이다. 실제 생성 출처는
배치 메타데이터에 있다. 변동성 0의 검산에서는 난수를 쓰지 않아 배치의 실사용 시드도 `None`이다.

성공은 모든 연말 계산 잔액이 음수가 아닌 경로의 비율이다. 중간 고갈 후 회복해도 실패이며,
잔액 0은 고갈이 아니다. 음수는 미충당 부족액이지 실제 대출/부채 계정이 아니다.
P10/P50/P90은 전체 경로의 분위수이며 한 경로·신뢰구간이 아니다. 분위수 차이는 각 결과의
동일 분위수끼리 뺀 값이지 경로별 차이를 모은 분위수가 아니다. 0%/100%는 보장·추천이나
기존 준비율이 아니며 가정/모델/환경이 바뀌면 결과도 달라질 수 있다.

기본 3개 사례에 `--details`를 쓰면 MC는 **75행**, What-if는 **627행**의 나이별 상세를
출력한다. What-if는 기준/변경 요약 351행과 차이 276행이며 원시 시장 경로나 개별 현금흐름
원장 전체를 출력하는 옵션은 아니다. 표시 반올림은 계산·고갈 판정에 영향을 주지 않는다.

## 4. 기본 실행 검산 예시

2026-09-09, Miniforge `retirewise-ai` / CPython 3.13.15, 기본 가정·1,000개 경로·시드 42에서
실제 CLI로 확인했다. MC 단독 모드와 What-if의 기준 결과는 같다.

| 사례 | 기준 성공 수/확률 | 최종 잔액 P50(명목 원) |
|---|---|---|
| `saved_working` | 579/1,000 = 57.9% | 215,318,873.83 |
| `saved_retired_no_pension` | 544/1,000 = 54.4% | 2,769,635.84 |
| `saved_pension_gap` | 0/1,000 = 0% | -182,032,089.82 |

첫 사례의 `save_more`는 73.9%(기준 대비 **+16.0pp**), `retire_earlier`는 43.8%(-14.1pp),
`lower_expenses`는 73.2%(+15.3pp)다. 두 번째의 생활비 축소는 94.7%(+40.3pp),
이미 은퇴한 사용자의 적립 변경은 기준과 완전히 같다. 세 번째는 모든 변경안의 성공확률이
0%지만 잔액·부족액 분포는 달라진다. 확률 차이 0을 모든 계산 결과가 같다는 뜻으로 보지 않는다.

이는 **합성 사례의 제한된 계산 범위**에 대한 재현 예시다. 특히 두 번째 수치에는 위에서
안내한 부채·부동산·일회성 지출이 포함되지 않는다. 실제 고객의 은퇴 성공확률로 제출하지 않는다.

테스트에서는 별도로 첫 사례의 수익률·변동성·물가를 모두 0으로 설정해 손계산도 확인한다.
기준 최종 잔액 -36,000,000원·최초 고갈 85세, 월 적립 총액 100만으로 변경 후
24,000,000원·성공확률 +100pp다. 이 검산 설정은 위 기본 CLI 시연 가정과 다르다.

## 5. 오류·검증·완료 범위

종료 코드 0은 계산 성공 또는 `--help`, 2는 잘못된 CLI 인자다. 입력 매핑·내부 모델·MC/What-if
계산의 알려진 오류는 코드 1로 종료하고 안전한 오류 위치/설명을 stderr에 출력한다.
선택된 모든 사례의 변환·계산이 끝난 뒤에만 stdout을 출력하므로 뒤 사례가 실패해도
앞 사례만 성공한 것처럼 일부 결과를 남기지 않는다. 잘못된 입력 원문이나 traceback을
계산 오류 메시지에 추가하지 않는다. 이는 HTTP 상태/응답 규격을 정의하는 것이 아니다.

저장소 루트에서 재실행한다.

```bash
conda run -n retirewise-ai python -S -B -m unittest discover -s ai-pjt/retirement_simulation/tests -p test_input_samples.py -v
conda run -n retirewise-ai python -B -m unittest discover -s ai-pjt/retirement_simulation/tests -q
conda run -n retirewise-ai python -S -B -m unittest discover -s ai-pjt/retirement_simulation/tests -q
```

신규 **23개**, 전체 **404개** 테스트가 일반·`-S` 실행에서 통과했다. 신규 검증은 실제 어댑터와
엔진 호출·공통 경로 1회 생성·출력과 결과 일치·모든 상세 행·범위 안내·원본/전역 난수 불변·
무변경/적립 0·실패 시 부분 출력 방지·CLI 옵션/하위 프로세스 실행을 포함한다.
큰 경로 수는 경계 전달을 검증했을 뿐 100,000개 경로의 운영 성능을 보장하지 않는다.

추가 검증 기록:

- 전체 **404개 역순 실행**도 통과했다. 실행 순서에 따른 공유 상태 영향을 점검했다.
- 위 CLI 명령 5개와 기존 어댑터 가이드의 Python 예제를 실제 실행했다.
- 합성 3개 사례의 24개 원천/하위 행이 BE serializer 8개의 선언 필드명에 포함되는지
  소스 AST로 확인했다. Django import·DB 조회·BE 쓰기 검증 실행은 하지 않았다.
- 모듈 로컬 Markdown 링크 **197개**와 그중 anchor **5개**, 전체 Python 구문을 확인했다.
- 기존 런타임 Python **11개 파일**은 시작 커밋 `b457f27`과 byte 단위 동일하다.
  변경은 새 샘플/테스트/가이드와 관련 문서 총 **8개 파일**뿐이다.

기존 현금흐름·MC·What-if CLI는 내부 프로필을 직접 만드는 합성 예제다. 이번 CLI는
**저장형 dict에서 실제 어댑터를 거친다**는 차이가 있으며 어느 쪽도 실제 고객을 조회하지 않는다.
이 단계로 입력 어댑터 브랜치 0~4번의 모듈 구현·검증·문서화를 마친다. 기존 3번 작업의
[381개 검증 기록](input-adapter-validation.md)은 해당 시점의 이력으로 유지한다.

입력 어댑터는 MR !45로 병합됐고, 후속 `feat/ai-retirement-analysis`에서
[통합 호출·입출력 규격](analysis-contract.md)을 문서화하고 What-if 변경 입력과
[통합 실행·불변 결과·실행 오류](analysis-execution.md), [결과/오류 JSON 변환](analysis-output.md)까지
구현했다. 기존 입력 연동 CLI는 수정하지 않았다. 이후 성능 측정·한도 근거, 통합 CLI를 분리해 진행한다.
실제 BE의 일관된 snapshot 조회·인증·없음 확인·부분 분석 허용 정책, 패키지 배포와 보고서 UI는
[서비스 연결 설계](service-integration-design.md)에 따른 별도 승인된 연결 작업이다.
푸시·MR 생성·병합은 요청받았을 때만 진행한다.
