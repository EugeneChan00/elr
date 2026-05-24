from __future__ import annotations

import base64
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from elr import cli
from elr.config import ResolvedConfig, SopsEnvEntry, SopsKeySpec
from elr.errors import ElrError
from elr.providers.oci import OciSecretProvider
from elr.sops import (
    CATALOG_MARKER,
    SopsSettings,
    _remove_catalog_block,
    build_run_env,
    normalize_age_key_content,
    remove_age_key,
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
                            "secrets": ["sops-age-keys"],
                        }
                    }
                }
            )
            provider._vaults_client = FakeVaultsClient()
            provider._secrets_client = FakeSecretsClient(KEYS_TXT.encode())

            with patch("elr.sops.OciSecretProvider", return_value=provider):
                settings = SopsSettings(
                    keys_file=key_path,
                    catalog_id="default",
                    provider="oci",
                    location="dev-env",
                    vault_key="sops-age-keys",
                )
                resolved = ResolvedConfig(
                    providers={
                        "oci": {
                            "locations": {
                                "dev-env": {
                                    "compartment_id": "compartment",
                                    "vault_id": "vault",
                                    "secrets": ["sops-age-keys"],
                                }
                            }
                        }
                    },
                    keys_file=key_path,
                    sops_keys={
                        "default": SopsKeySpec(
                            catalog_id="default",
                            provider="oci",
                            location="dev-env",
                            vault_key="sops-age-keys",
                            source=Path("config.yaml"),
                        )
                    },
                )
                path = sync_age_key(settings, resolved, force=True)

            self.assertEqual(path, key_path.resolve())
            self.assertTrue(key_path.is_file())
            self.assertEqual(oct(key_path.stat().st_mode & 0o777), "0o600")
            text = key_path.read_text(encoding="utf-8")
            self.assertIn(AGE_LINE, text)
            self.assertIn(f"{CATALOG_MARKER} default", text)

    def test_shell_source_requires_existing_file_without_sync(self):
        with tempfile.TemporaryDirectory() as tmp:
            resolved = ResolvedConfig(keys_file=Path(tmp) / "missing.txt")
            with self.assertRaises(ElrError):
                shell_source_lines(resolved, sync=False)

    def test_remove_catalog_block_strips_one_identity(self):
        text = (
            f"{CATALOG_MARKER} default\n# synced\n{AGE_LINE}\n\n"
            f"{CATALOG_MARKER} work\n# synced\nAGE-SECRET-KEY-WORK\n"
        )
        updated = _remove_catalog_block(text, "work")
        self.assertIn(f"{CATALOG_MARKER} default", updated)
        self.assertNotIn(f"{CATALOG_MARKER} work", updated)
        self.assertIn(AGE_LINE, updated)

    def test_remove_age_key_deletes_file_when_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            key_path = Path(tmp) / "keys.txt"
            write_age_key_file(
                key_path,
                f"{CATALOG_MARKER} default\n{AGE_LINE}\n",
            )
            resolved = ResolvedConfig(
                keys_file=key_path,
                sops_keys={
                    "default": SopsKeySpec(
                        catalog_id="default",
                        provider="oci",
                        location="dev-env",
                        vault_key="sops-age-keys",
                        source=Path("config.yaml"),
                    )
                },
            )
            remove_age_key(resolved, "default")
            self.assertFalse(key_path.is_file())


class SopsRunEnvTests(unittest.TestCase):
    def test_build_run_env_merges_local_and_imports(self):
        with tempfile.TemporaryDirectory() as tmp:
            key_path = Path(tmp) / "keys.txt"
            write_age_key_file(key_path, KEYS_TXT)
            resolved = ResolvedConfig(
                keys_file=key_path,
                local={"LOCAL_FLAG": "1"},
            )
            with patch("elr.sops.resolve_env") as resolve_mock:
                resolve_mock.return_value = types.SimpleNamespace(values={"GH_TOKEN": "tok"})
                env = build_run_env(resolved, fetch_imports=True, base_env={"PATH": "/bin"})
            self.assertEqual(env["LOCAL_FLAG"], "1")
            self.assertEqual(env["GH_TOKEN"], "tok")
            self.assertEqual(env["SOPS_AGE_KEY_FILE"], str(key_path))

    def test_build_run_env_decrypts_sops_env_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            key_path = Path(tmp) / "keys.txt"
            env_file = Path(tmp) / ".env.sops"
            env_file.write_text("encrypted\n", encoding="utf-8")
            write_age_key_file(key_path, KEYS_TXT)
            resolved = ResolvedConfig(
                keys_file=key_path,
                sops_env=[
                    SopsEnvEntry(alias="app", path=env_file, layer=2, source=Path("x")),
                ],
            )
            with patch("elr.sops.decrypt_sops_env_file", return_value={"APP_KEY": "secret"}):
                with patch("elr.sops.resolve_env", return_value=types.SimpleNamespace(values={})):
                    env = build_run_env(resolved, fetch_imports=True, base_env={})
            self.assertEqual(env["APP_KEY"], "secret")


class SopsCliTests(unittest.TestCase):
    def test_sops_source_routes_to_printer(self):
        with tempfile.TemporaryDirectory() as tmp:
            key_path = Path(tmp) / "keys.txt"
            write_age_key_file(key_path, KEYS_TXT)
            config_path = Path(tmp) / "env.oci.yaml"
            config_path.write_text(
                f"""
version: 1
providers:
  oci:
    locations:
      dev-env:
        compartment_id: c
        vault_id: v
sops:
  keys_file: {key_path}
  keys:
    default:
      location: dev-env
      key: sops-age-keys
""",
                encoding="utf-8",
            )
            output = __import__("io").StringIO()
            with patch("sys.stdout", output):
                code = cli.main(["sops", "source", "-e", str(config_path)])
            self.assertEqual(code, 0)
            self.assertIn("SOPS_AGE_KEY_FILE", output.getvalue())

    def test_sops_sync_all_routes_to_sync(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "env.oci.yaml"
            config_path.write_text(
                """
version: 1
providers:
  oci:
    locations:
      dev-env:
        compartment_id: c
        vault_id: v
sops:
  keys:
    default:
      location: dev-env
      key: sops-age-keys
""",
                encoding="utf-8",
            )
            with patch("elr.cli.sync_all_age_keys") as sync_mock:
                with patch("elr.cli.age_key_present", return_value=False):
                    code = cli.main(["sops", "sync", "-e", str(config_path)])
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
