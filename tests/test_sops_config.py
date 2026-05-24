from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from elr.sops_config import load_sops_config


class SopsConfigTests(unittest.TestCase):
    def test_layered_merge_and_repo_sync_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            system = root / "system.yaml"
            user = root / "user.yaml"
            project = root / "sops.oci.yaml"

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
        secrets: [sops-age-key, sops-age-key-work]
sops:
  defaults:
    age_key_dir: ~/.config/sops/age
  keys:
    default:
      location: dev-env
      secret: sops-age-key
    work:
      location: dev-env
      secret: sops-age-key-work
""",
                encoding="utf-8",
            )
            user.write_text("version: 1\n", encoding="utf-8")
            project.write_text(
                """
version: 1
sync:
  key: work
  env_file: .env.sops
""",
                encoding="utf-8",
            )

            with patch(
                "elr.sops_config.default_sops_config_paths",
                return_value=[system, user, project],
            ):
                resolved = load_sops_config()

            self.assertEqual(resolved.active_key, "work")
            self.assertEqual(resolved.keys["work"].secret, "sops-age-key-work")
            self.assertEqual(resolved.keys["work"].env_file, Path(".env.sops"))
            self.assertTrue(str(resolved.keys["work"].age_key_file).endswith("/work.txt"))
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
        secrets: [sops-age-key]
sops:
  secret: sops-age-key
  location: dev-env
  age_key_file: ~/.config/sops/age/keys.txt
""",
                encoding="utf-8",
            )
            with patch("elr.sops_config.default_sops_config_paths", return_value=[user]):
                resolved = load_sops_config()
            self.assertEqual(resolved.active_key, "default")
            self.assertEqual(resolved.keys["default"].secret, "sops-age-key")


if __name__ == "__main__":
    unittest.main()
