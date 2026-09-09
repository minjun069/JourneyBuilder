#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "${script_dir}/../../../.." && pwd)"
compose_files=(-f "${repo_root}/docker-compose.yml" -f "${repo_root}/docker-compose.e2e.yml")

case "${1:-all}" in
  --smoke)
    scenarios=(basic-info-proposal-mixed-decisions)
    test_match=harness-smoke.spec.mjs
    ;;
  all)
    scenarios=(
      basic-info-proposal-mixed-decisions
      financial-mindset-proposal-mixed-decisions
      financial-assets-proposal-mixed-decisions
    )
    test_match=representative.spec.mjs
    ;;
  basic-info-proposal-mixed-decisions|financial-mindset-proposal-mixed-decisions|financial-assets-proposal-mixed-decisions)
    scenarios=("$1")
    test_match=representative.spec.mjs
    ;;
  *)
    echo "usage: $0 [--smoke|all|<scenario-id>]" >&2
    exit 2
    ;;
esac

command -v docker >/dev/null 2>&1 || {
  echo 'Docker가 필요합니다.' >&2
  exit 1
}

run_scenario() (
  set -euo pipefail
  scenario="$1"
  token="$(printf '%s:%s:%s' "${scenario}" "$(date +%s%N)" "$$" | sha256sum | cut -c1-16)"

  export F05_SCENARIO_ID="${scenario}"
  export F05_DB_SCHEMA="p07_${token}"
  export F05_REDIS_NAMESPACE="p07:${token}:"
  export F05_CONTROL_TOKEN="f05-control-${token}-safe"
  export POSTGRES_DB=retirewise_e2e
  export POSTGRES_USER=retirewise_e2e
  export POSTGRES_PASSWORD="f05-db-${token}-safe"
  export DJANGO_SECRET_KEY="f05-django-${token}-safe"
  export AI_SERVICE_BEARER_TOKEN="f05-ai-${token}-safe"
  export DATABASE_URL="postgresql+asyncpg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@postgres:5432/${POSTGRES_DB}"
  export API_GATEWAY_PORT=0
  project="f05p07${token}"

  cleanup() {
    docker compose -p "${project}" "${compose_files[@]}" down --volumes --remove-orphans >/dev/null 2>&1 || true
  }
  trap cleanup EXIT INT TERM

  echo "[F05] ${scenario}: isolated stack ${project}"
  docker compose -p "${project}" "${compose_files[@]}" config --quiet
  docker compose -p "${project}" "${compose_files[@]}" build e2e
  docker compose -p "${project}" "${compose_files[@]}" up --detach --build --wait \
    postgres redis schema-init migrate backend gateway fixture-ai front
  if ! docker compose -p "${project}" "${compose_files[@]}" run --rm --no-deps \
    -e "F05_TEST_MATCH=${test_match}" e2e; then
    docker compose -p "${project}" "${compose_files[@]}" logs --no-color backend fixture-ai front gateway >&2
    exit 1
  fi
)

for scenario in "${scenarios[@]}"; do
  run_scenario "${scenario}"
done

echo '[F05] representative scenarios passed'
