from __future__ import annotations

import base64
from typing import Any

from elr.config import ImportSpec
from elr.errors import ConfigError, SecretResolutionError


class OciSecretProvider:
    def __init__(self, provider_config: dict[str, Any]):
        self.provider_config = provider_config
        self.auth = provider_config.get("auth", {}) or {}
        self.locations = provider_config.get("locations", {}) or {}
        self._secrets_client = None
        self._vaults_client = None
        self._secret_cache: dict[tuple[str, str, str], str] = {}

    def resolve_import(self, spec: ImportSpec) -> dict[str, str]:
        location = self.locations.get(spec.location)
        if not isinstance(location, dict):
            raise ConfigError(f"OCI location {spec.location!r} is not configured")

        resolved: dict[str, str] = {}
        for var in spec.vars:
            secret_name = _secret_name_for_var(var, location)
            secret_id = self._lookup_secret_id(location, secret_name)
            resolved[var] = self._fetch_secret_value(secret_id, var)
        return resolved

    def _clients(self):
        if self._secrets_client and self._vaults_client:
            return self._secrets_client, self._vaults_client

        try:
            import oci  # type: ignore
        except ImportError as exc:
            raise ConfigError(
                "OCI provider requires the 'oci' Python package. Install with: pip install oci"
            ) from exc

        mode = self.auth.get("mode", "config_file")
        region = self.auth.get("region")

        if mode == "instance_principal":
            signer = oci.auth.signers.InstancePrincipalsSecurityTokenSigner()
            config = {"region": region or signer.region}
            self._secrets_client = oci.secrets.SecretsClient(config, signer=signer)
            self._vaults_client = oci.vault.VaultsClient(config, signer=signer)
        elif mode == "config_file":
            config_file = self.auth.get("config_file", "~/.oci/config")
            profile = self.auth.get("profile", "DEFAULT")
            config = oci.config.from_file(config_file, profile)
            if region:
                config["region"] = region
            self._secrets_client = oci.secrets.SecretsClient(config)
            self._vaults_client = oci.vault.VaultsClient(config)
        else:
            raise ConfigError(f"unsupported OCI auth mode: {mode}")

        return self._secrets_client, self._vaults_client

    def _lookup_secret_id(self, location: dict[str, Any], secret_name: str) -> str:
        explicit = location.get("secret_ids", {})
        if isinstance(explicit, dict) and secret_name in explicit:
            return str(explicit[secret_name])

        compartment_id = location.get("compartment_id")
        vault_id = location.get("vault_id")
        if not compartment_id:
            raise ConfigError("OCI location requires compartment_id for name lookup")
        if not vault_id:
            raise ConfigError("OCI location requires vault_id for name lookup")

        cache_key = (str(compartment_id), str(vault_id), secret_name)
        if cache_key in self._secret_cache:
            return self._secret_cache[cache_key]

        _, vaults_client = self._clients()
        try:
            response = vaults_client.list_secrets(
                compartment_id=compartment_id,
                vault_id=vault_id,
                name=secret_name,
                lifecycle_state="ACTIVE",
                limit=50,
            )
        except Exception as exc:
            raise SecretResolutionError(f"OCI list_secrets failed for {secret_name}: {exc}") from exc

        items = list(getattr(response, "data", []) or [])
        matches = [item for item in items if getattr(item, "secret_name", None) == secret_name]
        if not matches:
            raise SecretResolutionError(f"OCI secret not found: {secret_name}")
        if len(matches) > 1:
            raise SecretResolutionError(f"OCI secret name is ambiguous: {secret_name}")

        secret_id = getattr(matches[0], "id", None)
        if not secret_id:
            raise SecretResolutionError(f"OCI secret {secret_name} did not include an id")
        self._secret_cache[cache_key] = str(secret_id)
        return str(secret_id)

    def _fetch_secret_value(self, secret_id: str, var_name: str) -> str:
        secrets_client, _ = self._clients()
        try:
            response = secrets_client.get_secret_bundle(secret_id=secret_id, stage="CURRENT")
        except Exception as exc:
            raise SecretResolutionError(f"OCI get_secret_bundle failed for {var_name}: {exc}") from exc

        content = getattr(getattr(response, "data", None), "secret_bundle_content", None)
        encoded = getattr(content, "content", None)
        if not encoded:
            raise SecretResolutionError(f"OCI secret bundle for {var_name} has no content")
        try:
            return base64.b64decode(encoded).decode("utf-8").rstrip("\n")
        except Exception as exc:
            raise SecretResolutionError(f"OCI secret content for {var_name} is not valid base64 text") from exc


def _secret_name_for_var(var: str, location: dict[str, Any]) -> str:
    template = str(location.get("secret_name_template", "{var}"))
    try:
        return template.format(var=var)
    except Exception as exc:
        raise ConfigError(f"invalid secret_name_template {template!r}: {exc}") from exc
