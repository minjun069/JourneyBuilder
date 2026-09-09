FROM python:3.13.14-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN groupadd --system app && useradd --system --gid app --home-dir /app app

COPY ai-pjt/requirements.lock ./requirements.lock
RUN python -m pip install --no-cache-dir --require-hashes -r requirements.lock

COPY --chown=app:app ai-pjt/ .
COPY --chown=app:app contract-fixtures/ai-lifecycle/v2/step-registry.json \
    ./contract-fixtures/ai-lifecycle/v2/step-registry.json
COPY --chown=app:app contract-fixtures/financial-calculation/v1/policy.json \
    ./contract-fixtures/financial-calculation/v1/policy.json

USER app

EXPOSE 8001

HEALTHCHECK --interval=10s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "readiness.py"]

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8001"]
