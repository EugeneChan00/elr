from __future__ import annotations

import argparse
import os
import sys

from . import __version__
from .config import load_config
from .errors import ElrError
from .profile import add_profile
from .resolver import resolve_env
from .sops import (
    age_key_present,
    exec_with_sops,
    load_sops_settings,
    print_shell_source,
    print_sync_status,
    sync_age_key,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="elr",
        description="OCI-backed bootstrap for SOPS age keys and optional env exec.",
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
    if argv[:2] == ["age", "sync"]:
        return _age_sync(argv[2:])
    if argv and argv[0] == "sops":
        return _sops(argv[1:])

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


def _age_sync(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="elr age sync",
        description="Fetch the SOPS age private key from OCI Vault and write keys.txt locally.",
    )
    parser.add_argument("--force", action="store_true", help="overwrite an existing age key file")
    parser.add_argument("--location", help="OCI location name (default: dev-env or config sops.location)")
    parser.add_argument("--secret", help="OCI vault secret name (default: sops-age-key)")
    parser.add_argument("--age-key-file", help="destination keys.txt path")
    args = parser.parse_args(argv)
    try:
        settings, config = load_sops_settings(
            age_key_file=args.age_key_file,
            location=args.location,
            secret=args.secret,
        )
        existed = age_key_present(settings.age_key_file)
        path = sync_age_key(settings, config, force=args.force)
        print_sync_status(path, created=not existed or args.force)
        return 0
    except ElrError as exc:
        print(f"elr: {exc}", file=sys.stderr)
        return 1


def _sops(argv: list[str]) -> int:
    if not argv:
        raise SystemExit(_sops_help())

    if argv[0] == "source":
        return _sops_source(argv[1:])
    if argv[0] == "store":
        return _sops_store(argv[1:])
    if argv[0] in ("exec", "run") or (argv[0] == "--" and len(argv) > 1):
        if argv[0] == "--":
            argv = argv[1:]
        else:
            argv = argv[1:]
        return _sops_exec(argv)

    if argv[0] == "--":
        return _sops_exec(argv[1:])

    raise SystemExit(_sops_help())


def _sops_help() -> int:
    print(
        "usage: elr sops {source|store|exec} ...\n"
        "       elr sops -- <command> [args...]\n"
        "\n"
        "  source   print shell exports for SOPS_AGE_KEY_FILE (use: eval \"$(elr sops source)\")\n"
        "  store    fetch age key from OCI Vault and write keys.txt (alias for elr age sync)\n"
        "  exec     sync age key and run: sops exec-env .env.sops -- <command>\n",
        file=sys.stderr,
    )
    return 2


def _sops_source(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="elr sops source",
        description="Print shell exports for SOPS_AGE_KEY_FILE.",
    )
    parser.add_argument(
        "--sync",
        action="store_true",
        help="fetch the age key from OCI when the local keys file is missing",
    )
    parser.add_argument("--location", help="OCI location name")
    parser.add_argument("--secret", help="OCI vault secret name")
    parser.add_argument("--age-key-file", help="age keys.txt path")
    args = parser.parse_args(argv)
    try:
        settings, config = load_sops_settings(
            age_key_file=args.age_key_file,
            location=args.location,
            secret=args.secret,
        )
        print_shell_source(settings, sync=args.sync, config=config)
        return 0
    except ElrError as exc:
        print(f"elr: {exc}", file=sys.stderr)
        return 1


def _sops_store(argv: list[str]) -> int:
    return _age_sync(argv)


def _sops_exec(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="elr sops exec",
        description="Sync the age key and run a command via sops exec-env.",
    )
    parser.add_argument("--no-sync", action="store_true", help="do not fetch the age key from OCI")
    parser.add_argument("--env-file", default=None, help="encrypted dotenv file (default: .env.sops)")
    parser.add_argument("--location", help="OCI location name")
    parser.add_argument("--secret", help="OCI vault secret name")
    parser.add_argument("--age-key-file", help="age keys.txt path")
    parser.add_argument("cmd", nargs=argparse.REMAINDER, help="command after --")
    args = parser.parse_args(argv)
    try:
        command = _parse_command(args.cmd, require_command=True)
        settings, config = load_sops_settings(
            age_key_file=args.age_key_file,
            location=args.location,
            secret=args.secret,
            env_file=args.env_file,
        )
        return exec_with_sops(
            settings,
            config,
            command,
            sync=not args.no_sync,
        )
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
