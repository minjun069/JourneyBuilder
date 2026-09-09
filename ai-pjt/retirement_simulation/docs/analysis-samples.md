# 통합 분석 샘플 실행·검증

작성일: 2026-09-09. `feat/ai-retirement-analysis`의 6번 작업, 시작 기준 `6504b59`.
[analysis_samples.py](../analysis_samples.py)는 내장 합성 저장 입력을 공개 통합 함수에 전달하고
[통합 호출 규격](analysis-contract.md)의 성공/오류 JSON을 출력하는 개발용 CLI다.
계산 엔진·공개 함수·기존 샘플을 변경하지 않는다. 실제 고객 조회·HTTP API·화면 연결은 아니다.
서비스 연결 담당자는 [BE 인수인계](be-handoff.md)를 함께 읽는다.

## 1. 바로 실행하기

저장소 루트에서 `ai-pjt/`로 이동한 뒤 Miniforge 환경으로 실행한다.
`--no-capture-output`은 conda가 출력을 모아 재출력하지 않도록 하는 옵션이다.
표준 라이브러리만 사용하므로 Django·DB·AI 서버·API key를 준비할 필요가 없다.

```bash
cd ai-pjt
conda run --no-capture-output -n retirewise-ai python -S -B -m retirement_simulation.analysis_samples
```

기본은 **재직 중인 합성 사례 1개·공통 경로 1,000개·시드 42·What-if 3개**다.
출력 길이가 있으므로 먼저 아래 손계산 사례로 전체 JSON 구조를 확인해도 된다.
이하 명령도 같은 `ai-pjt/`에서 실행한다.

```bash
conda run --no-capture-output -n retirewise-ai python -S -B -m retirement_simulation.analysis_samples --case hand_check --num-paths 2 --seed 7 --pretty
conda run --no-capture-output -n retirewise-ai python -S -B -m retirement_simulation.analysis_samples --case saved_working --mode monte-carlo
conda run --no-capture-output -n retirewise-ai python -S -B -m retirement_simulation.analysis_samples --case saved_retired_no_pension --num-paths 10
conda run --no-capture-output -n retirewise-ai python -S -B -m retirement_simulation.analysis_samples --case saved_pension_gap --num-paths 10
```

아래 두 명령은 **오류 응답 검증용으로 종료 코드 1이 정상**이다. 첫 명령은 연금이 없다는
확인 사실을 주지 않아 입력 단계에서 실패하고, 둘째는 계산 후 JSON 안전 숫자 범위를 검사하다 실패한다.
오류를 인위적으로 만드는 mock이 아니라 실제 공개 함수/출력 변환을 통과한다.

```bash
conda run --no-capture-output -n retirewise-ai python -S -B -m retirement_simulation.analysis_samples --case unconfirmed_pension --num-paths 2
conda run --no-capture-output -n retirewise-ai python -S -B -m retirement_simulation.analysis_samples --case hand_check --num-paths 2 --seed 9007199254740992
```

## 2. 옵션·출력 채널·종료 코드

| 옵션 | 기본값·동작 |
|---|---|
| `--case` | `saved_working`. 아래 5개 중 하나만 실행하며 `all`은 지원하지 않음 |
| `--mode` | `what-if`: 기준과 변경안 비교. `monte-carlo`: 변경안을 전달하지 않고 기준 MC만 계산 |
| `--num-paths` | 1~1,000의 정수, 기본 1,000. 이 CLI만의 시연용 보호 상한 |
| `--seed` | 0 이상 정수, 기본 42. 계산 모델의 검증을 재사용하며 최종 JSON에서 큰 숫자는 거절 |
| `--pretty` | JSON 들여쓰기만 변경. 값·필드·정밀도·계약은 동일 |
| `--help` | 실행하지 않고 사용법을 출력 |

옵션 이름 축약·고객 파일·stdin 입력·결과 파일 옵션·`--details`·임의 가정/변경 JSON은 받지 않는다.
`--num-paths 1001` 같은 잘못된 인자를 줄이거나 대체하지 않고 거절한다.
일반 Python 호출의 기존 **최대 100,000경로·10개 변경안**은 변경하지 않았다.
이 상한들은 서비스 기본값·응답시간 보장·동시성 정책이 아니다.

| 상황 | stdout | stderr | 종료 코드 |
|---|---|---|---|
| 정상 분석 | 전체 성공 JSON 1개 | 고정 합성 시연 안내 | 0 |
| 알려진 분석/출력 오류 | 전체 오류 JSON 1개 | 같은 안내, 원천 입력/오류 traceback 없음 | 1 |
| 잘못된 CLI 인자 | 비어 있음 | argparse 사용법·오류 | 2 |
| 도움말 | 일반 도움말 텍스트 | 비어 있음 | 0 |

이 종료 코드는 HTTP 상태 코드가 아니다. 성공/알려진 오류는
`analyze_retirement()` → `analysis_to_dict()` 또는 `analysis_error_to_dict()`로 만들고,
`json.dumps(..., ensure_ascii=False, allow_nan=False)`를 완료한 후 출력한다.
별도의 사례명 envelope·요약 헤더·준비율 필드를 JSON에 덧붙이지 않는다.
후반 변경안의 계산/출력 오류여도 성공 결과 일부와 오류를 섞어 출력하지 않는다.

예상 밖 버그·메모리 부족·사용자 중단·인코딩/출력 스트림 실패는 알려진 분석 오류로 바꾸지 않는다.
이때 표준 성공/오류 JSON이나 위 종료 코드 표의 처리를 보장하지 않는다. stdout 쓰기 자체의
실패까지 원자적으로 복구하는 기능은 없다. CLI 안내문은 stderr이므로 JSON 소비 시 두 채널을 합치지 않는다.

## 3. 합성 사례와 해석

| 사례 | 저장형 기준 입력 | What-if ID·의도 |
|---|---|---|
| `saved_working` | 현재 40·은퇴 60·종료 90세, 금융자산 1.2억 원·월 적립 75만 원, 65세부터 연금·2구간 생활비 | `save_more` 월 적립 100만 원, `retire_earlier` 58세 은퇴+생활비 공백 보완, `lower_expenses` 생활비 인하 |
| `saved_retired_no_pension` | 현재 65·은퇴 60·종료 70세, 금융자산 1억 원·연금 없음 확인. 미지원 부동산/부채/일회성 목표 존재 | `unchanged`, `lower_expenses`, `savings_no_effect` 은퇴 후 적립 변경의 무효과 |
| `saved_pension_gap` | 현재 58·은퇴 60·종료 75세, 금융자산 3천만 원·월 적립 80만 원, 65세부터 연금·분석 기간 밖 연금 | `unchanged`, `stop_saving`, `retire_later` 62세 은퇴 |
| `hand_check` | 현재=은퇴=종료 60세, 금융자산 10,000원·월 생활비 1,000원·연금 없음 확인 | `lower_expenses` 월 생활비 800원 |
| `unconfirmed_pension` | `hand_check`와 같지만 연금 없음 확인을 전달하지 않음 | 입력 매핑 오류 확인용. 어떤 모드에서도 비교 계산하지 않음 |

앞의 3개는 [기존 저장형 fixture](../input_samples.py)를 재사용하며, 수익률 평균 0.05·변동성 0.10·
물가 0.02를 쓴다. **시연 설정이지 시장 전망이나 운영 기본값이 아니다.** 뒤의 2개는 세 비율 모두 0이다.
기존 `input_samples`의 재직 사례는 무변경까지 4개 비교를 유지한다. 새 CLI만 ID로 3개 기능 사례를
선택하며 기존 샘플을 수정하거나 입력 매퍼/계산식을 복제하지 않았다.

`build_analysis_case()`는 매번 새 합성 dict/list를 만든다. 반환 dataclass는 frozen이지만 내부
컨테이너까지 불변인 것은 아니며, 고객 요청 검증 모델이나 BE용 공개 인터페이스가 아니다.
생성만으로 계산·매핑·난수 생성을 하지 않는다. 표준 공개 함수는 그 다음 단계에서 실제 입력을 검증한다.

### 손계산 확인

`hand_check --num-paths 2 --seed 7`은 [계약 문서](analysis-contract.md)의 전체 JSON 예시와 일치한다.
한 해의 생활비를 12개월 차감하고 수익률·물가는 0이므로 다음을 직접 검산할 수 있다.

| 지표 | 기준: 월 1,000원 | 변경: 월 800원 | 변경−기준 |
|---|---|---|---|
| 최종 계산 잔액 p50 | −2,000원 | 400원 | +2,400원 |
| 최종 미충당 부족액 p50 | 2,000원 | 0원 | −2,000원 |
| 성공확률 | 0 | 1 | +100pp |

이 사례의 p10/p50/p90은 같다. 음수 잔액은 부족액을 누적하는 계산 결과이며 실제 대출 잔액이 아니다.
성공확률은 0~1 비율, 비교 차이는 이미 pp다. 기존 준비율과 별도 지표이며 0%·100%도 미래 보장이 아니다.
자세한 단위·분위수·실행 출처는 [출력 변환 가이드](analysis-output.md)를 따른다.
매핑 안내와 모델/매핑 버전, 가정, 기준/변경 프로필, 전체 기간별 결과를 모두 유지한다.

`unconfirmed_pension`은 `input_mapping_failed` / `input` / `pension_assets` /
`empty_confirmation_required` 오류다. 큰 시드 사례는 `unsafe_json_number` / `output` /
`metadata.assumptions.seed`이며 실제 숫자를 오류 메시지에 넣지 않는다.
실제 고객 데이터의 빈 연금 목록에 샘플의 없음 확인을 복사해서 검증을 우회해서는 안 된다.

## 4. 검증 범위

[CLI 테스트](../tests/test_analysis_samples.py)는 fixture 재사용·원본 격리·기본 설정·양쪽 모드·
전체 계약 예시·출력 채널/종료 코드·공통 경로 1회 생성·입력/후반 출력 실패·예상 밖 예외 전파·
파일/네트워크 미사용·`-S` 실제 하위 프로세스 실행을 검사한다.
[통합 회귀 테스트](../tests/test_analysis_integration.py)는 기존 엔진과의 독립 대조, 0/1/10/11개 변경안,
복합 조건·정렬되지 않은 전체 생활비 교체·null/0/빈 일정, 103년 전체 기간·CLI 상한 밖의 공개 호출,
없음 확인·실제 단계별 오류·늦은 실패 후 재실행·원본/전역 난수/출력 격리를 검사한다.

저장소 루트에서 실행한다.

```bash
conda run -n retirewise-ai python -S -B -m unittest discover -s ai-pjt/retirement_simulation/tests -p 'test_analysis*.py' -v
conda run -n retirewise-ai python -B -m unittest discover -s ai-pjt/retirement_simulation/tests -q
conda run -n retirewise-ai python -S -B -m unittest discover -s ai-pjt/retirement_simulation/tests -q
```

2026-09-09, Miniforge `retirewise-ai` / CPython 3.13.15 검증 결과:

- 시작 기준 `6504b59`의 기존 **586개** 테스트 통과. 신규 **34개**(CLI 21개·통합 13개) 추가 후
  전체 **620개**가 일반·`-S`·`-S` 역순 실행에서 모두 통과했다.
- 위 Miniforge CLI 명령 **7개**를 실제 실행해 stdout의 전체 JSON과 종료 코드를 확인했다.
  정상 5개·예상 오류 2개이며 기본 1,000경로/3개 변경안도 실제 계산했다.
- 기존 출력/통합 실행/변경 입력 가이드와 새 BE 인수인계의 Python 예시 **4개**를 실행했다.
  새 인수인계 예시에 합성 기준·비교·입력 오류를 전달해 엄격한 JSON 왕복과 원본 불변도 확인했다.
- Python **43개 파일** 구문·로컬 Markdown 링크 **306개**·anchor **5개**·코드 블록·공백 검사 통과.
- 기존 Python **40개 파일 전부**가 `6504b59`와 byte 단위 동일하다. 새 Python은 CLI와 테스트 2개뿐이다.
  5번 성능 원시 JSON도 동일하며 기록의 런타임 소스 해시가 `6504b59` 당시 소스와 일치함을 재확인했다.
- 변경 **13개 파일 전부**가 `ai-pjt/retirement_simulation/` 내부다. BE·FE·Agent·컨텍스트·
  매뉴얼·공통 설정·의존성·배포·Jira는 변경하지 않았다.

위 결과는 모듈 합성 검증이며 실제 서비스 E2E·시장 가정 검증·배포 성능 확인을 대신하지 않는다.

## 5. 남은 작업

이 CLI의 1,000경로·기본 51년·3개 비교는 [5번 성능 기록](analysis-performance.md)의 초기 연동 시험
후보를 참고한 시연 범위다. 기존 성능 사례와 금융자산 행·생활비 구간·변경 조건이 다르므로
그 기록의 812.312ms를 이 CLI의 측정값이나 응답시간 약속으로 제시하지 않는다.
5번 원시 기록을 새 측정값으로 덮어쓰지 않았으며 엔진의 기간/변경안/경로 한도도 바꾸지 않았다.

다음은 브랜치 최종 변경 검토·최신 master 충돌 확인이며, 푸시·MR·병합은 명시 요청 시에만 진행한다.
그 뒤 서비스 연결에는 패키지 배포·인증된 BE 호출·실제 입력/가정 정책·보고서/What-if 화면·운영/E2E
검증이 필요하다. [BE 인수인계](be-handoff.md)는 그 작업의 체크리스트이지 다른 영역의 변경 승인서가 아니다.
