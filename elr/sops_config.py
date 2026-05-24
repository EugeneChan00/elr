"""Backward-compatible re-exports; prefer elr.config.load_config."""

from __future__ import annotations

from .config import (
    DEFAULT_KEYS_FILE,
    DEFAULT_OCI_LOCATION,
    DEFAULT_OCI_PROVIDER,
    DEFAULT_VAULT_KEY,
    ResolvedConfig,
    SopsEnvEntry,
    SopsKeySpec,
    load_config as load_sops_config,
)

DEFAULT_KEY_ID = "default"

ResolvedSopsConfig = ResolvedConfig

__all__ = [
    "DEFAULT_KEY_ID",
    "DEFAULT_KEYS_FILE",
    "DEFAULT_OCI_LOCATION",
    "DEFAULT_OCI_PROVIDER",
    "DEFAULT_VAULT_KEY",
    "ResolvedConfig",
    "ResolvedSopsConfig",
    "SopsEnvEntry",
    "SopsKeySpec",
    "load_sops_config",
]
