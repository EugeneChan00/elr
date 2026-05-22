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
      profile: DEFAULT

    locations:
      dev3top:
        compartment_id: ocid1.compartment.oc1...
        vault_id: ocid1.vault.oc1...
        secret_name_template: "{var}"
```

`mode: instance_principal` is also supported for OCI instances.

## Project Config

Project manifests can be public because they only name provider locations and
requested env vars:

```yaml
# env.oci.yaml
version: 1

imports:
  - provider: oci
    location: dev3top
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
