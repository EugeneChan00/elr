from __future__ import annotations

import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import agent_elr_runner as runner  # noqa: E402


class AgentElrRunnerTests(unittest.TestCase):
    def test_default_manifest_path_uses_agent_env_file(self):
        with patch.dict("os.environ", {"AGENT_ENV_FILE": "/tmp/custom/env.oci.yaml"}, clear=False):
            self.assertEqual(runner.default_manifest_path(), Path("/tmp/custom/env.oci.yaml"))

    def test_default_manifest_path_falls_back_to_home(self):
        with patch.dict("os.environ", {}, clear=True):
            self.assertEqual(
                runner.default_manifest_path(),
                Path("~/.agents/env.oci.yaml").expanduser(),
            )

    def test_load_only_updates_process_env(self):
        with tempfile.TemporaryDirectory() as tmp:
            manifest = Path(tmp) / "env.oci.yaml"
            manifest.write_text("version: 1\nlocal:\n  FOO: bar\n", encoding="utf-8")

            with patch.dict("os.environ", {}, clear=True):
                code = runner.main(["-e", str(manifest), "--load-only"])
                self.assertEqual(code, 0)
                self.assertEqual(runner.os.environ.get("FOO"), "bar")

    def test_load_only_uses_build_run_env(self):
        with tempfile.TemporaryDirectory() as tmp:
            manifest = Path(tmp) / "env.oci.yaml"
            manifest.write_text("version: 1\nlocal:\n  FOO: bar\n", encoding="utf-8")

            with patch("agent_elr_runner.build_run_env", return_value={"FOO": "from-build-run-env"}) as env_mock:
                with patch.dict("os.environ", {}, clear=True):
                    code = runner.main(["-e", str(manifest), "--load-only"])
                    self.assertEqual(code, 0)
                    env_mock.assert_called_once()
                    self.assertEqual(runner.os.environ.get("FOO"), "from-build-run-env")

    def test_print_plan_without_fetch(self):
        yaml_text = """
version: 1
imports:
  - provider: oci
    location: dev-env
    vars:
      - CLI_PROXY_URL
      - CLI_PROXY_API_KEY
local: {}
"""
        with tempfile.TemporaryDirectory() as tmp:
            manifest = Path(tmp) / "env.oci.yaml"
            manifest.write_text(yaml_text, encoding="utf-8")
            user_config = Path(tmp) / "config.yaml"
            user_config.write_text(
                """
version: 1
providers:
  oci:
    auth:
      mode: config_file
      region: [REDACTED]  # pragma: allowlist secret
      config_file: ~/.oci/config
      profile: ELR
    locations:
      dev-env:
        compartment_id: ocid1.compartment
        vault_id: ocid1.vault
        secrets:
          - openai-services
""",
                encoding="utf-8",
            )

            output = io.StringIO()
            with patch("elr.config.USER_CONFIG", user_config):
                with patch("elr.config.SYSTEM_CONFIG", Path("/nonexistent")):
                    with patch("elr.config.find_project_config", return_value=None):
                        with patch("sys.stdout", output):
                            code = runner.main(["-e", str(manifest), "--print-plan"])

            self.assertEqual(code, 0)
            text = output.getvalue()
            self.assertIn("CLI_PROXY_URL", text)
            self.assertIn("CLI_PROXY_API_KEY", text)

    def test_missing_manifest_exits_with_error(self):
        stderr = io.StringIO()
        with patch("sys.stderr", stderr):
            code = runner.main(["-e", "/nonexistent/env.oci.yaml", "--load-only"])
        self.assertEqual(code, 1)
        self.assertIn("agent manifest not found", stderr.getvalue())

    def test_exec_command_on_windows_uses_subprocess(self):
        env = {"PATH": "/usr/bin"}
        with patch("agent_elr_runner.sys.platform", "win32"):
            with patch("agent_elr_runner.subprocess.call", return_value=0) as call_mock:
                with self.assertRaises(SystemExit) as exc:
                    runner.exec_command(["echo", "ok"], env)
        self.assertEqual(exc.exception.code, 0)
        call_mock.assert_called_once_with(["echo", "ok"], env=env)


if __name__ == "__main__":
    unittest.main()
