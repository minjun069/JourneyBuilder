#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "$0")/../.." && pwd)"
tool="$repo_root/deploy/make-env.sh"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

output="$tmp/app.env"
LANGCHAIN_TRACING_V2=true LANGCHAIN_API_KEY=legacy-test-key \
  bash "$tool" --output "$output" > "$tmp/create.log"

test "$(stat -c '%a' "$output")" = 600
test "$(sed -n 's/^LANGCHAIN_TRACING_V2=//p' "$output")" = false
if grep -q '^LANGCHAIN_API_KEY=' "$output"; then
  echo 'make-env.sh가 사용하지 않는 LANGCHAIN_API_KEY를 생성했습니다' >&2
  exit 1
fi

grep -q 'LANGCHAIN_TRACING_V2: "false"' "$repo_root/docker-compose.yml"
if grep -q 'LANGCHAIN_API_KEY' "$repo_root/docker-compose.yml"; then
  echo 'Compose가 사용하지 않는 LANGCHAIN_API_KEY를 서비스에 전달합니다' >&2
  exit 1
fi

if bash "$tool" --output "$output" > /dev/null 2>&1; then
  echo 'make-env.sh가 기존 환경 파일을 덮어썼습니다' >&2
  exit 1
fi

echo 'make-env tracing guard tests passed'
