from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any, Callable
import base64
import binascii
import os
import sys

import yaml

from .config import USER_CONFIG, load_yaml_file
from .dotenv import parse_dotenv_file
from .errors import ConfigError

DEFAULTS = {
    "ELR_OCI_LOCATION": "dev-env",
    "ELR_OCI_AUTH_MODE": "config_file",
    "ELR_OCI_CONFIG_FILE": "~/.oci/config",
    "ELR_OCI_PROFILE": "ELR",
    "ELR_OCI_KEY_FILE": "~/.oci/elr_api.pem",
}
REQUIRED = (
    "ELR_OCI_REGION",
    "ELR_OCI_COMPARTMENT_ID",
    "ELR_OCI_VAULT_ID",
    "ELR_OCI_SECRETS",
)
PROMPTS = {
    "ELR_OCI_LOCATION": "ELR location",
    "ELR_OCI_AUTH_MODE": "OCI auth mode",
    "ELR_OCI_REGION": "OCI region",
    "ELR_OCI_CONFIG_FILE": "OCI config file",
    "ELR_OCI_PROFILE": "OCI profile",
    "ELR_OCI_KEY_FILE": "OCI private key file",
    "ELR_OCI_COMPARTMENT_ID": "OCI compartment OCID",
    "ELR_OCI_VAULT_ID": "OCI vault OCID",
    "ELR_OCI_SECRETS": "OCI secret bundles (comma-separated)",
}
OCI_CONFIG_REQUIRED = (
    "ELR_OCI_USER_ID",
    "ELR_OCI_FINGERPRINT",
    "ELR_OCI_TENANCY_ID",
    "ELR_OCI_REGION",
    "ELR_OCI_PRIVATE_KEY_B64",
)


def add_profile(
    *,
    from_env_file: str | None = None,
    force: bool = False,
    write_oci_config: bool = False,
    config_path: Path = USER_CONFIG,
    environ: Mapping[str, str] | None = None,
    stdin=None,
    prompt: Callable[[str], str] = input,
) -> tuple[Path, Path | None]:
    values = _collect_values(from_env_file=from_env_file, environ=environ)
    _prompt_for_missing(values, stdin=stdin, prompt=prompt)
    missing = [name for name in REQUIRED if not values.get(name)]
    if missing:
        raise ConfigError(f"missing required profile values: {', '.join(missing)}")
    if write_oci_config:
        oci_missing = [name for name in OCI_CONFIG_REQUIRED if not values.get(name)]
        if oci_missing:
            raise ConfigError(f"missing required OCI config values: {', '.join(oci_missing)}")

    secrets = _split_secrets(values["ELR_OCI_SECRETS"])
    if not secrets:
        raise ConfigError("missing required profile values: ELR_OCI_SECRETS")

    path = config_path.expanduser()
    data = load_yaml_file(path) if path.exists() else {"version": 1}
    if not isinstance(data, dict):
        raise ConfigError(f"config root must be a mapping: {path}")

    providers = _mapping_at(data, "providers")
    oci = _mapping_at(providers, "oci")
    auth = _mapping_at(oci, "auth")
    auth.update(
        {
            "mode": values["ELR_OCI_AUTH_MODE"],
            "region": values["ELR_OCI_REGION"],
            "config_file": values["ELR_OCI_CONFIG_FILE"],
            "profile": values["ELR_OCI_PROFILE"],
        }
    )

    locations = _mapping_at(oci, "locations")
    location_name = values["ELR_OCI_LOCATION"]
    if location_name in locations and not force:
        raise ConfigError(f"OCI profile {location_name!r} already exists; pass --force to replace")
    locations[location_name] = {
        "compartment_id": values["ELR_OCI_COMPARTMENT_ID"],
        "vault_id": values["ELR_OCI_VAULT_ID"],
        "secrets": secrets,
    }

    _write_private_yaml(path, data)
    oci_config_path = _write_oci_config(values) if write_oci_config else None
    return path, oci_config_path


def _collect_values(
    *,
    from_env_file: str | None,
    environ: Mapping[str, str] | None,
) -> dict[str, str]:
    values = dict(DEFAULTS)
    if from_env_file:
        values.update(parse_dotenv_file(from_env_file))
    env = os.environ if environ is None else environ
    for key, value in env.items():
        if key.startswith("ELR_OCI_"):
            values[key] = value
    return values


def _prompt_for_missing(
    values: dict[str, str],
    *,
    stdin,
    prompt: Callable[[str], str],
) -> None:
    input_stream = sys.stdin if stdin is None else stdin
    if not input_stream.isatty():
        return
    for name in (*DEFAULTS.keys(), *REQUIRED):
        current = values.get(name, "")
        if current:
            continue
        values[name] = prompt(f"{PROMPTS[name]}: ").strip()


def _split_secrets(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _mapping_at(parent: dict[str, Any], key: str) -> dict[str, Any]:
    value = parent.get(key)
    if value is None:
        value = {}
        parent[key] = value
    if not isinstance(value, dict):
        raise ConfigError(f"{key} must be a mapping")
    return value


def _write_private_yaml(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    text = yaml.safe_dump(data, sort_keys=False)
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    fd = os.open(path, flags, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(text)
    os.chmod(path, 0o600)


def _write_oci_config(values: dict[str, str]) -> Path:
    key_path = Path(values["ELR_OCI_KEY_FILE"]).expanduser()
    config_path = Path(values["ELR_OCI_CONFIG_FILE"]).expanduser()
    key_path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(key_path.parent, 0o700)
    _write_private_bytes(key_path, _decode_private_key(values["ELR_OCI_PRIVATE_KEY_B64"]))

    text = "\n".join(
        [
            f"[{values['ELR_OCI_PROFILE']}]",
            f"user={values['ELR_OCI_USER_ID']}",
            f"fingerprint={values['ELR_OCI_FINGERPRINT']}",
            f"tenancy={values['ELR_OCI_TENANCY_ID']}",
            f"region={values['ELR_OCI_REGION']}",
            f"key_file={key_path}",
            "",
        ]
    )
    _write_private_text(config_path, text)
    return config_path


def _decode_private_key(value: str) -> bytes:
    compact = "".join(value.split())
    try:
        decoded = base64.b64decode(compact, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ConfigError("ELR_OCI_PRIVATE_KEY_B64 is not valid base64") from exc
    if b"PRIVATE KEY" not in decoded:
        raise ConfigError("ELR_OCI_PRIVATE_KEY_B64 did not decode to a PEM private key")
    return decoded


def _write_private_text(path: Path, text: str) -> None:
    _write_private_bytes(path, text.encode("utf-8"))


def _write_private_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    fd = os.open(path, flags, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(data)
    os.chmod(path, 0o600)
