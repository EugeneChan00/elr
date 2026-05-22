from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any, Callable
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
    "ELR_OCI_COMPARTMENT_ID": "OCI compartment OCID",
    "ELR_OCI_VAULT_ID": "OCI vault OCID",
    "ELR_OCI_SECRETS": "OCI secret bundles (comma-separated)",
}


def add_profile(
    *,
    from_env_file: str | None = None,
    force: bool = False,
    config_path: Path = USER_CONFIG,
    environ: Mapping[str, str] | None = None,
    stdin=None,
    prompt: Callable[[str], str] = input,
) -> Path:
    values = _collect_values(from_env_file=from_env_file, environ=environ)
    _prompt_for_missing(values, stdin=stdin, prompt=prompt)
    missing = [name for name in REQUIRED if not values.get(name)]
    if missing:
        raise ConfigError(f"missing required profile values: {', '.join(missing)}")

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
    return path


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
