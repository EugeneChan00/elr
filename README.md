# ELR

ELR is a lightweight OCI-backed environment runner. It resolves env vars from
private provider configuration plus project manifests, exports values only into
the child process environment, and writes no plaintext `.env` file.

## Install

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
