from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from elr import cli


class CliTests(unittest.TestCase):
    def test_version(self):
        output = io.StringIO()
        with self.assertRaises(SystemExit) as exc:
            with redirect_stdout(output):
                cli.main(["--version"])
        self.assertEqual(exc.exception.code, 0)
        self.assertIn("elr 0.3.0", output.getvalue())

    def test_print_plan_without_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "env.oci.yaml"
            config_path.write_text("version: 1\nlocal:\n  FOO: bar\n", encoding="utf-8")
            output = io.StringIO()
            with redirect_stdout(output):
                code = cli.main(["-e", str(config_path), "--print-plan"])
            self.assertEqual(code, 0)
            self.assertIn("Config files:", output.getvalue())
            self.assertIn("FOO", output.getvalue())

    def test_default_run_exec_uses_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "env.oci.yaml"
            config_path.write_text("version: 1\nlocal:\n  FOO: bar\n", encoding="utf-8")
            with patch("elr.cli._exec") as exec_mock:
                with patch("elr.cli.build_run_env", return_value={"FOO": "bar"}):
                    code = cli.main(["-e", str(config_path), "echo", "ok"])
            self.assertEqual(code, 0)
            exec_mock.assert_called_once()
            self.assertEqual(exec_mock.call_args.args[0], ["echo", "ok"])

    def test_default_run_strips_leading_double_dash(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "env.oci.yaml"
            config_path.write_text("version: 1\nlocal:\n  FOO: bar\n", encoding="utf-8")
            with patch("elr.cli._exec") as exec_mock:
                with patch("elr.cli.build_run_env", return_value={"FOO": "bar"}):
                    code = cli.main(["-e", str(config_path), "--", "echo", "ok"])
            self.assertEqual(code, 0)
            exec_mock.assert_called_once()
            self.assertEqual(exec_mock.call_args.args[0], ["echo", "ok"])

    def test_profile_add_routes_to_profile_writer(self):
        output = io.StringIO()
        with patch("elr.cli.add_profile", return_value=("/tmp/config.yaml", None)) as add_mock:
            with redirect_stdout(output):
                code = cli.main(["profile", "add", "--from-env-file", "elr.env", "--force"])
        self.assertEqual(code, 0)
        add_mock.assert_called_once_with(
            from_env_file="elr.env",
            force=True,
            write_oci_config=False,
        )


if __name__ == "__main__":
    unittest.main()
