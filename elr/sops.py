from __future__ import annotations

import os
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .errors import ConfigError, ElrError
from .providers.oci import OciSecretProvider
from .sops_config import (
    DEFAULT_KEY_ID,
    ResolvedSopsConfig,
    SopsKeySpec,
    list_sync_plan,
    load_sops_config,
)


@dataclass(frozen=True)
class SopsSettings:
    age_key_file: Path
    env_file: Path
    provider: str
    location: str
    secret: str
    key_id: str = DEFAULT_KEY_ID


def load_sops_settings(
    *,
    explicit_env: str | None = None,
    include_project: bool = True,
    key_id: str | None = None,
    age_key_file: str | None = None,
    location: str | None = None,
    secret: str | None = None,
    provider: str | None = None,
    env_file: str | None = None,
) -> tuple[SopsSettings, ResolvedSopsConfig]:
    resolved = load_sops_config(
        explicit_env,
        include_project=include_project,
        key_id=key_id,
    )
    spec = resolved.keys[resolved.active_key]
    settings = _settings_from_spec(spec)

    if provider:
        settings = _replace(settings, provider=provider)
    if location:
        settings = _replace(settings, location=location)
    if secret:
        settings = _replace(settings, secret=secret)
    if age_key_file:
        settings = _replace(settings, age_key_file=Path(age_key_file).expanduser().resolve())
    if env_file:
        settings = _replace(settings, env_file=Path(env_file))

    return settings, resolved


def _settings_from_spec(spec: SopsKeySpec) -> SopsSettings:
    return SopsSettings(
        age_key_file=spec.age_key_file,
        env_file=spec.env_file,
        provider=spec.provider,
        location=spec.location,
        secret=spec.secret,
        key_id=spec.key_id,
    )


def _replace(settings: SopsSettings, **kwargs) -> SopsSettings:
    data = {
        "age_key_file": settings.age_key_file,
        "env_file": settings.env_file,
        "provider": settings.provider,
        "location": settings.location,
        "secret": settings.secret,
        "key_id": settings.key_id,
    }
    data.update(kwargs)
    return SopsSettings(**data)


def age_key_present(path: Path) -> bool:
    return path.is_file() and path.stat().st_size > 0


def sync_age_key(
    settings: SopsSettings,
    resolved: ResolvedSopsConfig,
    *,
    force: bool = False,
) -> Path:
    if age_key_present(settings.age_key_file) and not force:
        return settings.age_key_file

    provider_config = resolved.providers.get(settings.provider)
    if not isinstance(provider_config, dict):
        raise ConfigError(f"provider {settings.provider!r} is not configured")

    provider = OciSecretProvider(provider_config)
    raw = provider.fetch_raw_secret(settings.location, settings.secret)
    content = normalize_age_key_content(raw)
    write_age_key_file(settings.age_key_file, content)
    return settings.age_key_file


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
        raise ElrError("age key secret is empty")

    if "AGE-SECRET-KEY-" not in stripped:
        raise ElrError("age key secret does not contain AGE-SECRET-KEY-")

    if stripped.startswith("#") or "\n# " in stripped or stripped.startswith("# created:"):
        return stripped

    lines = [line.strip() for line in stripped.splitlines() if line.strip()]
    for line in lines:
        if line.startswith("SOPS_AGE_KEY="):
            return _minimal_keys_txt(line.split("=", 1)[1].strip())
        if line.startswith("AGE-SECRET-KEY-"):
            return _minimal_keys_txt(line)

    raise ElrError("age key secret format is not recognized")


def _minimal_keys_txt(secret_line: str) -> str:
    return f"# synced by elr\n{secret_line}"


def shell_source_lines(
    settings: SopsSettings,
    *,
    sync: bool = False,
    resolved: ResolvedSopsConfig | None = None,
) -> str:
    path = settings.age_key_file
    if sync:
        if resolved is None:
            settings, resolved = load_sops_settings(key_id=settings.key_id)
        sync_age_key(settings, resolved)
    elif not age_key_present(path):
        raise ElrError(
            f"age key file not found: {path}; run `elr sops sync` or `elr sops source --sync`"
        )

    quoted = shlex.quote(str(path))
    return f"export SOPS_AGE_KEY_FILE={quoted}\n"


def exec_with_sops(
    settings: SopsSettings,
    resolved: ResolvedSopsConfig,
    command: list[str],
    *,
    sync: bool = True,
    cwd: Path | None = None,
) -> int:
    if sync:
        sync_age_key(settings, resolved)

    env = os.environ.copy()
    env["SOPS_AGE_KEY_FILE"] = str(settings.age_key_file)
    env_file = (cwd or Path.cwd()) / settings.env_file
    if not env_file.is_file():
        raise ElrError(f"sops env file not found: {env_file}")

    full_command = [
        "sops",
        "exec-env",
        str(env_file),
        "--",
        *command,
    ]
    completed = subprocess.run(full_command, env=env, cwd=cwd or Path.cwd(), check=False)
    return int(completed.returncode)


def print_shell_source(
    settings: SopsSettings,
    *,
    sync: bool,
    resolved: ResolvedSopsConfig,
) -> None:
    print(shell_source_lines(settings, sync=sync, resolved=resolved), end="")


def print_sync_status(path: Path, *, created: bool, key_id: str | None = None) -> None:
    label = f" ({key_id})" if key_id else ""
    action = "Wrote" if created else "Age key already present at"
    print(f"{action}{label}: {path}")


def print_sops_plan(resolved: ResolvedSopsConfig, *, active_only: bool = False) -> None:
    print("Config files:")
    for path in resolved.loaded_files:
        print(f"  - {path}")
    print("SOPS keys:")
    for key_id, spec in list_sync_plan(resolved):
        if active_only and key_id != resolved.active_key:
            continue
        marker = " (active)" if key_id == resolved.active_key else ""
        print(f"  - {key_id}{marker}:")
        print(f"      provider: {spec.provider}")
        print(f"      location: {spec.location}")
        print(f"      secret:   {spec.secret}")
        print(f"      key file: {spec.age_key_file}")
        print(f"      env file: {spec.env_file}")
