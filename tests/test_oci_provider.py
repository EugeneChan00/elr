from __future__ import annotations

import base64
import types
import unittest

from elr.config import ImportSpec
from elr.providers.oci import OciSecretProvider, _secret_name_for_var


class OciProviderTests(unittest.TestCase):
    def test_secret_name_template(self):
        self.assertEqual(
            _secret_name_for_var("GH_TOKEN", {"secret_name_template": "dev3top/{var}"}),
            "dev3top/GH_TOKEN",
        )

    def test_provider_with_fake_clients(self):
        provider = OciSecretProvider(
            {
                "locations": {
                    "dev3top": {
                        "compartment_id": "compartment",
                        "vault_id": "vault",
                        "secret_name_template": "{var}",
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


class FakeVaultsClient:
    def list_secrets(self, **_kwargs):
        item = types.SimpleNamespace(secret_name="GH_TOKEN", id="secret-id")
        return types.SimpleNamespace(data=[item])


class FakeSecretsClient:
    def get_secret_bundle(self, **_kwargs):
        content = base64.b64encode(b"secret-value").decode("ascii")
        bundle_content = types.SimpleNamespace(content=content)
        data = types.SimpleNamespace(secret_bundle_content=bundle_content)
        return types.SimpleNamespace(data=data)


if __name__ == "__main__":
    unittest.main()
