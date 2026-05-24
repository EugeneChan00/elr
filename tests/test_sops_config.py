from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from elr.config import load_config
from elr.sops_config import load_sops_config


class SopsConfigTests(unittest.TestCase):
    def test_layered_merge_sops_keys_and_env(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            system = root / "system.yaml"
            user = root / "user.yaml"
            project = root / "env.oci.yaml"

            system.write_text(
                """
version: 1
providers:
  oci:
    auth:
      mode: config_file
    locations:
      dev-env:
        compartment_id: comp
        vault_id: vault
        secrets: [github-services, sops-age-keys]
sops:
  keys_file: ~/.config/sops/age/keys.txt
  keys:
    default:
      location: dev-env
      key: sops-age-keys
    work:
      location: dev-env
      key: sops-age-key-work
  env:
    app: .env.sops
""",
                encoding="utf-8",
            )
            user.write_text("version: 1\n", encoding="utf-8")
            project.write_text(
                """
version: 1
sops:
  env:
    deploy: ./deploy/.env.sops
  keys:
    my-repo:
      location: dev-env
      key: sops-age-key-my-repo
""",
                encoding="utf-8",
            )

            with patch("elr.config.default_config_paths", return_value=[system, user, project]):
                resolved = load_sops_config()

            self.assertEqual(resolved.sops_keys["default"].vault_key, "sops-age-keys")
            self.assertEqual(resolved.sops_keys["work"].vault_key, "sops-age-key-work")
            self.assertEqual(resolved.sops_keys["my-repo"].vault_key, "sops-age-key-my-repo")
            aliases = {entry.alias for entry in resolved.sops_env}
            self.assertEqual(aliases, {"app", "deploy"})
            self.assertEqual(len(resolved.loaded_files), 3)

    def test_legacy_flat_sops_maps_to_default_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            user = Path(tmp) / "config.yaml"
            user.write_text(
                """
version: 1
providers:
  oci:
    locations:
      dev-env:
        compartment_id: c
        vault_id: v
        secrets: [sops-age-keys]
sops:
  secret: sops-age-keys
  location: dev-env
  keys_file: ~/.config/sops/age/keys.txt
""",
                encoding="utf-8",
            )
            with patch("elr.config.default_config_paths", return_value=[user]):
                resolved = load_sops_config()
            self.assertIn("default", resolved.sops_keys)
            self.assertEqual(resolved.sops_keys["default"].vault_key, "sops-age-keys")
            self.assertTrue(any("deprecated" in w for w in resolved.warnings))

    def test_deprecated_secret_alias_on_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            user = Path(tmp) / "config.yaml"
            user.write_text(
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
      secret: old-name
""",
                encoding="utf-8",
            )
            with patch("elr.config.default_config_paths", return_value=[user]):
                resolved = load_sops_config()
            self.assertEqual(resolved.sops_keys["default"].vault_key, "old-name")
            self.assertTrue(any("deprecated" in w for w in resolved.warnings))


class ConfigMergeTests(unittest.TestCase):
    def test_locations_secrets_union_across_layers(self):
        with tempfile.TemporaryDirectory() as tmp:
            etc = Path(tmp) / "etc.yaml"
            user = Path(tmp) / "user.yaml"
            repo = Path(tmp) / "env.oci.yaml"
            etc.write_text(
                """
providers:
  oci:
    locations:
      dev-env:
        compartment_id: comp-etc
        vault_id: vault-etc
        secrets: [alpha, beta]
""",
                encoding="utf-8",
            )
            user.write_text(
                """
providers:
  oci:
    locations:
      dev-env:
        secrets: [beta, gamma]
""",
                encoding="utf-8",
            )
            repo.write_text(
                """
providers:
  oci:
    locations:
      dev-env:
        compartment_id: comp-repo
        secrets: [delta]
""",
                encoding="utf-8",
            )
            with patch("elr.config.default_config_paths", return_value=[etc, user, repo]):
                resolved = load_config()
            loc = resolved.providers["oci"]["locations"]["dev-env"]
            self.assertEqual(loc["compartment_id"], "comp-repo")
            self.assertEqual(loc["vault_id"], "vault-etc")
            self.assertEqual(loc["secrets"], ["alpha", "beta", "gamma", "delta"])


if __name__ == "__main__":
    unittest.main()
