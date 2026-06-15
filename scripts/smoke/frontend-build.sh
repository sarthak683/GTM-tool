#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/../../frontend"

if [ ! -d node_modules/aircall-everywhere ]; then
  echo "Declared dependencies are missing from frontend/node_modules; running npm ci."
  npm ci
fi

npm run build
