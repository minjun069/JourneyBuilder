# ── 1단계: 은퇴 시뮬레이션 wheel 빌더 (S15P21B109-135) ──────────────────────────
# ai-pjt/retirement_simulation 은 외부 의존성이 없는 순수 계산 패키지다. 계산 API 가
# import 해서 직접 호출하므로 BE 이미지에 정식 설치해야 한다.
#
# 왜 wheel 을 여기서 만드는가: 실행 가능한 wheel 은 Git 에 넣지 않는다는 모듈 방침
# (ai-pjt/retirement_simulation/docs/package-installation.md)과, 러너 체크아웃에서
# docker compose build 로 이미지를 만드는 배포 전제를 동시에 만족하는 지점이 여기뿐이다.
#
# 왜 `pip install <소스 디렉터리>` 가 아닌가: 그 형태는 direct_url.json 에 dir_info 를
# 남기는데 모듈의 인수 검사(tests/installed/check_package.py)가 이를 거부한다.
# 정상 wheel 설치만 허용된 계약이다.
#
# 빌드 도구는 이 스테이지에만 남고 최종 이미지로 넘어가지 않는다.
FROM python:3.13.14-slim-bookworm AS simulation-builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build

# 빌드 백엔드도 런타임과 같은 규율(해시 고정)로 잠근다. 런타임 requirements.lock 과
# 파일을 분리해 그 레이어의 캐시와 --require-hashes 설치를 건드리지 않는다.
# 베이스 이미지에는 pip 만 있고 setuptools 가 없어서 이 단계가 필수다.
COPY backend-pjt/requirements-build.lock ./requirements-build.lock
RUN python -m pip install --no-cache-dir --require-hashes -r requirements-build.lock

# ai-pjt 전체가 아니라 모듈 디렉터리만 가져온다.
COPY ai-pjt/retirement_simulation/ ./retirement_simulation/

# --no-build-isolation: 위에서 해시 검증으로 설치한 setuptools 를 그대로 쓴다
#   (pyproject 의 build-system.requires 와 같은 84.0.0 이어야 한다).
# --no-index --no-deps: 이 단계는 네트워크를 쓰지 않는다. 런타임 의존성은 원래 0개다.
#
# wheel 파일명에 버전을 적지 않는다 (S15P21B109-142). 모듈이 버전을 올리면
# 이미지 빌드가 깨지고, 그 사실이 모듈 담당 MR 의 충돌로 드러난다.
# 대신 "정확히 하나가 만들어졌는지" 만 확인한다 — --no-deps 라 항상 1개여야 한다.
RUN python -m pip wheel --no-cache-dir --no-index --no-deps --no-build-isolation \
        --wheel-dir /wheels ./retirement_simulation \
 && count="$(ls -1 /wheels/retirewise_retirement_simulation-*-py3-none-any.whl | wc -l)" \
 && { [ "$count" = "1" ] || { echo "wheel 이 1개가 아니다: $count"; exit 1; }; }


# ── 2단계: 런타임 ───────────────────────────────────────────────────────────────
FROM python:3.13.14-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN groupadd --system app && useradd --system --gid app --home-dir /app app

COPY backend-pjt/requirements.lock ./requirements.lock
RUN python -m pip install --no-cache-dir --require-hashes -r requirements.lock

# 은퇴 시뮬레이션 모듈: 1단계가 만든 wheel 을 일반 설치한다. 소스 복제나 PYTHONPATH
# 조작을 쓰지 않는다. --no-deps 는 "런타임 외부 의존성 0" 이라는 패키징 계약을
# 이미지에서도 강제한다. BE 소스 COPY 보다 앞에 둬서 코드 변경이 이 레이어를 무효화하지 않게 한다.
COPY --from=simulation-builder \
    /wheels/retirewise_retirement_simulation-*-py3-none-any.whl /tmp/
RUN python -m pip install --no-cache-dir --no-index --no-deps \
        /tmp/retirewise_retirement_simulation-*-py3-none-any.whl \
 && rm /tmp/retirewise_retirement_simulation-*-py3-none-any.whl

COPY --chown=app:app backend-pjt/ .
COPY --chown=app:app contract-fixtures/financial-calculation/v1/policy.json \
    ./contract-fixtures/financial-calculation/v1/policy.json
# 시뮬레이션 전용 계산 정책. wheel 에는 정책 JSON 이 들어 있지 않으므로 서버 설정으로 배치한다.
COPY --chown=app:app contract-fixtures/simulation/v1/policy.json \
    ./contract-fixtures/simulation/v1/policy.json

USER app

EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "readiness.py"]

CMD ["uvicorn", "config.asgi:application", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
