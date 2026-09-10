#!/usr/bin/env bash
set -euo pipefail

RAG_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPOSITORY_ROOT="$(cd "${RAG_ROOT}/../.." && pwd)"
COMPOSE_FILE="${RAG_ROOT}/docker-compose.yml"

usage() {
  cat <<'EOF'
Usage:
  ops.sh up | status | stop | db-check
  ops.sh qualify [run-operational options]
  ops.sh export [artifact export options]
  ops.sh import [artifact import options]

Required environment for managed local DB:
  RAG_INSTANCE_ID, RAG_POSTGRES_PASSWORD
Optional: RAG_POSTGRES_PORT, RAG_POSTGRES_DB, RAG_POSTGRES_USER

qualify additionally requires GOOGLE_API_KEY. Official qualification also needs
--artifact-kind official --operator-id <id> --confirm-rag-owner --promote.
EOF
}

require_local_database_environment() {
  : "${RAG_INSTANCE_ID:?RAG_INSTANCE_ID is required}"
  : "${RAG_POSTGRES_PASSWORD:?RAG_POSTGRES_PASSWORD is required}"
  if [[ ! "${RAG_INSTANCE_ID}" =~ ^[A-Za-z0-9_-]+$ ]]; then
    echo "RAG_INSTANCE_ID must use only letters, digits, underscore, or hyphen" >&2
    exit 2
  fi
  if [[ ! "${RAG_POSTGRES_PASSWORD}" =~ ^[A-Za-z0-9_-]+$ ]]; then
    echo "RAG_POSTGRES_PASSWORD must be URL-safe: letters, digits, underscore, or hyphen" >&2
    exit 2
  fi
}

compose() {
  docker compose --file "${COMPOSE_FILE}" "$@"
}

source_revision() {
  local revision
  revision="$(git -C "${REPOSITORY_ROOT}" rev-parse HEAD)"
  if [[ -n "$(git -C "${REPOSITORY_ROOT}" status --porcelain)" ]]; then
    revision="${revision}-dirty"
  fi
  printf '%s' "${revision}"
}

require_clean_official_source() {
  local previous=""
  local argument
  for argument in "$@"; do
    if [[ "${previous}" == "--artifact-kind" && "${argument}" == "official" ]]; then
      if [[ -n "$(git -C "${REPOSITORY_ROOT}" status --porcelain)" ]]; then
        echo "official qualification requires a clean committed worktree" >&2
        exit 2
      fi
    fi
    previous="${argument}"
  done
}

command="${1:-}"
if [[ -z "${command}" ]]; then
  usage
  exit 2
fi
shift

case "${command}" in
  up)
    require_local_database_environment
    compose up --detach --wait postgres
    ;;
  status)
    require_local_database_environment
    compose ps
    ;;
  stop)
    require_local_database_environment
    compose stop postgres
    ;;
  db-check)
    require_local_database_environment
    compose exec --no-TTY postgres psql \
      --username "${RAG_POSTGRES_USER:-retirewise}" \
      --dbname "${RAG_POSTGRES_DB:-retirewise_db}" \
      --set ON_ERROR_STOP=on \
      --command "SELECT extname FROM pg_extension WHERE extname = 'vector';" \
      --command "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public' AND table_name LIKE 'rag_%' ORDER BY table_name;"
    ;;
  qualify)
    require_local_database_environment
    : "${GOOGLE_API_KEY:?GOOGLE_API_KEY is required for Gemini qualification}"
    require_clean_official_source "$@"
    RAG_SOURCE_REVISION="$(source_revision)" \
      compose run --rm --build --no-TTY operator \
      -m rag.pipeline.cli run-operational "$@"
    ;;
  export)
    require_local_database_environment
    RAG_SOURCE_REVISION="$(source_revision)" \
      compose run --rm --build --no-TTY operator \
      -m rag.artifacts export "$@"
    ;;
  import)
    require_local_database_environment
    RAG_SOURCE_REVISION="$(source_revision)" \
      compose run --rm --build --no-TTY operator \
      -m rag.artifacts import "$@"
    ;;
  *)
    usage
    exit 2
    ;;
esac
