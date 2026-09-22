FROM python:3.13.14-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN groupadd --system app && useradd --system --gid app --home-dir /app app

COPY ai-pjt/requirements.lock ./requirements.lock
RUN python -m pip install --no-cache-dir --require-hashes -r requirements.lock

COPY --chown=app:app ai-pjt/ .
# 런타임 코드가 읽는 계약 픽스처는 이미지에 넣어야 한다. CI test 잡은 저장소를 통째로 마운트해서 돌기 때문에
COPY --chown=app:app contract-fixtures/ai-lifecycle/v2/step-registry.json \
    contract-fixtures/ai-lifecycle/v2/input-assist-default-values.json \
    contract-fixtures/ai-lifecycle/v2/reference-tokenization.json \
    ./contract-fixtures/ai-lifecycle/v2/
COPY --chown=app:app contract-fixtures/financial-calculation/v1/policy.json \
    ./contract-fixtures/financial-calculation/v1/policy.json

# CI unit tests mount the repository at /src, so verify static Agent assets in
# the image's real /app layout before the image can be promoted.
RUN python runtime_assets.py

ARG APP_RELEASE=local
ENV LANGFUSE_RELEASE=${APP_RELEASE}
LABEL org.opencontainers.image.revision=${APP_RELEASE}

USER app

EXPOSE 8001

HEALTHCHECK --interval=10s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "readiness.py"]

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8001"]
