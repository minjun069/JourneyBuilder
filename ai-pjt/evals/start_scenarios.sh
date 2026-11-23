#!/usr/bin/env bash
# Start one run and return immediately; no Codex session is needed afterwards.
set -euo pipefail
umask 077
eval_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
repo_dir=$(cd -- "$eval_dir/../.." && pwd)
dataset=${1:-"$eval_dir/data/torifinance-case-v1"}
output=${2:-"$repo_dir/test-results/torifinance-$(date +%Y%m%d-%H%M%S)"}
config=${SCENARIO_CONFIG:-/tmp/torifinance-run-config.json}
env_file=${SCENARIO_ENV_FILE:-"$repo_dir/.env"}
python=${SCENARIO_PYTHON:-"$repo_dir/ai-pjt/.venv/bin/python"}
export PYTHONDONTWRITEBYTECODE=1
export PYTHONPATH="${PYTHONPATH:+$PYTHONPATH:}/tmp/ax-scenario-schema-deps"
args=("$eval_dir/scenario_suite.py" --dataset "$dataset" --config "$config"
      --env-file "$env_file" --output "$output" --workers "${SCENARIO_WORKERS:-3}" --results-only)
if [[ -e "$output" || -e "$output.log" ]]; then
    echo "Output or log already exists; choose a new output path." >&2
    exit 1
fi
"$python" "${args[@]}" --validate-only
mkdir -p -- "$(dirname -- "$output")"
set -o noclobber
nohup "$python" -u "${args[@]}" > "$output.log" 2>&1 < /dev/null &
echo "PID: $!"
echo "Results: $output/{vanilla,sep20,master}/case-*.results.json"
echo "Log: $output.log"
echo "Completion: look for finished or finished_with_errors in the log."
