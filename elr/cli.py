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
    _settings_from_spec,
    age_key_present,
    build_run_env,
    load_sops_settings,
    print_config_plan,
    print_shell_source,
    print_sync_status,
    remove_age_key,
    sync_age_key,
    sync_all_age_keys,
)

_SUBCOMMANDS = frozenset({"profile", "sops"})


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:2] == ["profile", "add"]:
        return _profile_add(argv[2:])
    if argv and argv[0] == "sops":
        return _sops(argv[1:])
    if not argv:
        raise SystemExit(_usage())

    parser = argparse.ArgumentParser(
        prog="elr",
        description="Layered env.oci.yaml runner with SOPS bootstrap and OCI imports.",
        add_help=True,
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("-e", "--env", help="explicit env.oci.yaml / config path")
    parser.add_argument("--no-sync", action="store_true", help="do not fetch missing age keys")
    parser.add_argument(
        "--print-plan",
        action="store_true",
        help="print resolved config without fetching secrets or running a command",
    )
    parser.add_argument("cmd", nargs=argparse.REMAINDER, help="command to run")
    args = parser.parse_args(argv)

    try:
        command = _parse_command(args.cmd, require_command=not args.print_plan)
        resolved = load_config(args.env)

        if args.print_plan:
            print_config_plan(resolved)
            plan = resolve_env(resolved, fetch=False)
            print("Import variables:")
            if not plan.plan:
                print("  (none)")
            for entry in plan.plan:
                if entry.source_type == "local":
                    print(f"  - {entry.name}: local")
                else:
                    print(f"  - {entry.name}: {entry.provider}/{entry.location}")
            return 0

        if not args.no_sync and resolved.sops_keys and not age_key_present(resolved.keys_file):
            sync_all_age_keys(resolved)

        env = build_run_env(resolved, fetch_imports=True)
        _exec(command, env)
        return 0
    except ElrError as exc:
        print(f"elr: {exc}", file=sys.stderr)
        return 1


def _usage() -> int:
    print(
        "usage: elr [options] <command...>\n"
        "       elr sops sync [catalog_id]\n"
        "       elr sops source [--sync]\n"
        "       elr sops remove <catalog_id>\n"
        "       elr profile add ...",
        file=sys.stderr,
    )
    return 2


def _sops_sync(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="elr sops sync")
    parser.add_argument("catalog_id", nargs="?", help="sops.keys catalog id (default: sync all)")
    parser.add_argument("--print-plan", action="store_true", help="show resolved sops config")
    parser.add_argument("-e", "--env", help="explicit env.oci.yaml / config path")
    parser.add_argument("--force", action="store_true", help="overwrite existing age key material")
    parser.add_argument("--location", help="override OCI location")
    parser.add_argument("--key", help="override OCI vault object name (sops.keys.*.key)")
    args = parser.parse_args(argv)
    try:
        resolved = load_config(args.env)
        if args.print_plan:
            print_config_plan(resolved)
            return 0

        if args.catalog_id:
            settings, resolved = load_sops_settings(
                explicit_env=args.env,
                catalog_id=args.catalog_id,
                location=args.location,
                vault_key=args.key,
            )
            existed = _catalog_synced(resolved.keys_file, settings.catalog_id) and not args.force
            sync_age_key(settings, resolved, force=args.force)
            print_sync_status(resolved.keys_file, created=not existed, catalog_id=settings.catalog_id)
            return 0

        existed = age_key_present(resolved.keys_file)
        sync_all_age_keys(resolved, force=args.force)
        print_sync_status(resolved.keys_file, created=not existed or args.force)
        return 0
    except ElrError as exc:
        print(f"elr: {exc}", file=sys.stderr)
        return 1


def _catalog_synced(keys_file, catalog_id: str) -> bool:
    from .sops import _catalog_present

    return _catalog_present(keys_file, catalog_id)


def _sops(argv: list[str]) -> int:
    if not argv:
        raise SystemExit(_sops_help())
    if argv[0] == "sync":
        return _sops_sync(argv[1:])
    if argv[0] == "source":
        return _sops_source(argv[1:])
    if argv[0] == "remove":
        return _sops_remove(argv[1:])
    raise SystemExit(_sops_help())


def _sops_help() -> int:
    print(
        "usage: elr sops {sync|source|remove} ...\n"
        "\n"
        "  sync [catalog_id]   fetch age key(s) into sops.keys_file\n"
        "  source [--sync]     print export SOPS_AGE_KEY_FILE=...\n"
        "  remove <catalog_id> remove catalog block from local keys file\n",
        file=sys.stderr,
    )
    return 2


def _sops_source(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="elr sops source")
    parser.add_argument("--sync", action="store_true", help="fetch age keys when keys file is missing")
    parser.add_argument("-e", "--env", help="explicit env.oci.yaml / config path")
    args = parser.parse_args(argv)
    try:
        resolved = load_config(args.env)
        print_shell_source(resolved, sync=args.sync)
        return 0
    except ElrError as exc:
        print(f"elr: {exc}", file=sys.stderr)
        return 1


def _sops_remove(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="elr sops remove")
    parser.add_argument("catalog_id", help="sops.keys catalog id to remove from keys file")
    parser.add_argument("-e", "--env", help="explicit env.oci.yaml / config path")
    args = parser.parse_args(argv)
    try:
        resolved = load_config(args.env)
        path = remove_age_key(resolved, args.catalog_id)
        print(f"Removed catalog {args.catalog_id!r} from: {path}")
        return 0
    except ElrError as exc:
        print(f"elr: {exc}", file=sys.stderr)
        return 1


def _profile_add(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="elr profile add")
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
        raise ElrError("missing command; use: elr [options] <command...>")
    return raw


def _exec(command: list[str], env: dict[str, str]) -> None:
    os.execvpe(command[0], command, env)


if __name__ == "__main__":
    raise SystemExit(main())
