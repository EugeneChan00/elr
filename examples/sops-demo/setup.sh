#!/usr/bin/env bash
# Generate local age key, encrypt .env.plain → .env.sops, write ELR master keys.txt
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

if ! command -v age-keygen >/dev/null || ! command -v sops >/dev/null; then
  echo "Install age and sops first (see examples/sops-demo/README.md)" >&2
  exit 1
fi

if [[ ! -f age-key.txt ]]; then
  age-keygen -o age-key.txt <<< "elr-sops-demo"
fi

PUB=$(grep '^# public key:' age-key.txt | awk '{print $4}')
SECRET=$(grep '^AGE-SECRET-KEY-' age-key.txt)

cat > keys.txt <<EOF
# elr-catalog: default
# synced by elr (local demo)
${SECRET}
EOF
chmod 600 keys.txt age-key.txt

cat > demo.env.plain <<'EOF'
APP_NAME=elr-demo
DB_PASSWORD=demo-secret-123
API_TOKEN=token-abc-xyz
EOF

sops encrypt -a "$PUB" --input-type dotenv --output-type dotenv demo.env.plain > demo.env.sops

echo "Wrote keys.txt and demo.env.sops (public key: $PUB)"
echo "Try: elr -e $DIR/env.oci.yaml --no-sync bash -lc 'echo APP=\$APP_NAME DB=\$DB_PASSWORD'"
