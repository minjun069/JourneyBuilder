# 은퇴 시뮬레이션 패키지 설치 가이드

작성일: 2026-09-10. `feat/ai-retirement-packaging` 4번 작업.
모듈 개발자가 **로컬에서 빌드·설치·호출을 재현하는 방법**이다. BE Dockerfile·의존성 잠금·
인증 API·FE 연결 방법을 확정하는 문서는 아니다. 후속 사용자 요청으로 보완한
[BE 전달 자료](be-handoff.md)와 [FE 안내](fe-handoff.md), [확정 정책](simulation-policy.md)을 함께 참고한다.

2026-09-11 갱신: 현재 명령은 [비모델링 금융자산 분류 확장](unmodeled-financial-assets.md)이 포함된 `0.1.2`용이다.
7절의 `0.1.0`·8절의 `0.1.1` 기록은 당시 결과이며 새 버전의 설치 검증과 구분한다.

## 1. 먼저 구분할 것

| 항목 | 현재 규격 |
|---|---|
| 설치용 배포 이름/버전 | `retirewise-retirement-simulation` / `0.1.2` |
| Python import | `retirement_simulation` — 하이픈이 있는 배포 이름을 import하지 않음 |
| 지원 메타데이터 | Python `>=3.13,<3.14`. 이번 실제 검증은 Linux/WSL의 CPython 3.13.15 |
| 런타임 의존성 | 없음. AI/BE requirements·API key·DB·서버 기동 불필요 |
| wheel / sdist | `.whl`은 설치용, `.tar.gz`는 빌드 설정·테스트·문서를 포함한 재빌드용 소스 |
| 버전 확인 | `importlib.metadata.version("retirewise-retirement-simulation")`. `__version__` 속성은 추가하지 않음 |

계산/JSON 계약 버전은 패키지 버전과 별개다. 버전 이름이나 `py3-none-any` 태그만으로
모든 Python/OS·BE 이미지에서 검증됐다고 해석하지 않는다. 상세 규격은 [패키징 규격](packaging-contract.md)이다.
소스 테스트는 저장소에서, 설치 검사는 저장소 밖 새 환경에서 실행한다.

아래는 **Bash에서 같은 터미널 세션으로 순서대로** 실행하는 예다. 경로는 모두 인용하고,
어느 단계든 실패하면 다음 단계로 넘어가지 않는다. 이미 받은 wheel이 있다면 3절부터 시작한다.

## 2. 커밋된 소스에서 빌드하기

저장소 루트에서 Miniforge 환경을 활성화한다. `base`나 BE 환경에 빌드 도구를 설치하지 않는다.
`git status`에 모듈 변경이 보이면 먼저 검토한다. 아래 `git archive`에는 **커밋되지 않은 변경이 들어가지 않는다**.

```bash
conda activate retirewise-ai
python -I -B -c 'import sys; print(sys.executable, sys.version); assert sys.version_info[:2] == (3, 13)'
git status --short -- backend-pjt/packages/retirement_simulation
simulation_source_commit="$(git rev-parse HEAD)"
git show --no-patch --format='%H %s' "$simulation_source_commit"
```

확인한 커밋의 모듈만 저장소 밖 작업 디렉터리로 추출한다. 미추적 파일·다른 앱 전체를 복사하지 않는다.
이 사본은 빌드용이며 제품 소스의 별도 복제본으로 관리하지 않는다.

```bash
simulation_workdir="$(mktemp -d /tmp/retirement-package.XXXXXXXX)"
mkdir "$simulation_workdir/source"
git archive --format=tar --output="$simulation_workdir/source.tar" "${simulation_source_commit}:backend-pjt/packages/retirement_simulation"
tar --extract --file="$simulation_workdir/source.tar" --directory="$simulation_workdir/source" --no-same-owner
```

빌드 도구는 **전용 venv에만** 준비한다. 다음 다운로드 단계에는 네트워크가 필요하다.
명시한 도구 버전은 이번 검증 기준이지 항상 최신 버전을 자동 선택하라는 뜻이 아니다.

```bash
python -I -B -m venv "$simulation_workdir/build-env"
PIP_CONFIG_FILE=/dev/null "$simulation_workdir/build-env/bin/python" -I -B -m pip --isolated --disable-pip-version-check install --no-cache-dir --index-url https://pypi.org/simple "build==1.6.0" "setuptools==84.0.0" "packaging==26.3" "pyproject_hooks==1.2.0"
```

준비된 전용 환경에서 sdist를 만들고 **그 sdist로 wheel을 빌드**한다. 해시/크기 보고서는
`dist/` 밖에 둔다. 빌드 도구가 이미 있으므로 이 빌드 단계는 도구를 다운로드하지 않는다.

```bash
"$simulation_workdir/build-env/bin/python" -I -B -m build --no-isolation \
  --outdir "$simulation_workdir/dist" --report "$simulation_workdir/build-report.json" \
  "$simulation_workdir/source"
simulation_wheel="$simulation_workdir/dist/retirewise_retirement_simulation-0.1.2-py3-none-any.whl"
sha256sum "$simulation_wheel" "$simulation_workdir/dist/retirewise_retirement_simulation-0.1.2.tar.gz"
```

`--no-isolation`은 **별도로 준비한 build-env를 쓰는 선택**이다. 의존성 검사는 끄지 않는다.
`--wheel`만 지정하면 기본 sdist→wheel 검증 흐름과 달라진다. [build CLI 문서](https://build.pypa.io/en/stable/reference/cli.html)를 참고한다.
깨끗한 소스의 MANIFEST 제외 경고(`no previously-included ...`)와 실제 빌드 실패를 구분한다.
종료 코드 0, 산출물 2개, 이후 설치 검사까지 확인해야 한다.

소스 커밋·Python/도구 버전·`build-report.json`을 같은 빌드 기록으로 보관한다. 압축 시각 등으로
재빌드 해시는 달라질 수 있다. 같은 `0.1.2` 파일명만으로 같은 산출물이라고 판단하지 않는다.
해시를 포함한 보고서를 다시 패키지에 넣는 순환을 피하고, 산출물/venv/보고서는 Git에 커밋하지 않는다.

## 3. wheel을 먼저 자동 검증하기

저장소 루트에서 실행한다. 2절에서 직접 빌드했다면 `simulation_wheel`은 이미 설정되어 있다.
받은 wheel이면 그 파일의 **절대 경로**를 지정한다. 아래 해시 자리에는 신뢰하는 빌드 기록의
64자리 SHA-256을 넣는다. 출처 불명 파일에서 계산한 해시를 그대로 기대값으로 써도 배포자 신원을 검증할 수는 없다.

```bash
simulation_wheel="/absolute/path/retirewise_retirement_simulation-0.1.2-py3-none-any.whl"
simulation_expected_sha256="REPLACE_WITH_BUILD_RECORDED_SHA256"
python -B backend-pjt/packages/retirement_simulation/tests/run_installation_checks.py \
  --wheel "$simulation_wheel" --sha256 "$simulation_expected_sha256"
```

두 자리표시자는 실제 값으로 바꾼다. runner는 정확한 파일 구성·메타데이터·SHA-256/RECORD를
검사한 뒤 새 venv에 오프라인 일반 설치한다. `successful=true`, `tests_run=18`, `skipped=0`,
설치 전 부재·필수 모듈 누락 거부·복구 성공이 모두 확인되어야 한다.
이 환경은 종료 시 자동 제거되므로 출력된 `package_root`를 이후 실행 경로로 재사용하지 않는다.
검사 내용과 실패 조건은 [설치 검증 기록](installation-validation.md)을 따른다.
기존 17개 검사에 자유 입력·오타 제외와 필수 텍스트 오류를 반영하고 5종·ID 검증 1개를 추가했다.
`0.1.0`·`0.1.1` 파일명이나 내부 버전은 설치 전에 거절하며, 버전 이름만 바꾼 옛 계산 코드도
설치된 동작 검사에서 실패한다.

## 4. 별도 로컬 환경에 설치하고 위치 확인하기

직접 호출을 더 시험하려면 3절을 통과한 **같은 wheel**을 새 환경에 설치한다. 파일이 교체됐거나
출처/해시가 불확실하면 다시 검증한다. 기존 `retirewise-ai`는 Python을 선택하는 데만 사용한다.
아래는 로컬 실험이며 실제 BE 환경의 설치 명령이 아니다.

```bash
simulation_demo_dir="$(mktemp -d /tmp/retirement-package-demo.XXXXXXXX)"
python -I -B -m venv "$simulation_demo_dir/env"
PIP_CONFIG_FILE=/dev/null "$simulation_demo_dir/env/bin/python" -I -B -m pip --isolated --disable-pip-version-check install \
  --no-index --no-deps --no-compile --no-cache-dir "$simulation_wheel"
mkdir "$simulation_demo_dir/working"
cd "$simulation_demo_dir/working"
"$simulation_demo_dir/env/bin/python" -I -B -m pip check
"$simulation_demo_dir/env/bin/python" -I -B -c 'from importlib.metadata import version; from pathlib import Path; import sys; import retirement_simulation as simulation; assert sys.prefix != sys.base_prefix; assert Path(simulation.__file__).resolve().is_relative_to(Path(sys.prefix).resolve()); print(version("retirewise-retirement-simulation")); print(simulation.__file__)'
```

버전은 `0.1.2`, import 위치는 방금 만든 `env/.../site-packages/retirement_simulation/__init__.py`여야 한다.
설치 검증은 [일반 설치](https://pip.pypa.io/en/stable/topics/local-project-installs/) 기준이다.
`pip install -e`, 임의 `PYTHONPATH`, `sys.path.insert()`로 설치 누락을 우회하지 않는다.
정확한 Python의 `-m pip`를 써서 다른 환경의 pip와 섞이지 않게 한다.
wheel 설치 옵션은 [pip install 문서](https://pip.pypa.io/en/stable/cli/pip_install/)를 참고한다.

`-I`는 작업 디렉터리·사용자 site-packages·`PYTHON*`의 경로 간섭을 배제한다. `-B`는 bytecode
생성을 막는다. 설치된 site-packages도 배제하는 `-S`는 여기서는 쓰지 않는다.
[Python 실행 옵션](https://docs.python.org/3.13/using/cmdline.html#cmdoption-I),
[venv 문서](https://docs.python.org/3.13/library/venv.html)를 참고한다. OS 보안 격리를 제공하는 설정은 아니다.

## 5. 설치된 패키지의 CLI·공개 함수 호출

4절의 저장소 밖 작업 위치에서 같은 Python으로 실행한다. CLI는 **내장 합성 입력만 사용하는 데모**다.
사용자 보고서를 받는 HTTP API나 서비스의 subprocess 실행 규격이 아니다.

```bash
"$simulation_demo_dir/env/bin/python" -I -B -m retirement_simulation.analysis_samples --case hand_check --mode monte-carlo --num-paths 2 --seed 7
"$simulation_demo_dir/env/bin/python" -I -B -m retirement_simulation.analysis_samples --case hand_check --mode what-if --num-paths 2 --seed 7 --pretty
```

기준 잔액은 −2,000원, 생활비를 바꾼 잔액은 400원, 성공확률 차이는 +100pp다.
stdout은 계약 JSON 하나, stderr는 합성 데이터 안내다. CLI 종료 코드는 성공 0·알려진 오류 1·인자 오류 2다.
`--case unconfirmed_pension`은 의도한 오류 사례이며 성공확률 0으로 대체하지 않는다.

실제 Python 연결 방식은 CLI 호출이 아니라 최상위 공개 함수의 import다. 다음은 외부 조회 없이
완결되는 합성 예시다. 모든 비율 0%·경로 2개·연금 없음 확인은 손계산용이지 운영 기본값이 아니다.

```bash
"$simulation_demo_dir/env/bin/python" -I -B - <<'PY'
import json
from retirement_simulation import (
    RetirementAnalysisError, analyze_retirement,
    analysis_to_dict, analysis_error_to_dict,
)

saved_data = {
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
scenarios = [{"scenario_id": "lower_expenses", "changes": {"retirement_expenses": [
    {"from_age": 60, "to_age": 60, "monthly_amount": 800},
]}}]

try:
    result = analyze_retirement(
        saved_data, assumptions, scenarios=scenarios,
        confirmed_empty_sections=("pension_assets",),
    )
    payload = analysis_to_dict(result)  # 출력 변환도 같은 오류 처리 범위에 둔다.
except RetirementAnalysisError as error:
    payload = analysis_error_to_dict(error)

print(json.dumps(payload, ensure_ascii=False, allow_nan=False))
PY
```

`scenarios`를 생략하거나 빈 목록으로 주면 기준 MC만 실행한다. What-if는 기준과 변경안을 같은
시장 경로로 비교하므로 별도 기준 MC를 먼저 실행할 필요가 없다. 위 오류 처리는 알려진 모듈 오류만
다루며 HTTP 상태나 예외 전체 처리 정책을 정하지 않는다. dict 변환·JSON 인코딩 비용도 호출에 포함해 봐야 한다.
입력/가정/없음 확인은 [통합 계약](analysis-contract.md), 결과/오류·단위는 [출력 가이드](analysis-output.md)를 따른다.
성공확률 `0~1`, 확률 차이 `pp`는 기존 준비율과 다른 값이며 둘을 같은 지표로 바꾸지 않는다.

## 6. 확인 실패 시

| 증상 | 확인할 내용 |
|---|---|
| Python 버전 거부 | 해당 명령의 `sys.executable`·버전 확인. `--ignore-requires-python`으로 우회하지 않음 |
| `No module named build` / 빌드 의존성 누락 | 공용 환경이 아니라 2절의 전용 build-env·고정 도구 버전 확인 |
| SHA-256/파일 목록/메타데이터 불일치 | 선택한 소스 커밋·파일·빌드 기록 재확인. 기대 해시만 바꿔 통과시키지 않음 |
| 설치 후 `ModuleNotFoundError` | 설치와 실행의 Python이 같은지, `-S`를 잘못 붙이지 않았는지 확인 |
| 저장소의 소스가 import됨 | 일반 설치·저장소 밖 cwd·`-I` 확인. 소스 경로를 덧붙이지 않음 |
| `empty_confirmation_required` 등 분석 오류 | 필드/사유 코드를 확인하고 입력을 바로잡음. 없음 확인을 일괄 허용하지 않음 |
| 새 코드가 산출물에 없음 | `git archive`의 커밋 확인. 설치 파일은 작업 중인 소스의 수정 사항을 자동 반영하지 않음 |

자동 검증 runner의 환경은 스스로 정리된다. 2·4절에서 직접 만든 작업/데모 디렉터리는 남으므로
실험 종료 후 **실제로 생성된 정확한 경로와 내용**을 확인해 정리한다. 가상환경은 옮겨 재사용하지 않고
필요하면 새로 만든다. 공용 환경에서 패키지를 제거하거나 기존 환경을 덮어쓸 필요가 없다.

## 7. 4번 검증 기록과 남은 경계

`fde5cfc` 이후 모듈 내부 Markdown만 추가/수정했다. 소스 회귀 **643개**는 일반·`-S`·역순 실행,
별도 설치 검사 **14개**는 wheel의 일반 설치에서 통과했다. 가이드의 설치 위치·CLI·공개 함수 예시도
저장소 밖 새 환경에서 확인했다. 두 테스트 집계는 별개이며 이 문서로 테스트 개수를 늘리지 않았다.

- wheel **22개 파일**: 기존 Python 18개 + 배포 메타데이터 4개. sdist **87개 파일**:
  3번의 86개에 이 가이드 1개 추가. 파일 목록·원본 내용·이름/버전/무의존성·CRC/RECORD·경로 탈출/링크 부재 확인.
- Linux/CPython **3.13.15**·pip 26.2.1. 빌드 도구는 기존 전용 환경의 build 1.6.0·setuptools 84.0.0·
  packaging 26.3·pyproject_hooks 1.2.0을 사용했다. 기존 Miniforge 환경은 변경하지 않았다.
- 계산 Python·공개 export·입출력/가정/계산 규칙·기존 성능 원시 기록은 그대로다.
  SHA-256은 실행별 빌드 보고서에 남기며 이 문서에 고정하지 않는다.

패키징의 로컬 개발·검증·설치 안내까지 완료한 것이지 서비스 배포 완료가 아니다.
실제 BE Python 3.13.14 이미지·잠금/설치 경로·인증/입력 조회·운영 가정·동시 실행·FE 화면은 미검증이다.
다음 5번은 최종 변경 범위·최신 master 충돌 확인 후 **명시 요청 시** 푸시/MR이다.
BE 전달 설명/문서 보완은 사용자가 브랜치 종료 후 다시 요청할 때 진행한다.

## 8. 0.1.1 수정본 확인과 보관

2026-09-11 갱신. `기타` 금융자산 제외 수정 `84a67b5`를 포함하는 `0.1.1`은 소스 회귀 **682개**와
격리 설치 **17개**를 별도로 검증했다. 상세 버전/내용/제외 규칙·검증 범위는
[설치 경계 검증](installation-validation.md)을 참고한다. 기존 0.1.0 wheel을 이름만 바꿔 사용하지 않는다.

최종 커밋에서 2절의 `git archive`로 다시 빌드하고 3절의 설치 검사를 통과한 파일을 사용한다.
로컬 전달본을 남길 때는 모듈의 Git 제외 경로 `dist/0.1.1/<source-commit>/` 아래에 보관한다.
압축 파일은 그 안의 `artifacts/`, 빌드·검증 보고서는 상위에 두어 보고서가 패키지 입력에 들어가지 않게 한다.
같은 버전이라도 소스 커밋·파일 해시가 다르면 같은 파일로 보지 않는다. 이전 전달본은 덮어쓰지 않는다.

- 설치용: `retirewise_retirement_simulation-0.1.1-py3-none-any.whl`.
- 재빌드용: `retirewise_retirement_simulation-0.1.1.tar.gz`.
- 확인용: 빌드 보고서와 별도 검증 보고서의 소스 커밋/트리·Python/도구 버전·SHA-256·설치 결과.
- 기존 [정책 JSON](simulation-policy.json)은 별도 서버 설정 자료이며 wheel 내부 파일이 아니다.

이 로컬 보관 경로는 Git/MR에 올라가지 않으며 운영 설치 위치나 공개 다운로드 주소가 아니다.
BE 전달 시 동일 파일과 신뢰하는 검증 기록을 함께 전달하거나, 합의한 소스 커밋에서 BE가 직접 재빌드한다.
위는 0.1.1 보관 기록이다. 현재 임시 차단 해제는 아래 0.1.2 절의 **화면 안내 확인**까지 필요하다.

## 9. 0.1.2 수정본 확인과 보관

자유 입력·오타를 포함한 5종 밖 문자열 제외와 필수 텍스트 오류 구분이 포함된 버전이다.
빌드·설치 방식은 위 2~4절을 사용하며 [검증 기록](installation-validation.md)에서 결과와 한계를 확인한다.

- 설치용: `retirewise_retirement_simulation-0.1.2-py3-none-any.whl`.
- 재빌드용: `retirewise_retirement_simulation-0.1.2.tar.gz`.
- 로컬 보관: Git 제외 경로 `dist/0.1.2/<source-commit>/artifacts/`와 상위의 빌드/검증 보고서·`SHA256SUMS`.
- 소스 커밋/트리·버전·해시·설치 결과를 함께 기록하고 이전 버전/커밋 산출물은 덮어쓰지 않는다.

BE는 소스에서 직접 재빌드할 수도 있으므로 wheel을 별도 전달해야 하는지는 BE 설치 흐름에 따른다.
현재 BE의 버전 비종속 wheel 선택/설치 계약은 유지하며 모듈 담당이 Dockerfile을 수정하지 않는다.
BE 테스트의 임시 차단 기대값 조정과 실제 API/화면의 notice 확인은 [변경 안내](unmodeled-financial-assets.md)를
따른다. **패키지 설치만으로 임시 차단을 해제하지 않는다. 사용자에게 제외 사실이 보이는 것이 선행 조건**이다.
