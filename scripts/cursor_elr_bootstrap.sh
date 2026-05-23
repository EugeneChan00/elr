#!/usr/bin/env bash
set -euo pipefail

log() {
  printf 'elr bootstrap: %s\n' "$*" >&2
}

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

env_file="${ELR_BOOTSTRAP_ENV_FILE:-.cursor/elr.env}"
profile_args=(profile add --write-oci-config --force)
if [[ -f "$env_file" ]]; then
  profile_args+=(--from-env-file "$env_file")
fi

if command -v elr >/dev/null 2>&1; then
  log "using elr from PATH"
  elr "${profile_args[@]}"
elif command -v uv >/dev/null 2>&1; then
  log "using repo checkout through uv"
  uv sync
  uv run elr "${profile_args[@]}"
elif command -v python3 >/dev/null 2>&1; then
  log "using repo checkout through .venv"
  python3 -m venv .venv
  .venv/bin/python -m pip install -e .
  .venv/bin/elr "${profile_args[@]}"
else
  log "missing elr, uv, and python3; cannot bootstrap"
  exit 1
fi

log "profile ready"
