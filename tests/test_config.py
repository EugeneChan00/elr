from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from elr.config import load_config
from elr.errors import ConfigError


class ConfigTests(unittest.TestCase):
    def test_loads_imports_local_and_providers(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            user = root / "user.yaml"
            project = root / "env.oci.yaml"
            user.write_text(
                """
version: 1
providers:
  oci:
    auth:
      mode: config_file
    locations:
      dev3top:
        compartment_id: comp
        vault_id: vault
        secret_name_template: "{var}"
""",
                encoding="utf-8",
            )
            project.write_text(
                f"""
version: 1
imports:
  - {user}
  - provider: oci
    location: dev3top
    vars:
      - GH_TOKEN
local:
  CLI_PROXY_BASE_URL: https://aa.example/v1
""",
                encoding="utf-8",
            )

            config = load_config(str(project))
            self.assertIn("oci", config.providers)
            self.assertEqual(config.local["CLI_PROXY_BASE_URL"], "https://aa.example/v1")
            self.assertEqual(config.imports[0].vars, ("GH_TOKEN",))

    def test_missing_provider_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "env.oci.yaml"
            project.write_text(
                """
version: 1
imports:
  - provider: oci
    location: dev3top
    vars: [GH_TOKEN]
""",
                encoding="utf-8",
            )
            with self.assertRaises(ConfigError):
                load_config(str(project))


if __name__ == "__main__":
    unittest.main()
