from __future__ import annotations

import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from elr import cli


class CliTests(unittest.TestCase):
    def test_no_env_print_plan(self):
        output = io.StringIO()
        with redirect_stdout(output):
            code = cli.main(["--no-env", "--print-plan", "--", "echo", "ok"])
        self.assertEqual(code, 0)
        self.assertIn("No env config loaded", output.getvalue())

    def test_no_env_exec_uses_command(self):
        with patch("elr.cli._exec") as exec_mock:
            code = cli.main(["--no-env", "--", "echo", "ok"])
        self.assertEqual(code, 0)
        exec_mock.assert_called_once()
        self.assertEqual(exec_mock.call_args.args[0], ["echo", "ok"])


if __name__ == "__main__":
    unittest.main()
