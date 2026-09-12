#!/usr/bin/env bash
set -euo pipefail

f14_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
f14_project="${F14_COMPOSE_PROJECT_NAME:-f14regression-${CI_PIPELINE_ID:-local}-${CI_JOB_ID:-run}}"
case "$f14_project" in
  ""|*[!a-zA-Z0-9_-]*)
    echo "F14_COMPOSE_PROJECT_NAME must contain only letters, digits, underscore, or hyphen" >&2
    exit 2
    ;;
esac

f14_ai_image="${f14_project}-ai:test"
f14_node_volume="${f14_project}-node-modules"
export POSTGRES_PASSWORD="${CI_POSTGRES_PASSWORD:-f14-regression-postgres}"
export DJANGO_SECRET_KEY="${CI_DJANGO_SECRET_KEY:-f14-regression-django-secret-at-least-32-bytes}"
export AI_SERVICE_BEARER_TOKEN="${CI_AI_SERVICE_BEARER_TOKEN:-f14-regression-service-token}"
export AI_MASKING_PSEUDONYM_KEY="${CI_AI_MASKING_PSEUDONYM_KEY:-f14-regression-masking-key-at-least-32-bytes}"
export GOOGLE_API_KEY="${CI_GOOGLE_API_KEY:-f14-regression-dummy-provider-key}"
export DATABASE_URL="postgresql://retirewise:${POSTGRES_PASSWORD}@postgres:5432/retirewise_db"

f14_cleanup() {
  docker compose -p "$f14_project" -f "$f14_root/docker-compose.yml" down \
    --volumes --remove-orphans >/dev/null 2>&1 || true
  docker volume rm "$f14_node_volume" >/dev/null 2>&1 || true
  docker image rm -f "$f14_ai_image" \
    "${f14_project}-backend:latest" "${f14_project}-migrate:latest" \
    >/dev/null 2>&1 || true
}
trap f14_cleanup EXIT INT TERM

bash "$f14_root/deploy/tests/test_f14_config_snapshot.sh"

docker build -q -f "$f14_root/ai-pjt/Dockerfile" -t "$f14_ai_image" "$f14_root"
docker run --rm \
  --mount "type=bind,src=${f14_root},dst=/src,readonly" \
  -w /src/ai-pjt \
  -e AI_SERVICE_BEARER_TOKEN -e AI_MASKING_PSEUDONYM_KEY \
  -e GOOGLE_API_KEY -e DATABASE_URL \
  "$f14_ai_image" sh -c "
    python -m unittest discover -s retirement_simulation/tests -t . -q &&
    python -m unittest discover -s tests -q &&
    python -m unittest discover -s rag/tests -q &&
    python -m unittest discover -s tools/tests -q &&
    python -m unittest discover -s evals/tests -q &&
    cd evals/security && python -m unittest discover -p 'test_*.py' -q
  "

docker compose -p "$f14_project" -f "$f14_root/docker-compose.yml" run \
  --build --rm \
  --volume "$f14_root:/src:ro" \
  --workdir /src/backend-pjt \
  backend sh -c "
    python -m unittest discover -s tests -q &&
    python -m unittest discover -s contract_tests -q &&
    python manage.py test --verbosity 1
  "

docker volume create "$f14_node_volume" >/dev/null
docker run --rm \
  -e npm_config_cache=/tmp/.npm \
  --mount "type=bind,src=${f14_root},dst=/workspace,readonly" \
  --mount "type=volume,src=${f14_node_volume},dst=/workspace/front-pjt/node_modules" \
  -w /workspace/front-pjt node:22-bookworm \
  sh -c "npm ci --no-audit --no-fund && npm test"

python3 "$f14_root/ai-pjt/evals/security/f14_full_regression.py"
python3 "$f14_root/ai-pjt/evals/security/f14_rollback_evidence.py"
python3 "$f14_root/ai-pjt/evals/security/f14_final_e2e.py"
