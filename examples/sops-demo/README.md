# Local SOPS demo

End-to-end test of ELR decrypt without Oracle Vault.

## One-time setup

```bash
cd examples/sops-demo
./setup.sh
```

This creates:

| File | Purpose |
|------|---------|
| `age-key.txt` | Private age key (keep local, do not commit) |
| `keys.txt` | ELR master file (`# elr-catalog: default` block) |
| `demo.env.plain` | Cleartext template |
| `demo.env.sops` | Encrypted dotenv (committed as demo) |

## Encrypt / re-encrypt

```bash
# edit demo.env.plain, then:
PUB=$(grep '^# public key:' age-key.txt | awk '{print $4}')
sops encrypt -a "$PUB" --input-type dotenv --output-type dotenv demo.env.plain > demo.env.sops
```

## Decrypt via ELR

```bash
elr -e env.oci.yaml --no-sync --print-plan
elr -e env.oci.yaml --no-sync bash -lc 'echo APP=$APP_NAME DB=$DB_PASSWORD DEMO=$DEMO_MODE'
```

## Decrypt via sops directly

```bash
SOPS_AGE_KEY_FILE=./keys.txt sops -d --input-type dotenv --output-type dotenv demo.env.sops
```
