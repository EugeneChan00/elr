from __future__ import annotations

import base64
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from elr import cli
from elr.sops_config import ResolvedSopsConfig
from elr.errors import ElrError
from elr.providers.oci import OciSecretProvider
from elr.sops import (
    SopsSettings,
    normalize_age_key_content,
    shell_source_lines,
    sync_age_key,
    write_age_key_file,
)

AGE_LINE = "AGE-SECRET-KEY-1Q2W3E4R5T6Y7U8I9O0PAAAAABBBBBCCCCCDDDDDEEEEFFFFGG"
KEYS_TXT = f"# created: 2026-05-24\n# public key: age1example\n{AGE_LINE}\n"


class SopsNormalizeTests(unittest.TestCase):
    def test_accepts_full_keys_txt(self):
        self.assertEqual(normalize_age_key_content(KEYS_TXT), KEYS_TXT.strip())

    def test_accepts_single_secret_line(self):
        normalized = normalize_age_key_content(AGE_LINE)
        self.assertIn(AGE_LINE, normalized)
        self.assertTrue(normalized.startswith("# synced by elr"))

    def test_accepts_dotenv_sops_age_key(self):
        normalized = normalize_age_key_content(f"SOPS_AGE_KEY={AGE_LINE}\n")
        self.assertIn(AGE_LINE, normalized)


class SopsSyncTests(unittest.TestCase):
    def test_sync_writes_private_key_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            key_path = Path(tmp) / "keys.txt"
            provider = OciSecretProvider(
                {
                    "locations": {
                        "dev-env": {
                            "compartment_id": "compartment",
                            "vault_id": "vault",
                            "secrets": ["sops-age-key"],
                        }
                    }
                }
            )
            provider._vaults_client = FakeVaultsClient()
            provider._secrets_client = FakeSecretsClient(KEYS_TXT.encode())

            with patch("elr.sops.OciSecretProvider", return_value=provider):
                settings = SopsSettings(
                    age_key_file=key_path,
                    env_file=Path(".env.sops"),
                    provider="oci",
                    location="dev-env",
                    secret="sops-age-key",
                )
                resolved = ResolvedSopsConfig(
                    providers={
                        "oci": {
                            "locations": {
                                "dev-env": {
                                    "compartment_id": "compartment",
                                    "vault_id": "vault",
                                    "secrets": ["sops-age-key"],
                                }
                            }
                        }
                    },
                    keys={},
                    active_key="default",
                )
                path = sync_age_key(settings, resolved, force=True)

            self.assertEqual(path, key_path.resolve())
            self.assertTrue(key_path.is_file())
            self.assertEqual(oct(key_path.stat().st_mode & 0o777), "0o600")
            self.assertIn(AGE_LINE, key_path.read_text(encoding="utf-8"))

    def test_shell_source_requires_existing_file_without_sync(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings = SopsSettings(
                age_key_file=Path(tmp) / "missing.txt",
                env_file=Path(".env.sops"),
                provider="oci",
                location="dev-env",
                secret="sops-age-key",
            )
            with self.assertRaises(ElrError):
                shell_source_lines(settings, sync=False)


class SopsCliTests(unittest.TestCase):
    def test_sops_source_routes_to_printer(self):
        with tempfile.TemporaryDirectory() as tmp:
            key_path = Path(tmp) / "keys.txt"
            write_age_key_file(key_path, KEYS_TXT)
            settings = SopsSettings(
                age_key_file=key_path,
                env_file=Path(".env.sops"),
                provider="oci",
                location="dev-env",
                secret="sops-age-key",
            )
            resolved = ResolvedSopsConfig(providers={"oci": {}}, keys={}, active_key="default")
            with patch("elr.cli.load_sops_settings", return_value=(settings, resolved)):
                with patch("elr.cli.print_shell_source") as source_mock:
                    code = cli.main(["sops", "source"])
            self.assertEqual(code, 0)
            source_mock.assert_called_once_with(settings, sync=False, resolved=resolved)

    def test_sops_sync_routes_to_sync(self):
        settings = SopsSettings(
            age_key_file=Path("/tmp/keys.txt"),
            env_file=Path(".env.sops"),
            provider="oci",
            location="dev-env",
            secret="sops-age-key",
        )
        resolved = ResolvedSopsConfig(providers={"oci": {}}, keys={}, active_key="default")
        with patch("elr.cli.load_sops_settings", return_value=(settings, resolved)):
            with patch("elr.cli.age_key_present", return_value=False):
                with patch("elr.cli.sync_age_key", return_value=settings.age_key_file) as sync_mock:
                    code = cli.main(["sops", "sync"])
        self.assertEqual(code, 0)
        sync_mock.assert_called_once()


class FakeVaultsClient:
    def list_secrets(self, **_kwargs):
        name = _kwargs["name"]
        item = types.SimpleNamespace(secret_name=name, id=f"{name}-id")
        return types.SimpleNamespace(data=[item])


class FakeSecretsClient:
    def __init__(self, payload: bytes):
        self.payload = payload

    def get_secret_bundle(self, **_kwargs):
        content = base64.b64encode(self.payload).decode("ascii")
        bundle_content = types.SimpleNamespace(content=content)
        data = types.SimpleNamespace(secret_bundle_content=bundle_content)
        return types.SimpleNamespace(data=data)


if __name__ == "__main__":
    unittest.main()
