# ELR Handoff

## Linear / Product Context

Primary Linear issue: **RT-133 — Dev Environment Secret Management Setup**

Goal: create a lightweight `infisical run`-style utility for GitOps and shell
workflows that fetches secrets from Oracle Cloud Infrastructure Vault and exports
them only into the child process environment. This is separate from Agent Vault,
which remains useful for AI-agent HTTP proxy credential injection but is awkward
for Docker Compose, systemd env files, `cloudflared --token`, and other CLI/env
driven workflows.

## Current Repo State

Repo path on VPS:

```text
/home/ubuntu/elr
```

Implemented as a Python package:

```text
elr/cli.py              # CLI parsing, print-plan, no-env, exec
elr/config.py           # config discovery, YAML loading, merge/validation
elr/resolver.py         # local/env secret resolution plan
elr/providers/oci.py    # OCI SDK provider
tests/                  # unittest coverage
examples/               # example project and user configs
```

Local executable wrapper exists on the VPS:

```text
/home/ubuntu/.local/bin/elr
```

It runs the repo checkout directly:

```sh
PYTHONPATH="/home/ubuntu/elr${PYTHONPATH:+:$PYTHONPATH}" exec python3 -m elr.cli "$@"
```

## Supported UX

```bash
elr -- docker compose up -d
elr -- bash -lc 'echo "$OPENAI_API_KEY"'
elr -e deployment/cli-proxy-stack/env.oci.yaml -- cloudflared tunnel run --token "$CLOUDFLARE_TUNNEL_A1_AGENT_SERVICE_RUN_TOKEN"
elr --print-plan -- codex
elr --no-env -- ls
```

Important shell rule:

```bash
elr -- bash -lc 'echo "$OPENAI_API_KEY"'
```

Do not use:

```bash
elr -- echo "$OPENAI_API_KEY"
```

because the parent shell expands `$OPENAI_API_KEY` before `elr` starts.

## Config Model

Private user/system config stores provider identity and locations.

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

Project config can be public. It names the provider/location and requested vars,
but does not expose region, vault OCIDs, user OCIDs, tenancy OCIDs, or key paths.

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
      - CLOUDFLARE_TUNNEL_A1_AGENT_SERVICE_RUN_TOKEN

local:
  CLI_PROXY_BASE_URL: https://aa.renaissancelab.org/v1
  OPENAI_API_BASE_URL: https://aa.renaissancelab.org/v1
```

Resolution order:

```text
1. /etc/elr/config.yaml
2. ~/.config/elr/config.yaml
3. nearest env.oci.yaml / .env.oci.yaml, walking upward from CWD
4. explicit -e/--env file, if provided
```

Missing requested variables fail closed before the command is executed.

## OCI Requirements

On the Mac or any machine running `elr`, create an OCI API signing key and
`~/.oci/config`.

Oracle Console path:

```text
Profile menu -> User settings -> API keys -> Add API key -> Generate API key pair
```

Save:

```text
~/.oci/oci_api_key.pem
~/.oci/config
```

Expected config snippet:

```ini
[DEFAULT]
user=ocid1.user.oc1...
fingerprint=...
tenancy=ocid1.tenancy.oc1...
region=us-phoenix-1
key_file=/Users/<you>/.oci/oci_api_key.pem
```

Permissions:

```bash
chmod 700 ~/.oci
chmod 600 ~/.oci/config ~/.oci/oci_api_key.pem
```

Required OCI IAM permissions for the API user/group:

```text
inspect secret-family in compartment <compartment>
read secret-bundles in compartment <compartment>
```

The current implementation looks up secrets by name within a configured
compartment/vault and then fetches the current bundle.

## Install / Test Locally

The VPS currently has PyYAML but does not have `pip`, `pytest`, or the OCI SDK.
The repo tests use `unittest` and mock OCI calls.

On Mac:

```bash
cd ~/elr
python3 -m venv .venv
. .venv/bin/activate
pip install -e .
python -m unittest discover -s tests -v
```

Smoke without OCI:

```bash
elr --no-env -- echo ok
elr --print-plan -e examples/env.oci.yaml -- codex
```

Real OCI smoke:

```bash
mkdir -p ~/.config/elr
$EDITOR ~/.config/elr/config.yaml

cd <project-with-env.oci.yaml>
elr --print-plan -- bash -lc 'echo ready'
elr -- bash -lc 'test -n "$GH_TOKEN" && echo "GH_TOKEN loaded"'
```

## Troubleshooting

`elr: no env config found; pass --env or use --no-env`

- Add `env.oci.yaml` to the project, run from a directory under that project, or
  pass `-e path/to/env.oci.yaml`.

`provider 'oci' required ... is not configured`

- Add `~/.config/elr/config.yaml` with `providers.oci.locations.<name>`.
- Ensure the project `imports[].location` matches the private config location.

`OCI provider requires the 'oci' Python package`

- Install the package environment:

```bash
pip install -e .
```

or:

```bash
pip install oci PyYAML
```

`OCI secret not found: GH_TOKEN`

- Confirm the OCI secret name matches `secret_name_template`.
- If secrets are prefixed, configure:

```yaml
secret_name_template: "dev3top/{var}"
```

`OCI list_secrets failed`

- Check IAM policy allows listing/inspecting secrets in the compartment.
- Check `compartment_id`, `vault_id`, and `region`.

`OCI get_secret_bundle failed`

- Check IAM policy allows reading secret bundles.
- Check the secret is active and has a current version.

## Known Gaps / Next Steps

1. Finish OCI setup on the Mac with API signing key and `~/.oci/config`.
2. Create `~/.config/elr/config.yaml` with the real `dev3top` compartment/vault.
3. Create a real project `env.oci.yaml` for `dev3top`.
4. Run the real OCI smoke test.
5. Decide packaging:
   - GitHub Release script/binary
   - npm shim
   - pipx/pip package
6. Add CI once pushed:
   - `python -m unittest discover -s tests -v`
   - basic CLI smoke for `--no-env` and `--print-plan`
7. Consider later provider features:
   - env-based OCI API key auth for portable sandboxes
   - instance principal auth smoke test
   - optional `secret_ids` direct mapping cache
   - GitHub Release installer script

## Security Notes

- `elr` does not write plaintext `.env` files.
- `--print-plan` must never print secret values.
- Project configs should not contain region, tenancy/user OCIDs, vault OCIDs, or
  private key paths.
- Child processes can see exported env vars. This is intentional and matches the
  GitOps/CLI use case; use Agent Vault proxy mode separately when hiding raw
  credentials from AI agents is the priority.
