#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
app="${1:-$PWD/.artifacts/mac/Build/Products/Debug/DuoWorkspaceHost.app}"
set --
if [[ "${ENABLE_HARDENED_RUNTIME:-NO}" == YES ]]; then set -- --runtime; fi
exec /usr/bin/python3 Scripts/local_signing.py sign \
  --state-dir "${DUO_SIGNING_DIR:-$PWD/.secrets}" --app "$app" "$@"
