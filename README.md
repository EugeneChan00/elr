# ELR

ELR is a lightweight OCI-backed environment runner. It can bootstrap SOPS age
keys from Oracle Vault (recommended for multi-device setups) or resolve env vars
from private provider configuration plus project manifests for legacy
`elr -- <cmd>` workflows.

## Install

For agents and sandboxes, install the released CLI into `~/.local/bin/elr`:

```bash
curl -fsSL https://raw.githubusercontent.com/EugeneChan00/elr/v0.1.0/scripts/install.sh | bash
```

The installer downloads the latest GitHub Release wheel, installs it into a
private virtual environment under `~/.local/share/elr`, and symlinks the CLI to
`~/.local/bin/elr`.

For local development:

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e .
```

The runtime package depends on `oci` and `PyYAML`.

## SOPS bootstrap (recommended)

Use Oracle Vault for one secret only — the SOPS age private key — and keep all
application secrets in an encrypted `.env.sops` in git. Daily workflows use
`direnv` + `sops`, not `elr --`.

| Layer | Responsibility |
| --- | --- |
| PEM / OCI API key | Authenticate to Oracle Vault |
| Oracle Vault | Store `sops-age-key` (age private key) |
| ELR | `elr sops sync` → `~/.config/sops/age/keys.txt` |
| SOPS | Encrypt/decrypt `.env.sops` (MC_HOST_*, API keys, …) |
| direnv | Decrypt on `cd` into the project |

### Layered manifests — `env.oci.yaml` is canonical

One manifest format for both legacy env injection and SOPS key sync. ELR loads the
same file stack everywhere:

| File | Role |
| --- | --- |
| `/etc/elr/config.yaml` | System baseline: `providers` + `sops.keys` catalog |
| `~/.config/elr/config.yaml` | User overlay: more keys, auth, defaults |
| `env.oci.yaml` (repo root) | Project overlay: `sops.sync` picks catalog entry + `env_file` |

Later files **merge** `providers` and `sops.keys` (deep merge). The repo
`env.oci.yaml` can be public — only key ids and paths, no secret values.

**User baseline** (`examples/user-config.yaml`):

```yaml
sops:
  defaults:
    key: default
    age_key_dir: ~/.config/sops/age
  keys:
    default:
      location: dev-env
      secret: sops-age-key
    work:
      location: dev-env
      secret: sops-age-key-work
providers:
  oci: { ... }
```

**Repo** (`examples/env.oci.yaml`):

```yaml
sops:
  sync:
    key: work
    env_file: .env.sops
```

`elr sops sync` in that repo fetches `sops-age-key-work` → `~/.config/sops/age/work.txt`.
Flat `sops: { secret, age_key_file, ... }` in user config still maps to `keys.default`.
Legacy `imports` / `local` in the same file still work for `elr -- <cmd>`.

Set `auth.region` on the OCI provider (see `elr profile add`). Vault secret body can be
full `keys.txt`, a single `AGE-SECRET-KEY-1...` line, or `SOPS_AGE_KEY=...`.

### Commands

```bash
elr sops sync                  # active key for this repo (from env.oci.yaml sops.sync)
elr sops sync work             # named key from catalog
elr sops sync --all            # every key in sops.keys
elr sops sync --print-plan     # show resolved keys/paths (no Vault fetch)
eval "$(elr sops source)"      # export SOPS_AGE_KEY_FILE for active repo key
elr sops -- mc ls my-bucket    # sync active key + sops exec-env
```

### direnv

See `examples/envrc.sops` for a `.envrc` that runs `elr sops sync` when the key
file is missing, then sources decrypted dotenv from `.env.sops`.

```bash
cd project
mc ls oci-phoenix/quick-finance   # MC_HOST_* from sops, not elr --
```

You do not need Oracle for SOPS itself — the age key can live on each device,
NAS over Tailscale, or a password manager. Oracle + ELR is useful when you want
IAM, audit, and one canonical key fetched per device with PEM bootstrap.

## Private Config (legacy env injection)

Keep provider identity outside public repos:

```yaml
# ~/.config/elr/config.yaml
version: 1

providers:
  oci:
    auth:
      mode: config_file
      region: us-phoenix-1
      config_file: ~/.oci/config
      profile: ELR

    locations:
      dev-env:
        compartment_id: ocid1.compartment.oc1...
        vault_id: ocid1.vault.oc1...
        secrets:
          - github-services
          - openai-services
```

`mode: instance_principal` is also supported for OCI instances.

You can create or update this private OCI profile config from environment values:

```bash
ELR_OCI_REGION=us-phoenix-1 \
ELR_OCI_COMPARTMENT_ID=ocid1.compartment.oc1... \
ELR_OCI_VAULT_ID=ocid1.vault.oc1... \
ELR_OCI_SECRETS=github-services,openai-services \
elr profile add
```

`elr profile add --from-env-file .sandbox/elr.env` also reads `ELR_OCI_*`
values from a dotenv file. Shell environment values take precedence over the
dotenv file. The command writes only `~/.config/elr/config.yaml`, creates the
config directory with `0700`, and writes the config file with `0600`.

For fresh sandboxes, `elr profile add --write-oci-config` can also reconstruct
the OCI SDK profile and private key from environment variables:

```bash
ELR_OCI_USER_ID=ocid1.user.oc1... \
ELR_OCI_TENANCY_ID=ocid1.tenancy.oc1... \
ELR_OCI_FINGERPRINT=aa:bb:cc:... \
ELR_OCI_PRIVATE_KEY_B64="$(base64 -i ~/.oci/elr_api.pem)" \
ELR_OCI_REGION=us-phoenix-1 \
ELR_OCI_COMPARTMENT_ID=ocid1.tenancy.oc1... \
ELR_OCI_VAULT_ID=ocid1.vault.oc1... \
ELR_OCI_SECRETS=github-services,openai-services \
elr profile add --write-oci-config
```

This writes `~/.oci/elr_api.pem`, `~/.oci/config`, and
`~/.config/elr/config.yaml` with private local permissions. The local key file
name does not need to match anything in OCI; it only needs to match the
`key_file` path in the OCI config profile.

## Cursor Sandbox Bootstrap

This repo includes `.cursor/environment.json`, which runs:

```bash
bash scripts/cursor_elr_bootstrap.sh
```

on Cursor environment setup. In a fresh Cursor sandbox, provide these environment
variables through Cursor's environment/secrets UI:

```bash
ELR_OCI_USER_ID=ocid1.user.oc1...
ELR_OCI_TENANCY_ID=ocid1.tenancy.oc1...
ELR_OCI_FINGERPRINT=aa:bb:cc:...
ELR_OCI_PRIVATE_KEY_B64=<base64 encoded RSA private key>
ELR_OCI_REGION=us-phoenix-1
ELR_OCI_COMPARTMENT_ID=ocid1.tenancy.oc1...
ELR_OCI_VAULT_ID=ocid1.vault.oc1...
ELR_OCI_SECRETS=github-services,openai-services
```

Optional overrides:

```bash
ELR_OCI_PROFILE=ELR
ELR_OCI_LOCATION=dev-env
ELR_OCI_CONFIG_FILE=~/.oci/config
ELR_OCI_KEY_FILE=~/.oci/elr_api.pem
ELR_BOOTSTRAP_ENV_FILE=.cursor/elr.env
```

The bootstrap first uses `elr` if it is already installed, otherwise it installs
the latest GitHub Release with:

```bash
curl -fsSL https://raw.githubusercontent.com/EugeneChan00/elr/v0.1.0/scripts/install.sh | bash
```

If curl/Python are unavailable, it falls back to the repo checkout with
`uv run elr`, then to a local `.venv` editable install. `.cursor/*.env` is
gitignored for local sandbox-only setup files.

## Project Config

Project manifests can be public because they only name provider locations and
requested env vars:

```yaml
# env.oci.yaml
version: 1

imports:
  - provider: oci
    location: dev-env
    vars:
      - GH_TOKEN
      - CLI_PROXY_API_KEY
      - OPENAI_API_KEY

local:
  CLI_PROXY_BASE_URL: https://proxy.example.com/v1
  OPENAI_API_BASE_URL: https://proxy.example.com/v1
```

## Usage

```bash
elr -- docker compose up -d
elr -- bash -lc 'echo "$OPENAI_API_KEY"'
elr -e deployment/cli-proxy-stack/env.oci.yaml -- cloudflared tunnel run --token "$CLOUDFLARE_TUNNEL_A1_AGENT_SERVICE_RUN_TOKEN"
elr --print-plan -- codex
elr --no-env -- ls
```

Commands containing `$VAR` must be quoted through a child shell:

```bash
elr -- bash -lc 'echo "$OPENAI_API_KEY"'
```

Do not use:

```bash
elr -- echo "$OPENAI_API_KEY"
```

because the parent shell expands `$OPENAI_API_KEY` before `elr` starts.

## Agent Session Env (`~/.agents`)

For PI or other agent sessions, keep a fixed manifest at
`~/.agents/env.oci.yaml` and load it through the cross-platform Python runner
(works on Windows via `uv run`, no shell script required).

```yaml
# ~/.agents/env.oci.yaml
version: 1

imports:
  - provider: oci
    location: dev-env
    vars:
      - CLI_PROXY_URL
      - CLI_PROXY_API_KEY
      - OPENAI_API_KEY

local: {}
```

Put both the proxy URL and API key in the OCI secret bundle (dotenv format):

```dotenv
CLI_PROXY_URL=https://proxy.example.com/v1
CLI_PROXY_API_KEY=sk-...
OPENAI_API_KEY=sk-...
```

Copy the standalone uv project template from `examples/agents/` into
`~/.agents/`, then at PI session startup either bootstrap the current process
or wrap the agent command:

```bash
cd ~/.agents
uv run runner.py --print-plan
uv run runner.py --load-only
uv run runner.py -- <pi-agent-command> [args...]
```

From the ELR repo checkout:

```bash
uv run scripts/agent_elr_runner.py -- <pi-agent-command> [args...]
```

Set `AGENT_ENV_FILE` to override the default manifest path.
