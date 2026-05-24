from __future__ import annotations

import base64
import types
import unittest

from elr.config import ImportSpec
from elr.errors import SecretResolutionError
from elr.providers.oci import OciSecretProvider


class OciProviderTests(unittest.TestCase):
    def test_provider_resolves_requested_vars_from_allowed_dotenv_bundles(self):
        provider = OciSecretProvider(
            {
                "locations": {
                    "dev3top": {
                        "compartment_id": "compartment",
                        "vault_id": "vault",
                        "secrets": ["github-services"],
                    }
                }
            }
        )
        provider._vaults_client = FakeVaultsClient()
        provider._secrets_client = FakeSecretsClient()
        values = provider.resolve_import(
            ImportSpec(
                provider="oci",
                location="dev3top",
                vars=("GH_TOKEN",),
                source=__file__,
            )
        )
        self.assertEqual(values["GH_TOKEN"], "secret-value")
        self.assertNotIn("UNREQUESTED", values)

    def test_provider_fails_when_requested_var_absent(self):
        provider = OciSecretProvider(
            {
                "locations": {
                    "dev3top": {
                        "compartment_id": "compartment",
                        "vault_id": "vault",
                        "secrets": ["github-services"],
                    }
                }
            }
        )
        provider._vaults_client = FakeVaultsClient()
        provider._secrets_client = FakeSecretsClient()

        with self.assertRaises(SecretResolutionError):
            provider.resolve_import(
                ImportSpec(
                    provider="oci",
                    location="dev3top",
                    vars=("MISSING",),
                    source=__file__,
                )
            )

    def test_provider_only_looks_up_configured_bundle_names(self):
        provider = OciSecretProvider(
            {
                "locations": {
                    "dev3top": {
                        "compartment_id": "compartment",
                        "vault_id": "vault",
                        "secrets": ["github-services"],
                    }
                }
            }
        )
        vaults = FakeVaultsClient()
        provider._vaults_client = vaults
        provider._secrets_client = FakeSecretsClient()

        provider.resolve_import(
            ImportSpec(
                provider="oci",
                location="dev3top",
                vars=("GH_TOKEN",),
                source=__file__,
            )
        )
        self.assertEqual(vaults.names, ["github-services"])

    def test_provider_skips_missing_allowed_bundle_names(self):
        provider = OciSecretProvider(
            {
                "locations": {
                    "dev3top": {
                        "compartment_id": "compartment",
                        "vault_id": "vault",
                        "secrets": ["github-services", "agent-service"],
                    }
                }
            }
        )
        vaults = FakeVaultsClient(missing={"agent-service"})
        provider._vaults_client = vaults
        provider._secrets_client = FakeSecretsClient()

        values = provider.resolve_import(
            ImportSpec(
                provider="oci",
                location="dev3top",
                vars=("GH_TOKEN",),
                source=__file__,
            )
        )

        self.assertEqual(values["GH_TOKEN"], "secret-value")
        self.assertEqual(vaults.names, ["github-services", "agent-service"])

    def test_fetch_raw_secret_returns_unparsed_bundle(self):
        provider = OciSecretProvider(
            {
                "locations": {
                    "dev3top": {
                        "compartment_id": "compartment",
                        "vault_id": "vault",
                        "secrets": ["sops-age-key"],
                    }
                }
            }
        )
        provider._vaults_client = FakeVaultsClient()
        provider._secrets_client = FakeSecretsClient(
            payload=b"# created: 2026\nAGE-SECRET-KEY-1abc\n"
        )
        raw = provider.fetch_raw_secret("dev3top", "sops-age-key")
        self.assertIn("AGE-SECRET-KEY-1abc", raw)

    def test_provider_fails_when_requested_var_only_in_missing_bundle(self):
        provider = OciSecretProvider(
            {
                "locations": {
                    "dev3top": {
                        "compartment_id": "compartment",
                        "vault_id": "vault",
                        "secrets": ["agent-service"],
                    }
                }
            }
        )
        provider._vaults_client = FakeVaultsClient(missing={"agent-service"})
        provider._secrets_client = FakeSecretsClient()

        with self.assertRaises(SecretResolutionError) as exc:
            provider.resolve_import(
                ImportSpec(
                    provider="oci",
                    location="dev3top",
                    vars=("AGENT_TOKEN",),
                    source=__file__,
                )
            )
        self.assertIn("AGENT_TOKEN", str(exc.exception))


class FakeVaultsClient:
    def __init__(self, missing=None):
        self.names = []
        self.missing = set(missing or ())

    def list_secrets(self, **_kwargs):
        name = _kwargs["name"]
        self.names.append(name)
        if name in self.missing:
            return types.SimpleNamespace(data=[])
        item = types.SimpleNamespace(secret_name=name, id=f"{name}-id")
        return types.SimpleNamespace(data=[item])


class FakeSecretsClient:
    def __init__(self, payload: bytes | None = None):
        self.payload = payload or b"GH_TOKEN=secret-value\nUNREQUESTED=yes\n"

    def get_secret_bundle(self, **_kwargs):
        content = base64.b64encode(self.payload).decode("ascii")
        bundle_content = types.SimpleNamespace(content=content)
        data = types.SimpleNamespace(secret_bundle_content=bundle_content)
        return types.SimpleNamespace(data=data)


if __name__ == "__main__":
    unittest.main()
