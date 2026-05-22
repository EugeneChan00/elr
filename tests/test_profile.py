from __future__ import annotations

import io
import os
import stat
import tempfile
import unittest
from pathlib import Path

import yaml

from elr.errors import ConfigError
from elr.profile import add_profile


class ProfileTests(unittest.TestCase):
    def test_creates_config_with_private_permissions(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / ".config" / "elr" / "config.yaml"
            add_profile(config_path=config, environ=_env(), stdin=NonTty())

            data = yaml.safe_load(config.read_text(encoding="utf-8"))
            location = data["providers"]["oci"]["locations"]["dev-env"]
            self.assertEqual(location["secrets"], ["github-services", "openai-services"])
            self.assertEqual(_mode(config.parent), 0o700)
            self.assertEqual(_mode(config), 0o600)

    def test_preserves_unrelated_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "config.yaml"
            config.write_text(
                """
version: 1
providers:
  other:
    enabled: true
  oci:
    locations:
      old:
        compartment_id: old-comp
        vault_id: old-vault
        secrets: [old-services]
""",
                encoding="utf-8",
            )

            add_profile(config_path=config, environ=_env(), stdin=NonTty())

            data = yaml.safe_load(config.read_text(encoding="utf-8"))
            self.assertTrue(data["providers"]["other"]["enabled"])
            self.assertIn("old", data["providers"]["oci"]["locations"])
            self.assertIn("dev-env", data["providers"]["oci"]["locations"])

    def test_existing_location_fails_without_force(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "config.yaml"
            add_profile(config_path=config, environ=_env(), stdin=NonTty())

            with self.assertRaises(ConfigError):
                add_profile(config_path=config, environ=_env(), stdin=NonTty())

    def test_force_updates_existing_location(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "config.yaml"
            add_profile(config_path=config, environ=_env(), stdin=NonTty())
            env = _env(ELR_OCI_SECRETS="cloudflare-services")

            add_profile(config_path=config, environ=env, stdin=NonTty(), force=True)

            data = yaml.safe_load(config.read_text(encoding="utf-8"))
            location = data["providers"]["oci"]["locations"]["dev-env"]
            self.assertEqual(location["secrets"], ["cloudflare-services"])

    def test_shell_env_wins_over_env_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "config.yaml"
            env_file = Path(tmp) / "elr.env"
            env_file.write_text(
                """
ELR_OCI_REGION=us-ashburn-1
ELR_OCI_COMPARTMENT_ID=file-comp
ELR_OCI_VAULT_ID=file-vault
ELR_OCI_SECRETS=file-services
""",
                encoding="utf-8",
            )

            add_profile(
                config_path=config,
                from_env_file=str(env_file),
                environ=_env(ELR_OCI_REGION="us-phoenix-1"),
                stdin=NonTty(),
            )

            data = yaml.safe_load(config.read_text(encoding="utf-8"))
            self.assertEqual(data["providers"]["oci"]["auth"]["region"], "us-phoenix-1")
            location = data["providers"]["oci"]["locations"]["dev-env"]
            self.assertEqual(location["compartment_id"], "compartment")

    def test_reads_missing_values_from_env_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "config.yaml"
            env_file = Path(tmp) / "elr.env"
            env_file.write_text(
                """
export ELR_OCI_REGION='us-ashburn-1'
ELR_OCI_COMPARTMENT_ID="file-comp"
ELR_OCI_VAULT_ID=file-vault
ELR_OCI_SECRETS=file-services
""",
                encoding="utf-8",
            )

            add_profile(config_path=config, from_env_file=str(env_file), environ={}, stdin=NonTty())

            data = yaml.safe_load(config.read_text(encoding="utf-8"))
            self.assertEqual(data["providers"]["oci"]["auth"]["region"], "us-ashburn-1")
            location = data["providers"]["oci"]["locations"]["dev-env"]
            self.assertEqual(location["secrets"], ["file-services"])

    def test_prompts_for_missing_values_on_tty(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "config.yaml"
            answers = iter(["us-phoenix-1", "compartment", "vault", "github-services"])

            add_profile(
                config_path=config,
                environ={},
                stdin=Tty(),
                prompt=lambda _message: next(answers),
            )

            data = yaml.safe_load(config.read_text(encoding="utf-8"))
            self.assertEqual(data["providers"]["oci"]["auth"]["region"], "us-phoenix-1")

    def test_missing_values_fail_non_interactively(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "config.yaml"

            with self.assertRaises(ConfigError) as exc:
                add_profile(config_path=config, environ={}, stdin=NonTty())

            self.assertIn("ELR_OCI_REGION", str(exc.exception))


class NonTty(io.StringIO):
    def isatty(self):
        return False


class Tty(io.StringIO):
    def isatty(self):
        return True


def _env(**overrides):
    values = {
        "ELR_OCI_REGION": "us-phoenix-1",
        "ELR_OCI_COMPARTMENT_ID": "compartment",
        "ELR_OCI_VAULT_ID": "vault",
        "ELR_OCI_SECRETS": "github-services,openai-services",
    }
    values.update(overrides)
    return values


def _mode(path: Path) -> int:
    return stat.S_IMODE(os.stat(path).st_mode)


if __name__ == "__main__":
    unittest.main()
