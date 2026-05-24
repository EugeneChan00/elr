from __future__ import annotations

import os
import re
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .config import ResolvedConfig, SopsKeySpec, load_config
from .dotenv import parse_dotenv_text
from .errors import ConfigError, ElrError
from .providers.oci import OciSecretProvider
from .resolver import resolve_env

CATALOG_MARKER = "# elr-catalog:"
AGE_SECRET_PATTERN = re.compile(r"AGE-SECRET-KEY-[A-Z0-9]+")


@dataclass(frozen=True)
class SopsSettings:
    keys_file: Path
    catalog_id: str
    provider: str
    location: str
    vault_key: str


def load_sops_settings(
    *,
    explicit_env: str | None = None,
    include_project: bool = True,
    catalog_id: str | None = None,
    keys_file: str | None = None,
    location: str | None = None,
    vault_key: str | None = None,
    provider: str | None = None,
) -> tuple[SopsSettings, ResolvedConfig]:
    resolved = load_config(explicit_env, include_project=include_project)
    if not resolved.sops_keys:
        raise ConfigError("no sops.keys defined in config")

    selected = catalog_id
    if selected is None:
        if len(resolved.sops_keys) == 1:
            selected = next(iter(resolved.sops_keys))
        else:
            raise ConfigError(
                "multiple sops.keys defined; pass catalog id: "
                + ", ".join(sorted(resolved.sops_keys))
            )

    if selected not in resolved.sops_keys:
        raise ConfigError(
            f"sops catalog {selected!r} is not defined; known: {', '.join(sorted(resolved.sops_keys))}"
        )

    spec = resolved.sops_keys[selected]
    settings = _settings_from_spec(resolved.keys_file, spec)
    if provider:
        settings = _replace(settings, provider=provider)
    if location:
        settings = _replace(settings, location=location)
    if vault_key:
        settings = _replace(settings, vault_key=vault_key)
    if keys_file:
        settings = _replace(settings, keys_file=Path(keys_file).expanduser().resolve())
    return settings, resolved


def _settings_from_spec(keys_file: Path, spec: SopsKeySpec) -> SopsSettings:
    return SopsSettings(
        keys_file=keys_file,
        catalog_id=spec.catalog_id,
        provider=spec.provider,
        location=spec.location,
        vault_key=spec.vault_key,
    )


def _replace(settings: SopsSettings, **kwargs) -> SopsSettings:
    data = {
        "keys_file": settings.keys_file,
        "catalog_id": settings.catalog_id,
        "provider": settings.provider,
        "location": settings.location,
        "vault_key": settings.vault_key,
    }
    data.update(kwargs)
    return SopsSettings(**data)


def age_key_present(path: Path) -> bool:
    return path.is_file() and path.stat().st_size > 0


def sync_age_key(
    settings: SopsSettings,
    resolved: ResolvedConfig,
    *,
    force: bool = False,
) -> Path:
    if age_key_present(settings.keys_file) and not force and _catalog_present(settings.keys_file, settings.catalog_id):
        return settings.keys_file

    provider_config = resolved.providers.get(settings.provider)
    if not isinstance(provider_config, dict):
        raise ConfigError(f"provider {settings.provider!r} is not configured")

    provider = OciSecretProvider(provider_config)
    raw = provider.fetch_raw_secret(settings.location, settings.vault_key)
    content = normalize_age_key_content(raw)
    tagged = _tag_catalog_content(settings.catalog_id, content)
    merged = _merge_catalog_block(settings.keys_file, settings.catalog_id, tagged, force=force)
    write_age_key_file(settings.keys_file, merged)
    return settings.keys_file


def sync_all_age_keys(resolved: ResolvedConfig, *, force: bool = False) -> Path:
    if not resolved.sops_keys:
        raise ConfigError("no sops.keys defined in config")
    keys_file = resolved.keys_file
    for spec in resolved.sops_keys.values():
        settings = _settings_from_spec(keys_file, spec)
        sync_age_key(settings, resolved, force=force)
    return keys_file


def remove_age_key(resolved: ResolvedConfig, catalog_id: str) -> Path:
    if catalog_id not in resolved.sops_keys:
        raise ConfigError(
            f"sops catalog {catalog_id!r} is not defined; known: {', '.join(sorted(resolved.sops_keys))}"
        )
    keys_file = resolved.keys_file
    if not keys_file.is_file():
        raise ElrError(f"age key file not found: {keys_file}")
    updated = _remove_catalog_block(keys_file.read_text(encoding="utf-8"), catalog_id)
    if updated.strip():
        write_age_key_file(keys_file, updated.rstrip("\n") + "\n")
    else:
        keys_file.unlink(missing_ok=True)
    return keys_file


def write_age_key_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    text = content if content.endswith("\n") else f"{content}\n"
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    fd = os.open(path, flags, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(text)
    os.chmod(path, 0o600)


def normalize_age_key_content(text: str) -> str:
    stripped = text.strip()
    if not stripped:
        raise ElrError("age key payload is empty")
    if "AGE-SECRET-KEY-" not in stripped:
        raise ElrError("age key payload does not contain AGE-SECRET-KEY-")
    if stripped.startswith("#") or "\n# " in stripped or stripped.startswith("# created:"):
        return stripped
    lines = [line.strip() for line in stripped.splitlines() if line.strip()]
    for line in lines:
        if line.startswith("SOPS_AGE_KEY="):
            return _minimal_keys_txt(line.split("=", 1)[1].strip())
        if line.startswith("AGE-SECRET-KEY-"):
            return _minimal_keys_txt(line)
    raise ElrError("age key payload format is not recognized")


def _minimal_keys_txt(secret_line: str) -> str:
    return f"# synced by elr\n{secret_line}"


def _tag_catalog_content(catalog_id: str, content: str) -> str:
    lines = content.strip().splitlines()
    body = [line for line in lines if not line.startswith(CATALOG_MARKER)]
    return "\n".join([f"{CATALOG_MARKER} {catalog_id}", *body])


def _catalog_present(path: Path, catalog_id: str) -> bool:
    if not path.is_file():
        return False
    marker = f"{CATALOG_MARKER} {catalog_id}"
    return marker in path.read_text(encoding="utf-8")


def _split_catalog_blocks(text: str) -> list[tuple[str | None, str]]:
    blocks: list[tuple[str | None, str]] = []
    current_id: str | None = None
    current_lines: list[str] = []
    for line in text.splitlines():
        if line.startswith(CATALOG_MARKER):
            if current_id is not None or current_lines:
                blocks.append((current_id, "\n".join(current_lines).strip()))
            current_id = line[len(CATALOG_MARKER) :].strip()
            current_lines = []
            continue
        current_lines.append(line)
    if current_id is not None or current_lines:
        blocks.append((current_id, "\n".join(current_lines).strip()))
    return blocks


def _merge_catalog_block(path: Path, catalog_id: str, tagged_content: str, *, force: bool) -> str:
    existing = path.read_text(encoding="utf-8") if path.is_file() else ""
    updated = _remove_catalog_block(existing, catalog_id).strip()
    parts = [part for part in [updated, tagged_content.strip()] if part]
    return "\n\n".join(parts) + "\n"


def _remove_catalog_block(text: str, catalog_id: str) -> str:
    kept: list[str] = []
    for cid, body in _split_catalog_blocks(text):
        if cid == catalog_id:
            continue
        if cid:
            block = f"{CATALOG_MARKER} {cid}"
            if body:
                block = f"{block}\n{body}"
            kept.append(block.strip())
        elif body.strip():
            kept.append(body.strip())
    if not kept:
        return ""
    return "\n\n".join(kept) + "\n"


def shell_source_lines(resolved: ResolvedConfig, *, sync: bool = False) -> str:
    path = resolved.keys_file
    if sync:
        sync_all_age_keys(resolved)
    elif not age_key_present(path):
        raise ElrError(
            f"age key file not found: {path}; run `elr sops sync` or `elr sops source --sync`"
        )
    quoted = shlex.quote(str(path))
    return f"export SOPS_AGE_KEY_FILE={quoted}\n"


def decrypt_sops_env_file(path: Path, env: dict[str, str]) -> dict[str, str]:
    if not path.is_file():
        raise ElrError(f"encrypted env file not found: {path}")
    command = [
        "sops",
        "-d",
        "--input-type",
        "dotenv",
        "--output-type",
        "dotenv",
        str(path),
    ]
    completed = subprocess.run(command, env=env, capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise ElrError(f"sops decrypt failed for {path}: {detail}")
    return parse_dotenv_text(completed.stdout, source=path)


def build_run_env(
    resolved: ResolvedConfig,
    *,
    fetch_imports: bool = True,
    base_env: dict[str, str] | None = None,
) -> dict[str, str]:
    env = dict(base_env or os.environ)
    env["SOPS_AGE_KEY_FILE"] = str(resolved.keys_file)

    if age_key_present(resolved.keys_file):
        for entry in resolved.sops_env:
            env.update(decrypt_sops_env_file(entry.path, env))

    if fetch_imports:
        resolution = resolve_env(resolved, fetch=True)
        for key, value in resolution.values.items():
            env[key] = value

    for key, value in resolved.local.items():
        env[key] = value

    return env


def print_shell_source(resolved: ResolvedConfig, *, sync: bool) -> None:
    print(shell_source_lines(resolved, sync=sync), end="")


def print_sync_status(path: Path, *, created: bool, catalog_id: str | None = None) -> None:
    label = f" ({catalog_id})" if catalog_id else ""
    action = "Wrote" if created else "Age key already present at"
    print(f"{action}{label}: {path}")


def print_config_plan(resolved: ResolvedConfig) -> None:
    print("Config files:")
    for path in resolved.loaded_files:
        print(f"  - {path}")
    if resolved.warnings:
        print("Warnings:")
        for warning in resolved.warnings:
            print(f"  - {warning}")
    print(f"SOPS keys file: {resolved.keys_file}")
    print("SOPS keys:")
    if not resolved.sops_keys:
        print("  (none)")
    for catalog_id, spec in sorted(resolved.sops_keys.items()):
        print(f"  - {catalog_id}:")
        print(f"      provider: {spec.provider}")
        print(f"      location: {spec.location}")
        print(f"      vault key: {spec.vault_key}")
    print("SOPS env:")
    if not resolved.sops_env:
        print("  (none)")
    for entry in resolved.sops_env:
        print(f"  - {entry.alias}: {entry.path} (layer {entry.layer})")
    print("Imports:")
    if not resolved.imports:
        print("  (none)")
    for spec in resolved.imports:
        print(f"  - {spec.provider}/{spec.location}: {', '.join(spec.vars)}")
    print("Local:")
    if not resolved.local:
        print("  (none)")
    for key in sorted(resolved.local):
        print(f"  - {key}")
