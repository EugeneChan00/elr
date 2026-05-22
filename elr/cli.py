from __future__ import annotations

import argparse
import os
import sys

from .config import load_config
from .errors import ElrError
from .resolver import resolve_env


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="elr",
        description="Resolve env vars from OCI Vault and exec a command.",
    )
    parser.add_argument("-e", "--env", help="explicit project env config file")
    parser.add_argument("--no-env", action="store_true", help="run command without loading env config")
    parser.add_argument(
        "--print-plan",
        action="store_true",
        help="print resolved variable names and sources without secret values",
    )
    parser.add_argument("cmd", nargs=argparse.REMAINDER, help="-- <command> [args...]")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        command = _parse_command(args.cmd, require_command=not args.print_plan)

        if args.no_env:
            if args.print_plan:
                print("No env config loaded (--no-env).")
                return 0
            _exec(command, os.environ.copy())
            return 0

        config = load_config(args.env)
        resolution = resolve_env(config, fetch=not args.print_plan)

        if args.print_plan:
            _print_plan(config.loaded_files, resolution.plan)
            return 0

        env = os.environ.copy()
        env.update(resolution.values)
        _exec(command, env)
        return 0
    except ElrError as exc:
        print(f"elr: {exc}", file=sys.stderr)
        return 1


def _parse_command(raw: list[str], require_command: bool) -> list[str]:
    if raw and raw[0] == "--":
        raw = raw[1:]
    if require_command and not raw:
        raise ElrError("missing command; use: elr [options] -- <command...>")
    return raw


def _print_plan(files, entries) -> None:
    print("Config files:")
    for path in files:
        print(f"  - {path}")
    print("Variables:")
    if not entries:
        print("  (none)")
        return
    for entry in entries:
        if entry.source_type == "local":
            print(f"  - {entry.name}: local")
        else:
            print(f"  - {entry.name}: {entry.provider}/{entry.location}")


def _exec(command: list[str], env: dict[str, str]) -> None:
    os.execvpe(command[0], command, env)


if __name__ == "__main__":
    raise SystemExit(main())
