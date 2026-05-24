#!/usr/bin/env python3
"""Cross-platform ELR runner hook for agent / PI session startup.

Resolves ~/.agents/env.oci.yaml (or AGENT_ENV_FILE) and either loads env into
the current process or execs a child command with the resolved variables.

Usage:
  uv run agent_elr_runner.py --print-plan
  uv run agent_elr_runner.py --load-only
  uv run agent_elr_runner.py -- <pi-agent-command> [args...]

Environment:
  AGENT_ENV_FILE  Override manifest path (default: ~/.agents/env.oci.yaml)
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

from elr.config import ResolvedConfig, load_config
from elr.errors import ElrError
from elr.resolver import resolve_env

DEFAULT_MANIFEST = Path("~/.agents/env.oci.yaml")


def default_manifest_path() -> Path:
    override = os.environ.get("AGENT_ENV_FILE")
    if override:
        return Path(override).expanduser()
    return DEFAULT_MANIFEST.expanduser()


def load_agent_config(manifest: Path | str) -> ResolvedConfig:
    return load_config(str(manifest), include_project=False)


def resolve_agent_env(manifest: Path | str | None = None, *, fetch: bool = True) -> dict[str, str]:
    path = Path(manifest).expanduser() if manifest else default_manifest_path()
    if not path.is_file():
        raise ElrError(f"agent manifest not found: {path}")

    config = load_agent_config(path)
    resolution = resolve_env(config, fetch=fetch)
    return resolution.values


def apply_agent_env(values: dict[str, str]) -> None:
    os.environ.update(values)


def exec_command(command: list[str], env: dict[str, str]) -> None:
    if not command:
        raise ElrError("missing command; use: agent_elr_runner.py [options] -- <command...>")
    if sys.platform == "win32":
        raise SystemExit(subprocess.call(command, env=env))
    os.execvpe(command[0], command, env)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agent_elr_runner",
        description="Load agent env from ELR manifest for PI session startup.",
    )
    parser.add_argument(
        "-e",
        "--env",
        help="agent manifest path (default: ~/.agents/env.oci.yaml or AGENT_ENV_FILE)",
    )
    parser.add_argument(
        "--print-plan",
        action="store_true",
        help="print resolved variable names and sources without fetching secrets",
    )
    parser.add_argument(
        "--load-only",
        action="store_true",
        help="fetch secrets and update os.environ in this process, then exit",
    )
    parser.add_argument(
        "cmd",
        nargs=argparse.REMAINDER,
        help="[--] <command> [args...]; omit with --load-only to bootstrap the current process",
    )
    return parser


def _parse_command(raw: list[str]) -> list[str]:
    if raw and raw[0] == "--":
        raw = raw[1:]
    return raw


def _print_plan(manifest: Path, fetch: bool) -> int:
    config = load_agent_config(manifest)
    resolution = resolve_env(config, fetch=fetch)
    print(f"Agent manifest: {manifest}")
    print("Config files:")
    for path in config.loaded_files:
        print(f"  - {path}")
    print("Variables:")
    if not resolution.plan:
        print("  (none)")
        return 0
    for entry in resolution.plan:
        if entry.source_type == "local":
            print(f"  - {entry.name}: local")
        else:
            print(f"  - {entry.name}: {entry.provider}/{entry.location}")
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        manifest = Path(args.env).expanduser() if args.env else default_manifest_path()
        if not manifest.is_file():
            raise ElrError(f"agent manifest not found: {manifest}")

        if args.print_plan:
            return _print_plan(manifest, fetch=False)

        command = _parse_command(args.cmd)
        values = resolve_agent_env(manifest, fetch=True)

        if args.load_only:
            apply_agent_env(values)
            return 0

        if not command:
            raise ElrError(
                "missing command; use --load-only to bootstrap this process, "
                "or pass a command after --"
            )

        env = os.environ.copy()
        env.update(values)
        exec_command(command, env)
        return 0
    except ElrError as exc:
        print(f"agent_elr_runner: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
