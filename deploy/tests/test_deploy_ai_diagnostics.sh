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
  case "$format" in
    *restart_count*)
      printf '%s\n' '  state=restarting running=true restarting=true restart_count=4 exit_code=1 oom_killed=false error=""'
      ;;
    *start=%s*)
      printf '%s\n' '  start=2026-09-16T00:00:00Z end=2026-09-16T00:00:01Z exit=1 output="not ready: RuntimeError\\n"'
      ;;
    *)
      printf '%s\n' healthy
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
grep -q 'ai-pjt 컨테이너 진단 (롤백 전)' "$deploy_log"
grep -q 'state=restarting.*restart_count=4.*exit_code=1.*oom_killed=false' "$deploy_log"
grep -q 'ai-pjt healthcheck 최근 기록' "$deploy_log"
grep -q 'output="not ready: RuntimeError' "$deploy_log"
grep -q 'DIAGNOSTIC_AI_EXCEPTION' "$deploy_log"
grep -q '롤백 후 상태: web=healthy backend=healthy ai-pjt=healthy http=200 api=401' "$deploy_log"

diagnostic_line="$(grep -n 'DIAGNOSTIC_AI_EXCEPTION' "$deploy_log" | cut -d: -f1)"
rollback_line="$(grep -n 'web ← s15p21b109-web:last-good' "$deploy_log" | cut -d: -f1)"
test "$diagnostic_line" -lt "$rollback_line"

if grep -q "$secret_sentinel" "$deploy_log"; then
  echo '배포 진단 로그가 환경변수 값을 노출했습니다' >&2
  exit 1
fi

echo 'deploy AI diagnostics tests passed'
