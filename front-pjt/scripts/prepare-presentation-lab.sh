#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
image="retirewise-presentation-lab:local"
command -v docker >/dev/null || { echo "Presentation Lab 계산 데이터 생성에는 Docker가 필요합니다." >&2; exit 1; }
mkdir -p "$repo_root/front-pjt/.dev"
docker build -f "$repo_root/backend-pjt/Dockerfile" -t "$image" "$repo_root"
docker run --rm --network none --user "$(id -u):$(id -g)" \
  --entrypoint python \
  -v "$repo_root:/workspace:ro" \
  -v "$repo_root/front-pjt/.dev:/output" \
  -w /workspace/backend-pjt \
  "$image" devtools/export_presentation_lab.py --output /output/presentation.json
