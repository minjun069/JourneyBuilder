# 패키지 설치 경계 검증

작성일: 2026-09-10. `feat/ai-retirement-packaging` 3번, 기준 커밋 `d1d8285` 이후 작업이다.
모듈 내부의 **로컬 설치 인수 검사**이며 BE 설치/배포 가이드나 서비스 통합 완료 기록이 아니다.
BE/FE·Agent·공용 의존성·Docker/Compose·BE 전달 문서는 수정하지 않았다.

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
  --wheel /absolute/path/retirewise_retirement_simulation-0.1.0-py3-none-any.whl \
  --sha256 BUILD_RECORDED_SHA256
```

성공 JSON에는 `successful=true`, `tests_run=14`, `skipped=0`, 실제 설치 위치·Python 버전·해시,
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

다음 4번은 전체 회귀·산출물 최종 확인과 **모듈 설치 가이드** 정리다. 실제 BE Python 3.13.14 이미지,
인증 API·DB/준비율·FE 화면·동시 요청·운영 성능은 여전히 별도 작업이다.
BE 전달 자료 보완은 사용자 요청대로 브랜치 종료 후 다시 요청받아 진행한다. 푸시/MR도 별도 요청 시에만 한다.
