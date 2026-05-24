from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from elr.config import find_project_config, load_config
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
      dev-env:
        compartment_id: comp
        vault_id: vault
        secrets:
          - example-bundle-a
""",
                encoding="utf-8",
            )
            project.write_text(
                f"""
version: 1
imports:
  - {user}
  - provider: oci
    location: dev-env
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
    location: dev-env
    vars: [GH_TOKEN]
""",
                encoding="utf-8",
            )
            with patch("elr.config.default_config_paths", return_value=[project]):
                with self.assertRaises(ConfigError):
                    load_config(str(project))

    def test_project_config_search_stops_at_git_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            outside = Path(tmp)
            (outside / "env.oci.yaml").write_text("version: 1\n", encoding="utf-8")
            repo = outside / "repo"
            nested = repo / "a" / "b"
            nested.mkdir(parents=True)
            (repo / ".git").mkdir()

            self.assertIsNone(find_project_config(nested))

    def test_project_config_search_finds_inside_git_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            nested = repo / "a" / "b"
            nested.mkdir(parents=True)
            (repo / ".git").mkdir()
            project = repo / "env.oci.yaml"
            project.write_text("version: 1\n", encoding="utf-8")

            self.assertEqual(find_project_config(nested), project.resolve())

    def test_project_config_search_walks_to_root_without_git_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            nested = root / "a" / "b"
            nested.mkdir(parents=True)
            project = root / "env.oci.yaml"
            project.write_text("version: 1\n", encoding="utf-8")

            self.assertEqual(find_project_config(nested), project.resolve())

    def test_project_config_search_honors_git_file_marker(self):
        with tempfile.TemporaryDirectory() as tmp:
            outside = Path(tmp)
            (outside / "env.oci.yaml").write_text("version: 1\n", encoding="utf-8")
            repo = outside / "repo"
            nested = repo / "a"
            nested.mkdir(parents=True)
            (repo / ".git").write_text("gitdir: ../.git/worktrees/repo\n", encoding="utf-8")

            self.assertIsNone(find_project_config(nested))


if __name__ == "__main__":
    unittest.main()
