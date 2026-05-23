# ELR

ELR is a lightweight OCI-backed environment runner. It resolves env vars from
private provider configuration plus project manifests, exports values only into
the child process environment, and writes no plaintext `.env` file.

## Install

For agents and sandboxes, install the released CLI into `~/.local/bin/elr`:

```bash
curl -fsSL https://raw.githubusercontent.com/EugeneChan00/elr/main/scripts/install.sh | bash
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

## Private Config

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
curl -fsSL https://raw.githubusercontent.com/EugeneChan00/elr/main/scripts/install.sh | bash
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
  CLI_PROXY_BASE_URL: https://aa.renaissancelab.org/v1
  OPENAI_API_BASE_URL: https://aa.renaissancelab.org/v1
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
