#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/../.."

python3 -m compileall -q app scripts

echo "Backend Python compile smoke passed."

