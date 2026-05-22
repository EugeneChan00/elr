from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from elr.config import ImportSpec, ResolvedConfig
from elr.resolver import resolve_env


class FakeProvider:
    def __init__(self, _config):
        pass

    def resolve_import(self, spec):
        return {var: f"value-for-{var}" for var in spec.vars}


class ResolverTests(unittest.TestCase):
    def test_resolves_local_and_secret_values(self):
        config = ResolvedConfig(
            providers={"oci": {"locations": {"dev3top": {}}}},
            local={"LOCAL_ONLY": "yes"},
            imports=[
                ImportSpec(
                    provider="oci",
                    location="dev3top",
                    vars=("GH_TOKEN",),
                    source=Path("env.oci.yaml"),
                )
            ],
        )
        with patch("elr.resolver.OciSecretProvider", FakeProvider):
            result = resolve_env(config)
        self.assertEqual(result.values["LOCAL_ONLY"], "yes")
        self.assertEqual(result.values["GH_TOKEN"], "value-for-GH_TOKEN")

    def test_print_plan_does_not_fetch(self):
        config = ResolvedConfig(
            providers={"oci": {"locations": {"dev3top": {}}}},
            imports=[
                ImportSpec(
                    provider="oci",
                    location="dev3top",
                    vars=("GH_TOKEN",),
                    source=Path("env.oci.yaml"),
                )
            ],
        )
        result = resolve_env(config, fetch=False)
        self.assertEqual(result.values, {})
        self.assertEqual(result.plan[0].name, "GH_TOKEN")


if __name__ == "__main__":
    unittest.main()
