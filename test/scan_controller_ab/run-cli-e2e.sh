#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

"$SCRIPT_DIR/run-ab-test.sh" >/dev/null

cd "$SCRIPT_DIR"
docker rm -f am-scan-cli-e2e >/dev/null 2>&1 || true
set +e
docker compose -f docker-compose.yaml run \
  --name am-scan-cli-e2e \
  --entrypoint python3 \
  ab-runner \
  /app/test/scan_controller_ab/cli_e2e_test.py
RUN_EXIT=$?
set -e

docker logs am-scan-cli-e2e
docker rm -f am-scan-cli-e2e >/dev/null 2>&1 || true
exit "$RUN_EXIT"
