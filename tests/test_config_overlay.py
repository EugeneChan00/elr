"""Overlay precedence: etc → user → repo → explicit (-e)."""

from __future__ import annotations

import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from elr.config import (
    LAYER_ETC,
    LAYER_EXPLICIT,
    LAYER_REPO,
    LAYER_USER,
    ImportSpec,
    ResolvedConfig,
    SopsEnvEntry,
    load_config,
)
from elr.sops import build_run_env


def _write(path: Path, text: str) -> Path:
    path.write_text(text.strip() + "\n", encoding="utf-8")
    return path


def _minimal_providers(location: str = "dev-env") -> str:
    return f"""
providers:
  oci:
    auth:
      mode: config_file
    locations:
      {location}:
        compartment_id: comp-base
        vault_id: vault-base
        secrets: [sops-age-key-example]
"""


class ConfigOverlayPrecedenceTests(unittest.TestCase):
    def _load(
        self,
        *paths: Path,
        explicit: str | None = None,
        layers: list[int] | None = None,
    ):
        if layers is None:
            default_layers = [LAYER_ETC, LAYER_USER, LAYER_REPO, LAYER_EXPLICIT]
            layers = default_layers[: len(paths)]
        layer_by_path = {p.resolve(): layer for p, layer in zip(paths, layers)}
        if explicit:
            layer_by_path[Path(explicit).expanduser().resolve()] = LAYER_EXPLICIT

        def fake_layer_for_path(path: Path) -> int:
            return layer_by_path.get(path.expanduser().resolve(), LAYER_REPO)

        with (
            patch("elr.config.default_config_paths", return_value=list(paths)),
            patch("elr.config.layer_for_path", side_effect=fake_layer_for_path),
        ):
            return load_config(explicit)

    def test_load_order_four_layers_etc_user_repo_explicit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            etc = _write(root / "etc.yaml", "local:\n  LAYER: etc")
            user = _write(root / "user.yaml", "local:\n  LAYER: user")
            repo = _write(root / "env.oci.yaml", "local:\n  LAYER: repo")
            explicit = _write(root / "override.yaml", "local:\n  LAYER: explicit")

            resolved = self._load(etc, user, repo, explicit, explicit=str(explicit))
            self.assertEqual(
                [p.name for p in resolved.loaded_files],
                ["etc.yaml", "user.yaml", "env.oci.yaml", "override.yaml"],
            )
            self.assertEqual(resolved.local["LAYER"], "explicit")

    def test_locations_deep_merge_compartment_and_vault(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            etc = _write(
                root / "etc.yaml",
                """
providers:
  oci:
    locations:
      dev-env:
        compartment_id: comp-etc
        vault_id: vault-etc
""",
            )
            user = _write(
                root / "user.yaml",
                """
providers:
  oci:
    locations:
      dev-env:
        vault_id: vault-user
""",
            )
            repo = _write(
                root / "repo.yaml",
                """
providers:
  oci:
    locations:
      dev-env:
        compartment_id: comp-repo
""",
            )
            resolved = self._load(etc, user, repo)
            loc = resolved.providers["oci"]["locations"]["dev-env"]
            self.assertEqual(loc["compartment_id"], "comp-repo")
            self.assertEqual(loc["vault_id"], "vault-user")

    def test_locations_secrets_union_dedupe_preserves_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            etc = _write(
                root / "etc.yaml",
                """
providers:
  oci:
    locations:
      dev-env:
        secrets: [alpha, beta, alpha]
""",
            )
            user = _write(
                root / "user.yaml",
                """
providers:
  oci:
    locations:
      dev-env:
        secrets: [beta, gamma]
""",
            )
            repo = _write(
                root / "repo.yaml",
                """
providers:
  oci:
    locations:
      dev-env:
        secrets: [gamma, delta]
""",
            )
            resolved = self._load(etc, user, repo)
            secrets = resolved.providers["oci"]["locations"]["dev-env"]["secrets"]
            self.assertEqual(secrets, ["alpha", "beta", "gamma", "delta"])

    def test_sops_keys_deep_merge_per_catalog_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            etc = _write(
                root / "etc.yaml",
                _minimal_providers()
                + """
sops:
  keys:
    default:
      provider: oci
      location: dev-env
      key: key-etc
    work:
      provider: oci
      location: dev-env
      key: work-etc
""",
            )
            user = _write(
                root / "user.yaml",
                """
sops:
  keys:
    default:
      key: key-user
""",
            )
            repo = _write(
                root / "repo.yaml",
                """
sops:
  keys:
    default:
      location: dev-env
      key: key-repo
    my-repo:
      provider: oci
      location: dev-env
      key: key-my-repo
""",
            )
            resolved = self._load(etc, user, repo)
            default = resolved.sops_keys["default"]
            self.assertEqual(default.provider, "oci")
            self.assertEqual(default.location, "dev-env")
            self.assertEqual(default.vault_key, "key-repo")
            self.assertEqual(resolved.sops_keys["work"].vault_key, "work-etc")
            self.assertEqual(resolved.sops_keys["my-repo"].vault_key, "key-my-repo")

    def test_sops_env_union_by_alias_later_layer_wins(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            etc = _write(
                root / "etc.yaml",
                _minimal_providers()
                + """
sops:
  env:
    app: ./etc/.env.sops
    shared: ./etc/shared.sops
""",
            )
            user = _write(
                root / "user.yaml",
                """
sops:
  env:
    shared: ./user/shared.sops
""",
            )
            repo = _write(
                root / "repo.yaml",
                """
sops:
  env:
    deploy: ./deploy/.env.sops
""",
            )
            resolved = self._load(etc, user, repo)
            by_alias = {e.alias: e for e in resolved.sops_env}
            self.assertEqual(by_alias["app"].path, (root / "etc" / ".env.sops").resolve())
            self.assertEqual(by_alias["shared"].path, (root / "user" / "shared.sops").resolve())
            self.assertEqual(by_alias["shared"].layer, LAYER_USER)
            self.assertEqual(by_alias["deploy"].path, (root / "deploy" / ".env.sops").resolve())
            self.assertEqual({e.alias for e in resolved.sops_env}, {"app", "shared", "deploy"})

    def test_sops_env_explicit_layer_overrides_alias(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = _write(
                root / "repo.yaml",
                _minimal_providers()
                + """
sops:
  env:
    app: ./repo/.env.sops
""",
            )
            explicit = _write(
                root / "override.yaml",
                """
sops:
  env:
    app: ./override/.env.sops
""",
            )
            resolved = self._load(repo, explicit, explicit=str(explicit), layers=[LAYER_REPO, LAYER_EXPLICIT])
            entry = next(e for e in resolved.sops_env if e.alias == "app")
            self.assertEqual(entry.path, (root / "override" / ".env.sops").resolve())
            self.assertEqual(entry.layer, LAYER_EXPLICIT)

    def test_sops_keys_file_scalar_replace(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            etc = _write(
                root / "etc.yaml",
                _minimal_providers()
                + """
sops:
  keys_file: /etc/sops/keys.txt
""",
            )
            user = _write(
                root / "user.yaml",
                """
sops:
  keys_file: ~/.config/sops/age/keys-user.txt
""",
            )
            repo = _write(
                root / "repo.yaml",
                """
sops:
  keys_file: ./repo-keys.txt
""",
            )
            resolved = self._load(etc, user, repo)
            self.assertEqual(resolved.keys_file, (root / "repo-keys.txt").resolve())

    def test_imports_append_across_layers(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            etc = _write(
                root / "etc.yaml",
                _minimal_providers()
                + """
imports:
  - provider: oci
    location: dev-env
    vars: [ETC_VAR]
""",
            )
            user = _write(
                root / "user.yaml",
                """
imports:
  - provider: oci
    location: dev-env
    vars: [USER_VAR]
""",
            )
            repo = _write(
                root / "repo.yaml",
                """
imports:
  - provider: oci
    location: dev-env
    vars: [REPO_VAR]
""",
            )
            resolved = self._load(etc, user, repo)
            self.assertEqual(len(resolved.imports), 3)
            self.assertEqual(
                [spec.vars[0] for spec in resolved.imports],
                ["ETC_VAR", "USER_VAR", "REPO_VAR"],
            )
            self.assertTrue(all(isinstance(spec, ImportSpec) for spec in resolved.imports))

    def test_local_per_key_replace(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            etc = _write(
                root / "etc.yaml",
                """
local:
  SHARED: etc-value
  ETC_ONLY: 1
""",
            )
            user = _write(
                root / "user.yaml",
                """
local:
  SHARED: user-value
  USER_ONLY: 2
""",
            )
            repo = _write(
                root / "repo.yaml",
                """
local:
  SHARED: repo-value
  REPO_ONLY: 3
""",
            )
            resolved = self._load(etc, user, repo)
            self.assertEqual(resolved.local["SHARED"], "repo-value")
            self.assertEqual(resolved.local["ETC_ONLY"], "1")
            self.assertEqual(resolved.local["USER_ONLY"], "2")
            self.assertEqual(resolved.local["REPO_ONLY"], "3")

    def test_build_run_env_decrypts_all_sops_env_aliases(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            key_path = root / "keys.txt"
            key_path.write_text("AGE-SECRET-KEY-1\n", encoding="utf-8")
            app_env = root / "app.sops"
            deploy_env = root / "deploy.sops"
            app_env.write_text("app\n", encoding="utf-8")
            deploy_env.write_text("deploy\n", encoding="utf-8")

            resolved = ResolvedConfig(
                keys_file=key_path,
                sops_env=[
                    SopsEnvEntry(alias="app", path=app_env, layer=LAYER_ETC, source=root / "etc.yaml"),
                    SopsEnvEntry(alias="deploy", path=deploy_env, layer=LAYER_REPO, source=root / "repo.yaml"),
                ],
            )

            def fake_decrypt(path: Path, env: dict) -> dict[str, str]:
                if path == app_env:
                    return {"APP_KEY": "from-app"}
                if path == deploy_env:
                    return {"DEPLOY_KEY": "from-deploy"}
                return {}

            with patch("elr.sops.decrypt_sops_env_file", side_effect=fake_decrypt):
                with patch("elr.sops.resolve_env", return_value=types.SimpleNamespace(values={})):
                    env = build_run_env(resolved, fetch_imports=True, base_env={})

            self.assertEqual(env["APP_KEY"], "from-app")
            self.assertEqual(env["DEPLOY_KEY"], "from-deploy")

    def test_auth_and_location_fields_deep_merge_when_omitted_in_later_layer(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            etc = _write(
                root / "etc.yaml",
                """
providers:
  oci:
    auth:
      mode: config_file
      profile: DEFAULT
    locations:
      dev-env:
        compartment_id: comp-etc
        vault_id: vault-etc
        secrets: [alpha, beta]
""",
            )
            repo = _write(
                root / "repo.yaml",
                """
providers:
  oci:
    auth:
      mode: instance_principal
    locations:
      dev-env:
        compartment_id: comp-repo
""",
            )
            resolved = self._load(etc, repo, layers=[LAYER_ETC, LAYER_REPO])
            auth = resolved.providers["oci"]["auth"]
            loc = resolved.providers["oci"]["locations"]["dev-env"]
            self.assertEqual(auth["mode"], "instance_principal")
            self.assertEqual(auth["profile"], "DEFAULT")
            self.assertEqual(loc["compartment_id"], "comp-repo")
            self.assertEqual(loc["vault_id"], "vault-etc")
            self.assertEqual(loc["secrets"], ["alpha", "beta"])

    def test_separate_location_names_merge_independently(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            etc = _write(
                root / "etc.yaml",
                """
providers:
  oci:
    locations:
      dev-env:
        compartment_id: comp-dev
        secrets: [a]
      prod-env:
        compartment_id: comp-prod
        vault_id: vault-prod
        secrets: [x]
""",
            )
            repo = _write(
                root / "repo.yaml",
                """
providers:
  oci:
    locations:
      dev-env:
        vault_id: vault-dev
        secrets: [b]
      staging-env:
        compartment_id: comp-staging
        secrets: [s]
""",
            )
            resolved = self._load(etc, repo, layers=[LAYER_ETC, LAYER_REPO])
            locations = resolved.providers["oci"]["locations"]
            self.assertEqual(locations["dev-env"]["compartment_id"], "comp-dev")
            self.assertEqual(locations["dev-env"]["vault_id"], "vault-dev")
            self.assertEqual(locations["dev-env"]["secrets"], ["a", "b"])
            self.assertEqual(locations["prod-env"]["vault_id"], "vault-prod")
            self.assertEqual(locations["staging-env"]["compartment_id"], "comp-staging")

    def test_sops_env_sorted_by_layer_then_alias(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            etc = _write(
                root / "etc.yaml",
                _minimal_providers()
                + """
sops:
  env:
    zebra: ./z.sops
    alpha: ./a.sops
""",
            )
            repo = _write(
                root / "repo.yaml",
                """
sops:
  env:
    beta: ./b.sops
""",
            )
            resolved = self._load(etc, repo, layers=[LAYER_ETC, LAYER_REPO])
            self.assertEqual(
                [(e.layer, e.alias) for e in resolved.sops_env],
                [(LAYER_ETC, "alpha"), (LAYER_ETC, "zebra"), (LAYER_REPO, "beta")],
            )


if __name__ == "__main__":
    unittest.main()
