from __future__ import annotations

import argparse
import os
import sys

from . import __version__
from .config import load_config
from .errors import ElrError
from .profile import add_profile
from .resolver import resolve_env


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="elr",
        description="Resolve env vars from OCI Vault and exec a command.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("-e", "--env", help="explicit project env config file")
    parser.add_argument("--no-env", action="store_true", help="run command without loading env config")
    parser.add_argument(
        "--print-plan",
        action="store_true",
        help="print resolved variable names and sources without secret values",
    )
    parser.add_argument(
        "cmd",
        nargs=argparse.REMAINDER,
        help="[--] <command> [args...]; -- is optional unless the command itself starts with '-'",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:2] == ["profile", "add"]:
        return _profile_add(argv[2:])

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


def _profile_add(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="elr profile add",
        description="Create or update the local ELR OCI profile config.",
    )
    parser.add_argument("--from-env-file", help="read ELR_OCI_* values from a dotenv file")
    parser.add_argument("--force", action="store_true", help="replace an existing location")
    parser.add_argument(
        "--write-oci-config",
        action="store_true",
        help="also write ~/.oci/config and the private key from ELR_OCI_PRIVATE_KEY_B64",
    )
    args = parser.parse_args(argv)
    try:
        path, oci_config_path = add_profile(
            from_env_file=args.from_env_file,
            force=args.force,
            write_oci_config=args.write_oci_config,
        )
        if oci_config_path:
            print(f"Wrote OCI config: {oci_config_path}")
        print(f"Wrote ELR OCI profile config: {path}")
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
