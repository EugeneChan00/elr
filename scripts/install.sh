#!/usr/bin/env bash
set -euo pipefail

repo="${ELR_INSTALL_REPO:-EugeneChan00/elr}"
version="${ELR_VERSION:-latest}"
install_dir="${ELR_INSTALL_DIR:-$HOME/.local/share/elr}"
bin_dir="${ELR_BIN_DIR:-$HOME/.local/bin}"

log() {
  printf 'elr install: %s\n' "$*" >&2
}

need() {
  if ! command -v "$1" >/dev/null 2>&1; then
    log "missing required command: $1"
    exit 1
  fi
}

download_url_for_release() {
  python3 - "$repo" "$version" <<'PY'
import json
import sys
import urllib.request

repo, version = sys.argv[1], sys.argv[2]
if version == "latest":
    api_url = f"https://api.github.com/repos/{repo}/releases/latest"
else:
    api_url = f"https://api.github.com/repos/{repo}/releases/tags/{version}"

with urllib.request.urlopen(api_url) as response:
    release = json.load(response)

for asset in release.get("assets", []):
    name = asset.get("name", "")
    if name.endswith(".whl"):
        print(asset["browser_download_url"])
        break
else:
    raise SystemExit(f"no wheel asset found for {repo} {version}")
PY
}

need python3

mkdir -p "$install_dir" "$bin_dir"
tmpdir="$(mktemp -d)"
trap 'rm -rf "$tmpdir"' EXIT

wheel_file="${ELR_WHEEL_FILE:-}"
if [[ -z "$wheel_file" ]]; then
  need curl
  wheel_url="$(download_url_for_release)"
  wheel_file="$tmpdir/elr.whl"
  log "downloading $repo $version"
  curl -fsSL "$wheel_url" -o "$wheel_file"
else
  log "using local wheel $wheel_file"
fi

venv="$install_dir/venv"
python3 -m venv "$venv"
"$venv/bin/python" -m ensurepip --upgrade >/dev/null
"$venv/bin/python" -m pip install --upgrade pip >/dev/null
"$venv/bin/python" -m pip install --upgrade "$wheel_file" >/dev/null

ln -sf "$venv/bin/elr" "$bin_dir/elr"

log "installed $("$bin_dir/elr" --version 2>/dev/null || "$bin_dir/elr" --help | head -1)"
log "binary: $bin_dir/elr"
case ":$PATH:" in
  *":$bin_dir:"*) ;;
  *) log "add $bin_dir to PATH if your shell cannot find elr" ;;
esac
