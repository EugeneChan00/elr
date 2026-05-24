# ELR

ELR is a lightweight OCI-backed environment runner. It bootstraps SOPS age keys
from Oracle Vault, decrypts all configured `.env.sops` files, resolves OCI
import variables, and runs child commands with the merged environment.

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

## Unified config stack

ELR loads the same YAML schema from three layers (lowest → highest precedence):

| File | Role |
| --- | --- |
| `/etc/elr/config.yaml` | System baseline: `providers`, `sops.keys`, `imports` |
| `~/.config/elr/config.yaml` | User overlay |
| `env.oci.yaml` (repo, cwd → git root) | Project overlay |

On conflict, **repo wins over user wins over etc**. Explicit `-e/--env` is
highest of all.

| Section | Merge rule |
| --- | --- |
| `providers.oci.locations.<name>` | Deep merge per location; `compartment_id` / `vault_id` replaced by higher layer |
| `providers.oci.locations.<name>.secrets` | **Union + dedupe** across layers (repo can append bundle names) |
| `sops.keys.<catalog_id>` | Deep merge per catalog id |
| `sops.env.<alias>` | Deep merge per alias (all entries decrypted on run) |
| `sops.keys_file` | Scalar replace — highest layer wins |
| `imports` | Append entries; `vars` must be a YAML list |
| `local` | Per-key replace — highest layer wins |

### Naming: `key` vs `secrets`

Two different OCI concepts:

| YAML path | Meaning |
| --- | --- |
| `providers.oci.locations.*.secrets` | Dotenv **bundle allowlist** — OCI Vault objects holding `KEY=VALUE` bundles for `imports` |
| `sops.keys.<catalog_id>.key` | **Age-key Vault object name** — single object whose payload is age private key material |

Under `sops.keys`, the field is always **`key`**, never `secret`. Deprecated
`sops.keys.*.secret` is accepted with a warning.

### User baseline (`examples/user-config.yaml`)

See `examples/user-config.yaml` for a fully commented template covering
`providers`, `sops.keys_file`, `sops.keys`, `sops.env`, `imports`, and `local`.

### Repo overlay (`examples/env.oci.yaml`)

Typical repo manifest adds `sops.env` paths, optional `sops.keys`, `imports`,
and `local` — without repeating full `providers` unless needed.

## SOPS bootstrap

Oracle Vault stores the age private key; application secrets live in encrypted
`.env.sops` files in git.

| Layer | Responsibility |
| --- | --- |
| PEM / OCI API key | Authenticate to Oracle Vault |
| Oracle Vault | Store age key object (e.g. `sops-age-keys`) |
| ELR | `elr sops sync` → master `sops.keys_file` (default `~/.config/sops/age/keys.txt`) |
| SOPS | Encrypt/decrypt `.env.sops` |
| direnv | Optional interactive shell decrypt (see `examples/envrc.sops`) |

Vault secret body can be full `keys.txt`, a single `AGE-SECRET-KEY-1...` line,
or `SOPS_AGE_KEY=...`.

### SOPS commands

```bash
elr sops sync                  # sync all catalog keys into keys_file
elr sops sync work             # sync one catalog id from sops.keys
elr sops sync --print-plan     # show resolved config (no Vault fetch)
elr sops source                # print export SOPS_AGE_KEY_FILE=...
elr sops source --sync         # fetch keys when keys file is missing
elr sops remove work           # remove catalog block from local keys file
eval "$(elr sops source)"      # shell bootstrap
```

All catalog entries merge into **one** master `keys_file` (tagged with
`# elr-catalog: <id>` blocks).

## Default run: `elr <command>`

No `--` separator required. ELR merges **all** configured sources, then execs
the child:

1. Auto-sync age keys when `sops.keys` is defined and `keys_file` is missing
   (disable with `--no-sync`)
2. Set `SOPS_AGE_KEY_FILE`
3. Decrypt every path in merged `sops.env` via `sops -d`
4. Resolve all `imports` from OCI dotenv bundles
5. Apply merged `local`
6. `exec` the command

```bash
elr docker compose up -d
elr mc ls my-bucket
elr -e deployment/env.oci.yaml -- cloudflared tunnel run --token "$TOKEN"
elr --print-plan -- codex
```

Commands containing `$VAR` must be quoted through a child shell:

```bash
elr bash -lc 'echo "$OPENAI_API_KEY"'
```

### direnv

See `examples/envrc.sops` for a `.envrc` that syncs the age key when missing,
then sources decrypted dotenv from `.env.sops`.

## Private OCI config

Keep provider identity outside public repos:

```yaml
# ~/.config/elr/config.yaml
version: 1

providers:
  oci:
    auth:
      mode: config_file
      region: [REDACTED]
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

Create or update the private OCI profile from environment values:

```bash
ELR_OCI_REGION=[REDACTED] \
ELR_OCI_COMPARTMENT_ID=ocid1.compartment.oc1... \
ELR_OCI_VAULT_ID=ocid1.vault.oc1... \
ELR_OCI_SECRETS=github-services,openai-services \
elr profile add
```

`elr profile add --from-env-file .sandbox/elr.env` reads `ELR_OCI_*` values
from a dotenv file. Shell environment values take precedence over the dotenv
file.

For fresh sandboxes, `elr profile add --write-oci-config` reconstructs the OCI
SDK profile and private key from environment variables.

## Cursor Sandbox Bootstrap

This repo includes `.cursor/environment.json`, which runs:

```bash
bash scripts/cursor_elr_bootstrap.sh
```

on Cursor environment setup. Provide `ELR_OCI_*` variables through Cursor's
environment/secrets UI. See the script and prior docs for the full variable list.

## Project config example

```yaml
# env.oci.yaml
version: 1

imports:
  - provider: oci
    location: dev-env
    vars:
      - GH_TOKEN
      - CLI_PROXY_API_KEY

sops:
  env:
    app: .env.sops

local:
  CLI_PROXY_BASE_URL: https://proxy.example.com/v1
```

## Agent Session Env (`~/.agents`)

For PI or other agent sessions, keep a fixed manifest at
`~/.agents/env.oci.yaml` and load it through the cross-platform Python runner.

The agent runner uses the same unified env merge (SOPS decrypt + imports +
local):

```bash
cd ~/.agents
uv run runner.py --print-plan
uv run runner.py --load-only
uv run runner.py -- <pi-agent-command> [args...]
```

From the ELR repo checkout:

```bash
uv run scripts/agent_elr_runner.py --load-only
uv run scripts/agent_elr_runner.py -- <pi-agent-command> [args...]
```

Set `AGENT_ENV_FILE` to override the default manifest path.
