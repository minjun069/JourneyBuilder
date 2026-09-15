#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "$0")/../.." && pwd)"
tool="$repo_root/deploy/make-env.sh"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

output="$tmp/app.env"
GOOGLE_API_KEY=test-gms-key bash "$tool" --output "$output" > "$tmp/create.log"

test "$(stat -c '%a' "$output")" = 600
test "$(sed -n 's/^GOOGLE_API_KEY=//p' "$output")" = test-gms-key

for fixed_name in \
  DJANGO_DEBUG EMBEDDING_PROVIDER EMBEDDING_MODEL GOOGLE_GEMINI_BASE_URL \
  RAG_FINANCIAL_COLLECTION RAG_INTERFACE_COLLECTION EMBED_DIM \
  LANGCHAIN_TRACING_V2 LANGCHAIN_API_KEY
do
  if grep -q "^${fixed_name}=" "$output"; then
    echo "make-env.sh가 Compose 고정값을 루트 env에 생성했습니다: $fixed_name" >&2
    exit 1
  fi
done

grep -q 'LANGCHAIN_TRACING_V2: "false"' "$repo_root/docker-compose.yml"
if grep -q 'LANGCHAIN_API_KEY' "$repo_root/docker-compose.yml"; then
  echo 'Compose가 사용하지 않는 LANGCHAIN_API_KEY를 서비스에 전달합니다' >&2
  exit 1
fi

if bash "$tool" --output "$output" > /dev/null 2>&1; then
  echo 'make-env.sh가 기존 환경 파일을 덮어썼습니다' >&2
  exit 1
fi

echo 'make-env root contract tests passed'
