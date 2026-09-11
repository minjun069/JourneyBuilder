# 패키지 설치 경계 검증

작성일: 2026-09-10. `feat/ai-retirement-packaging` 3번, 기준 커밋 `d1d8285` 이후 작업이다.
모듈 내부의 **로컬 설치 인수 검사**이며 BE 설치/배포 가이드나 서비스 통합 완료 기록이 아니다.
BE/FE·Agent·공용 의존성·Docker/Compose·BE 전달 문서는 수정하지 않았다.

위 설명과 1~3·5절은 최초 `0.1.0` 검증 당시의 기록이다. 2026-09-11부터 4절 재실행 명령은
`0.1.2` 설치 runner를 사용하며, 자유 입력 금융자산 제외 보완은 후속 버전 검사에 포함한다.

## 1. 소스 테스트와 설치 테스트의 구분

기존 테스트는 AI 소스 경로를 추가하거나 저장소의 계약 문서·fixture를 읽는다. 이 테스트만
통과해도 wheel에 파일이 빠졌거나 소스 경로로 우회하는 문제가 남을 수 있으므로 검증을 분리했다.

| 검사 | 대상·실행 방식 | 설치 여부 |
|---|---|---|
| 소스 회귀 643개 | 기존 630개 + [설치 runner 방어 검사](../tests/test_installation_runner.py) 13개 | 설치/빌드/네트워크 없이 실행 |
| 설치 인수 14개 | [독립 검사 파일](../tests/installed/check_package.py)을 저장소 밖으로 복사하고 새 venv에서 실행 | 지정한 wheel만 오프라인 일반 설치 |
| 설치 부재·필수 모듈 누락 대조 | 설치 전 import 불가, 설치 후 필수 파일 임시 분리→새 프로세스 실패→복구 후 성공 | 같은 일회성 설치 환경 안에서만 검사 |

마지막 행은 runner의 추가 대조 검사이며 14개의 unittest 개수에 중복 합산하지 않는다.
`tests/installed/`에는 `__init__.py`가 없고 파일 이름도 `test_*.py`가 아니다. 일반 소스 회귀가
설치 검사를 자동 실행하거나, 환경이 없다는 이유로 skip한 것을 성공으로 세지 않는다.

## 2. 실행 방식과 안전 경계

[run_installation_checks.py](../tests/run_installation_checks.py)는 표준 라이브러리만 사용한다.

1. 호출자가 지정한 **정확한 로컬 wheel 파일과 빌드 때 기록한 SHA-256**을 검사한다.
   파일명/배포 이름/버전/Python 범위·무의존성·22개 파일 목록·CRC·RECORD 해시/크기를 확인한다.
   URL·인덱스 패키지 이름·디렉터리·symlink를 설치 대상으로 받지 않는다.
2. 저장소 밖 임시 디렉터리에 wheel을 복사하고 해시를 재검사한다. 같은 디렉터리에 새 venv를
   만들며 기존 Miniforge·BE 환경에 설치/제거/업데이트하지 않는다.
3. `-I -B`로 아직 패키지를 찾을 수 없는지 확인한다. 작업 디렉터리에는 의도적으로 실패하는
   같은 이름의 패키지를 두고, `PYTHONPATH`에는 그 디렉터리와 실제 AI 소스 경로를 넣는다.
   둘 중 하나로 우회하면 정상적인 설치 검증으로 인정하지 않는다.
4. `pip install --no-index --no-deps --no-compile --no-cache-dir`로 검증한 wheel만 일반 설치한다.
   사용자 pip 설정도 격리하고 새 환경에는 기본 pip와 계산 패키지만 존재하는지 확인한다.
5. 독립 검사 파일만 임시 위치로 복사해 `-I -B`로 실행한다. `sys.prefix`·site-packages·
   `__file__`·distribution 위치·`direct_url.json`의 설치 원본 해시를 확인한다.
6. 일회성 설치의 `what_if_adapter.py`를 임시 백업 위치로 이동한 뒤 **새 프로세스**의 import가
   `ModuleNotFoundError`로 실패하는지 확인한다. `finally`에서 복구하고 다시 import 성공을 확인한다.
   모듈 누락을 실제 저장소의 코드로 보완하거나 `sys.path`를 수정하지 않는다.
7. 성공/실패 모두 임시 설치 환경을 정리한다. stdout은 성공 시 검증 JSON, stderr는 단계/테스트 결과다.
   입력/검사 실패는 종료 코드 1, 필수 CLI 인자 누락 등 argparse 오류는 2다. skip·0개 실행은 실패다.

일반 설치는 실행 환경의 site-packages가 필요하므로 `-S`를 설치된 자식 프로세스에 사용하지 않는다.
`-I`는 현재 디렉터리·사용자 site-packages·`PYTHON*` 설정의 import 간섭을 배제하고,
`-B`는 bytecode 캐시 생성을 막는다. [Python 실행 옵션](https://docs.python.org/3.13/using/cmdline.html#cmdoption-I),
[venv 안내](https://docs.python.org/3.13/library/venv.html), [pip 설치 옵션](https://pip.pypa.io/en/stable/cli/pip_install/)을 참고한다.
이 검사는 컨테이너/OS 보안 격리를 구현한 것이 아니며, 신뢰하는 내부 산출물에만 사용한다.
SHA-256 비교는 지정된 파일과의 일치 검사이지 배포자의 신원을 인증하는 기능이 아니다.

## 3. 검증한 기능

- 설치 위치에서 기존 Python 모듈 **18개**와 공개 export **44개**가 정상 해석된다.
  tests/docs·BE/AI 앱·빌드 도구를 런타임에 요구하지 않는다. 패키지 버전은 0.1.0이며
  계산/매핑/JSON 계약 버전은 기존 값으로 유지한다.
- 공개 `analyze_retirement()`·결과/오류 dict 변환을 소스 fixture 없이 만든 합성 dict로 호출한다.
  기준 MC와 What-if의 전체 결과를 엄격한 JSON으로 왕복하고 단위·가정·시드·안내를 확인한다.
- 손계산: 자산 10,000원, 이미 은퇴, 1년, 모든 비율 0%에서 월 생활비 1,000원은 −2,000원,
  800원으로 바꾸면 400원이며 성공확률 차이는 +100pp다. 시장 가정이나 운영 기본값이 아니다.
- 재직 합성 사례에서 월 적립액·은퇴 나이·생활비 변경을 같은 기준에 독립 적용한다.
  입력 원본·전역 RNG 상태를 보존하고 동일 입력/시드의 재실행 결과가 일치한다.
- 변동성 0은 기존 규격대로 `return_model=constant`, `effective_seed=null`, `rng_algorithm=null`이다.
  설정 시드는 `assumptions.seed`에 남는다. 양의 변동성 사례에서는 실제 시드 7과 RNG 출처도 확인한다.
- 실제 없음 미확인 오류와 안전한 JSON 정수 범위를 벗어난 출력 오류를 성공확률 0으로 바꾸지 않는다.
- 통합 CLI의 기준/What-if 출력 전체가 같은 입력의 공개 API 결과와 일치한다. pretty 출력,
  성공 0·알려진 오류 1·인자 오류 2, stdout/stderr 분리와 오류 시 성공 payload 부재를 확인한다.
- 기존 샘플 CLI 5개(`samples`, `monte_carlo_samples`, `what_if_samples`, `input_samples`,
  `analysis_samples`)가 설치된 소스만으로 실행된다. 별도 서버나 고객 DB를 호출하지 않는다.

소스 쪽 방어 검사 13개는 합성 ZIP만 사용한다. 정상 구조, 해시 불일치/형식, 잘못된 파일,
필수 파일 누락, 추가 모듈/tests/환경 파일/경로 탈출, archive symlink, 중복 항목, 런타임 의존성,
중복 메타데이터, 잘못된 RECORD·내부 해시, subprocess/CLI 실패를 검사한다.
Git 메타데이터 없는 소스의 경로 격리와 venv 생성 실패 시 임시 디렉터리 정리도 확인한다.
이 합성 ZIP의 가짜 Python 내용은 설치하거나 실행하지 않는다.

## 4. 재실행

wheel 준비는 [패키징 규격의 빌드 기록](packaging-contract.md)을 따른다. runner는 빌드 도구를
설치하거나 새 wheel을 만들지 않는다. 아래 파일 경로와 해시는 신뢰하는 빌드 산출물 값으로 바꾼다.

```bash
conda run --no-capture-output -n retirewise-ai python -B ai-pjt/retirement_simulation/tests/run_installation_checks.py \
  --wheel /absolute/path/retirewise_retirement_simulation-0.1.2-py3-none-any.whl \
  --sha256 BUILD_RECORDED_SHA256
```

현재 `0.1.2`의 성공 JSON에는 `successful=true`, `tests_run=18`, `skipped=0`, 실제 설치 위치·Python 버전·해시,
설치 전 부재·필수 모듈 누락 거부·복구 성공을 기록한다. 출력의 설치 위치는 이미 제거한 일회성 경로다.
wheel 설치에 네트워크는 필요 없지만, 앞선 별도 빌드 도구 준비에는 네트워크가 필요할 수 있다.

소스 회귀와 runner 방어 검사만 실행할 때:

```bash
conda run --no-capture-output -n retirewise-ai python -S -B -m unittest discover -s ai-pjt/retirement_simulation/tests -q
conda run --no-capture-output -n retirewise-ai python -S -B -m unittest discover -s ai-pjt/retirement_simulation/tests -p test_installation_runner.py -v
```

## 5. 이번 완료 범위와 남은 작업

로컬 CPython 3.13.15/Linux·Miniforge `retirewise-ai`를 기준으로 새 venv를 만들어 검증했다.
빌드 도구는 2번에서 만든 전용 환경(build 1.6.0/setuptools 84.0.0)을 재사용했고 공용 환경은 바꾸지 않았다.
wheel은 계속 파일 22개이며 새 테스트/문서는 sdist에만 포함한다. 기존 계산 Python 18개,
공개 export·가정/계산/JSON 규칙·역사적 성능 기록은 수정하지 않았다.

3번 종료 당시 다음은 4번 전체 회귀·산출물 최종 확인과 모듈 설치 가이드였다.
후속 4번의 재검증과 실제 로컬 설치 명령은 [모듈 설치 가이드](package-installation.md)에 정리했다.
위 3번 당시의 파일 수/도구/테스트 기록은 유지한다. 실제 BE Python 3.13.14 이미지,
인증 API·DB/준비율·FE 화면·동시 요청·운영 성능은 여전히 별도 작업이다.
BE 전달 자료 보완은 사용자 요청대로 브랜치 종료 후 다시 요청받아 진행한다. 푸시/MR도 별도 요청 시에만 한다.

## 6. 0.1.1 기타 금융자산 수정본 검증

2026-09-11, `fix/ai-simulation-other-assets` 3번 작업. 계산 소스 기준은 `84a67b5`이며
패키지·설치 검사를 갱신한 사전 검증 스냅샷은 `7442d45593cb6f9ac52b70c639452b348ad09bb7`이다.
패키지 메타데이터와 설치 도구를 `0.1.1`로 올리고 기존 `0.1.0` 설치본과 구분했다.

### 실행 결과

- 수정 전 소스 **680개**, 수정 후 **682개**가 통과했다. 수정 후 일반·`-S` 실행을 각각 확인했다.
  신규 2개는 runner/pyproject 버전 일치와 옛 버전·파일명 거절 검사이며, 패키징 관련 소스 검사는 총 25개다.
- 전용 build-env에서 **sdist를 만든 뒤 그 sdist로 wheel을 빌드**했다. wheel **22개 파일**,
  sdist **96개 일반 파일**이며 현재 소스/포함 규칙과 일치했다. 옛 0.1.0의 파일 개수 기록은 바꾸지 않는다.
- 두 산출물의 메타데이터·README UTF-8 본문·소스 내용·파일 목록·안전한 경로를 검사했고,
  wheel의 CRC/RECORD와 두 파일의 SHA-256·크기를 빌드 보고서와 대조했다.
- 설치 전 부재 → 저장소 밖 새 venv에 **오프라인 일반 설치** → 독립 검사 **17개, skip 0개 통과**.
  기존 14개와 새 기타 금융자산 검사 3개를 별도 집계하며 소스 테스트 682개에 더하지 않는다.
- 필수 모듈 누락 시 새 프로세스에서 import 실패, 복구 후 성공을 확인했다. 소스 fallback은 허용하지 않는다.

추가 설치 검사의 내용:

1. 금융자산에 기타를 여러 행 추가해도 기준 MC/What-if 응답은 새 notice 외에 동일하다.
   미사용 금액의 null/누락, 원본 배열 위치, 총 월 적립 변경, 입력/RNG 보존, 엄격한 JSON도 확인한다.
2. 기타만 있는 입력은 모델링 자산/적립이 0이고 실제 없음 확인으로 바뀌지 않는다.
   합성 1년 기준/생활비 변경 결과 -12,000원/-9,600원과 모순된 없음 확인의 오류를 확인한다.
3. 기타 다음의 미지원 분류는 `input_mapping_failed` / `unsupported_value`로 전체 실패한다.
   `financial_assets[1].asset_type` 경로와 개인정보 없는 오류 JSON을 보존한다.

### 환경·산출물·후속 경계

CPython **3.13.15**, pip **26.2.1**, build **1.6.0**, setuptools **84.0.0**,
packaging **26.3**, pyproject_hooks **1.2.0**. 빌드 도구는 공식 PyPI에서 작업 전용 임시 venv에만
설치했다. 기존 Miniforge·BE 환경은 갱신하지 않았고 실행 의존성은 여전히 없다.

`84a67b5`의 런타임 Python 18개·공개 export·입출력/가정/계산 규칙은 이번 패키징에서 바꾸지 않았다.
위 사전 검사 후 문서까지 확정한 **최종 커밋을 다시 추출해 빌드·설치 검증**하는 절차를 따른다.
최종 전달본의 소스 커밋/트리, 도구 버전, 파일 목록·SHA-256·설치 결과는 압축 파일 바깥의
별도 빌드·검증 보고서에 기록한다. 문서 보완 전의 사전 산출물을 최종 파일로 취급하지 않는다.

보관·재실행은 [설치 가이드](package-installation.md)를 따른다. 실제 BE 이미지의 설치/API·
원본 snapshot·가정·notice 전달 확인은 BE 연결 인수이며, 확인 전까지 BE의 기타 사전 차단을 유지한다.
패키지·보고서의 공개 게시, 푸시/MR, BE/FE·CI 변경은 이 단계에 포함하지 않는다.

## 7. 0.1.2 비모델링 분류 확장 설치 검증

2026-09-11, `fix/ai-simulation-unmodeled-assets` 3번. 시작 master는 `c4b0327`,
입력 구현 커밋은 `eb693e1`이다. [변경 규칙](unmodeled-financial-assets.md)과 일치하도록
정적 버전·runner·설치 검사·현재 설치 명령을 0.1.2로 갱신했다. 과거 버전의 기록/산출물은 보존했다.

### 모듈 검증 결과

| 검사 | 결과 |
|---|---|
| 소스 전체 회귀 | **685개 통과**, 일반 실행과 `-S` 실행. AI CI와 같은 `-s retirement_simulation/tests -t .` 발견 방식도 확인 |
| 패키징 설정·runner 방어 | **25개 통과**. 전체 685개에 포함되며 별도 합산하지 않음 |
| 빌드 | 저장소 밖 추적 소스 사본에서 sdist 생성 후 해당 sdist로 wheel 빌드 |
| 파일 구성 | wheel **22개**(런타임 18개+메타데이터 4개), sdist **97개 일반 파일** |
| 내용·무결성 | 원천/포함 규칙과 파일·byte 내용 일치, 메타데이터/README·안전한 경로·CRC/RECORD·SHA-256/크기 대조 |
| 별도 설치 인수 | 새 venv에 오프라인 일반 설치, 공개 API/CLI **18개 통과**, skip 0 |
| 설치 격리 | 설치 전 import 부재, site-packages 출처, 소스 fallback 차단, 필수 모듈 누락 실패/복구 성공 |

설치 검사 18개는 소스 685개와 별개다. 기존 17개의 분류 검사를 확장하고 5종/ID 검증 1개를 추가했다.

- 기타·전세보증금·회원권·오타·자유 문자열을 포함한 혼합/모두 제외 입력에서 자산·적립 제외,
  원본 행 notice, 없음 확인과의 구분, 원본·RNG 보존을 설치된 공개 함수로 확인했다.
- 기준/What-if·월 적립 총액 변경, 양수 변동성·고정 seed에서 제외 안내 외의 전체 결과 동일성을 확인했다.
- 필드 누락·null/빈/공백·숫자/bool/배열/객체와 뒤쪽 지원 행의 금액 오류는 안전한 오류 JSON으로 보존했다.
- `0.1.0`·`0.1.1` 파일명과 내부 버전은 임시 설치 환경을 만들기 전에 거절하는 방어 검사를 유지했다.
- 계산 Python 18개는 `eb693e1`과 동일하다. 패키징 때문에 계산·가정·공개 export·JSON을 바꾸지 않았다.

### BE 호환성 결과 — 실패 1건 유지

별도 임시 venv에 0.1.2 wheel을 실제 설치하고 BE의 `test_simulation_package.py`와
`test_simulation_adapter.py`를 수정 없이 실행했다. **45개 중 44개 통과, 1개 실패, error/skip 0**이다.
패키지/이미지 계약 10개는 모두 통과하여 버전 비종속 설치 계약이 유지됨을 확인했다.
실제 Docker 이미지를 빌드하거나 서비스에 설치했다는 뜻은 아니다.

실패는 `test_every_rejected_case_is_also_rejected_by_the_module`의 `free_text_asset_type` 사례다.
`전세보증금`에 모듈 오류를 기대하지만 0.1.2는 요청대로 제외 안내와 부분 분석을 반환한다.
소스 직접 대조(어댑터 35개 중 1개 실패)와 실제 설치본 대조 모두 같은 충돌을 보였다.
BE 담당자가 영구 입력 오류와 화면 안내 전 임시 차단 테스트를 분리해야 한다.
기존 테스트를 skip하거나 BE 코드를 수정하지 않았으며 **전체 CI 통과/즉시 병합 가능으로 표시하지 않는다**.

### 환경·재빌드·후속 경계

CPython **3.13.15**, 기존 전용 build-env의 pip **26.2.1**, build **1.6.0**,
setuptools **84.0.0**, packaging **26.3**, pyproject_hooks **1.2.0**을 재사용했다.
이번 작업은 빌드 도구 다운로드나 공용 Miniforge/BE 환경 변경 없이 진행했고 런타임 의존성도 추가하지 않았다.

커밋 전 추적 작업 파일의 스냅샷으로 사전 검증했다. 문서까지 확정한 최종 커밋은 `git archive`로
다시 추출해 빌드/파일/설치 검사를 반복하고, 최종 소스 커밋·트리·해시·설치 결과를 압축 파일 바깥의
`build-report.json`·`verification-report.json`·`be-compatibility.json`·`SHA256SUMS`에 기록한다.
보관 위치는 [설치 가이드](package-installation.md)의 Git 제외 경로 `dist/0.1.2/<source-commit>/`다.
사전 검증 파일의 해시를 최종 전달본 해시로 사용하거나 과거 산출물을 덮어쓰지 않는다.

BE/FE·Agent·CI·Docker·공통 정책·requirements는 수정하지 않았다. 실제 HTTP/DB·화면 안내·
운영 부하·배포는 미검증이다. **사용자가 제외 사실을 확인할 수 있는 화면과 묶어서 BE 임시 차단을 해제**하며,
모듈 설치 검증만으로 차단을 해제하지 않는다. 푸시/MR·병합·외부 전달은 별도 요청 범위다.
