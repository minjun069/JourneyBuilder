#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "$0")/../.." && pwd)"
tool="$repo_root/deploy/deploy.sh"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

fake_bin="$tmp/bin"
mkdir -p "$fake_bin"

cat > "$fake_bin/docker" <<'SH'
#!/usr/bin/env bash
set -euo pipefail

if [ "$1" = compose ]; then
  printf '%s\n' "${APP_RELEASE:-missing}" >> "$APP_RELEASE_LOG"
  shift
  while [ $# -gt 0 ]; do
    case "$1" in
      --project-name|--env-file|-f) shift 2 ;;
      *) break ;;
    esac
  done
  command="$1"
  shift
  case "$command" in
    config|up|restart) exit 0 ;;
    ps)
      printf 'fake-%s\n' "${@: -1}"
      ;;
    logs)
      service="${@: -1}"
      if [ "$service" = ai-pjt ]; then
        printf '%s\n' 'Traceback (most recent call last):' 'DIAGNOSTIC_AI_EXCEPTION'
      else
        printf '%s\n' 'backend log marker'
      fi
      ;;
    *) exit 0 ;;
  esac
  exit 0
fi

if [ "$1" = image ]; then
  exit 0
fi

if [ "$1" = tag ]; then
  exit 0
fi

if [ "$1" = inspect ]; then
  format="$3"
  container="${@: -1}"
  case "$format" in
    *restart_count*)
      printf '%s\n' '  state=restarting running=true restarting=true restart_count=4 exit_code=1 oom_killed=false error=""'
      ;;
    *start=%s*)
      printf '%s\n' '  start=2026-09-16T00:00:00Z end=2026-09-16T00:00:01Z exit=1 output="not ready: RuntimeError\\n"'
      ;;
    *State.Status*State.ExitCode*)
      if [ "$container" = fake-rag-bootstrap ]; then
        rag_state="$(dirname "$0")/rag-health-seen"
        if [ "${FAKE_RAG_FAIL_ONCE:-0}" = 1 ] && [ ! -e "$rag_state" ]; then
          : > "$rag_state"
          printf '%s\n' 'exited:2'
        else
          printf '%s\n' 'exited:0'
        fi
      else
        printf '%s\n' 'running:0'
      fi
      ;;
    *)
      if [ "$container" = fake-ai-pjt ]; then
        ai_state="$(dirname "$0")/ai-health-seen"
        if [ -e "$ai_state" ]; then
          printf '%s\n' healthy
        else
          : > "$ai_state"
          printf '%s\n' starting
        fi
      else
        printf '%s\n' healthy
      fi
      ;;
  esac
  exit 0
fi

exit 0
SH

cat > "$fake_bin/curl" <<'SH'
#!/usr/bin/env bash
case "${*: -1}" in
  */consultations/) printf '401' ;;
  *) printf '200' ;;
esac
SH

cat > "$fake_bin/sleep" <<'SH'
#!/usr/bin/env bash
exit 0
SH

chmod +x "$fake_bin/docker" "$fake_bin/curl" "$fake_bin/sleep"

secret_sentinel='DEPLOY_DIAGNOSTICS_MUST_NOT_PRINT_ENV_VALUES'
env_file="$tmp/app.env"
printf 'GOOGLE_API_KEY=%s\n' "$secret_sentinel" > "$env_file"

deploy_log="$tmp/deploy.log"
export APP_RELEASE_LOG="$tmp/app-release.log"
expected_release="$(git -C "$repo_root" rev-parse HEAD)"
if [ -n "$(git -C "$repo_root" status --porcelain)" ]; then
  expected_release="${expected_release}-dirty"
fi
set +e
PATH="$fake_bin:$PATH" \
  ENV_FILE="$env_file" \
  STATE_DIR="$tmp/state" \
  AI_REQUIRED=1 \
  TIMEOUT=0 \
  bash "$tool" --no-build > "$deploy_log" 2>&1
status=$?
set -e

test "$status" -eq 1
test "$(sort -u "$APP_RELEASE_LOG")" = "$expected_release"
grep -q 'ai-pjt 컨테이너 진단 (롤백 전)' "$deploy_log"
grep -q 'state=restarting.*restart_count=4.*exit_code=1.*oom_killed=false' "$deploy_log"
grep -q 'ai-pjt healthcheck 최근 기록' "$deploy_log"
grep -q 'output="not ready: RuntimeError' "$deploy_log"
grep -q 'DIAGNOSTIC_AI_EXCEPTION' "$deploy_log"
grep -q 'rag-bootstrap ← s15p21b109-rag-bootstrap:last-good' "$deploy_log"
grep -q '롤백 후 상태: rag-bootstrap=exited:0 web=healthy backend=healthy ai-pjt=healthy http=200 api=401' "$deploy_log"

diagnostic_line="$(grep -n 'DIAGNOSTIC_AI_EXCEPTION' "$deploy_log" | cut -d: -f1)"
rollback_line="$(grep -n 'web ← s15p21b109-web:last-good' "$deploy_log" | cut -d: -f1)"
test "$diagnostic_line" -lt "$rollback_line"

if grep -q "$secret_sentinel" "$deploy_log"; then
  echo '배포 진단 로그가 환경변수 값을 노출했습니다' >&2
  exit 1
fi

rag_failure_log="$tmp/rag-failure.log"
set +e
PATH="$fake_bin:$PATH" \
  ENV_FILE="$env_file" \
  STATE_DIR="$tmp/rag-state" \
  AI_REQUIRED=1 \
  FAKE_RAG_FAIL_ONCE=1 \
  TIMEOUT=0 \
  bash "$tool" --no-build > "$rag_failure_log" 2>&1
rag_status=$?
set -e

test "$rag_status" -eq 1
grep -q '실패: 공식 RAG artifact import·activate가 완료되지 않았습니다 (exited:2)' "$rag_failure_log"
grep -q 'rag-bootstrap 컨테이너 진단 (롤백 전)' "$rag_failure_log"
grep -q 'rag-bootstrap ← s15p21b109-rag-bootstrap:last-good' "$rag_failure_log"
grep -q '롤백 후 상태: rag-bootstrap=exited:0' "$rag_failure_log"

echo 'deploy AI diagnostics tests passed'
