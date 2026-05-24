from __future__ import annotations

import os
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import ResolvedConfig, USER_CONFIG, load_config, load_yaml_file
from .errors import ConfigError, ElrError
from .providers.oci import OciSecretProvider

DEFAULT_AGE_KEY_FILE = Path("~/.config/sops/age/keys.txt")
DEFAULT_SOPS_ENV_FILE = ".env.sops"
DEFAULT_OCI_SECRET = "sops-age-key"
DEFAULT_OCI_LOCATION = "dev-env"
DEFAULT_OCI_PROVIDER = "oci"


@dataclass(frozen=True)
class SopsSettings:
    age_key_file: Path
    env_file: Path
    provider: str
    location: str
    secret: str


def load_sops_settings(
    *,
    config_path: Path | None = None,
    age_key_file: str | None = None,
    location: str | None = None,
    secret: str | None = None,
    provider: str | None = None,
    env_file: str | None = None,
) -> tuple[SopsSettings, ResolvedConfig]:
    path = (config_path or USER_CONFIG).expanduser()
    sops_section: dict[str, Any] = {}
    if path.is_file():
        data = load_yaml_file(path)
        raw = data.get("sops")
        if raw is not None:
            if not isinstance(raw, dict):
                raise ConfigError(f"sops must be a mapping in {path}")
            sops_section = raw

    resolved_provider = provider or sops_section.get("provider") or DEFAULT_OCI_PROVIDER
    resolved_location = location or sops_section.get("location") or DEFAULT_OCI_LOCATION
    resolved_secret = secret or sops_section.get("secret") or DEFAULT_OCI_SECRET
    resolved_age_key = age_key_file or sops_section.get("age_key_file") or str(DEFAULT_AGE_KEY_FILE)
    resolved_env_file = env_file or sops_section.get("env_file") or DEFAULT_SOPS_ENV_FILE

    config = load_config(include_project=False)
    return (
        SopsSettings(
            age_key_file=Path(resolved_age_key).expanduser().resolve(),
            env_file=Path(resolved_env_file),
            provider=str(resolved_provider),
            location=str(resolved_location),
            secret=str(resolved_secret),
        ),
        config,
    )


def age_key_present(path: Path) -> bool:
    return path.is_file() and path.stat().st_size > 0


def sync_age_key(
    settings: SopsSettings,
    config: ResolvedConfig,
    *,
    force: bool = False,
) -> Path:
    if age_key_present(settings.age_key_file) and not force:
        return settings.age_key_file

    provider_config = config.providers.get(settings.provider)
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


def shell_source_lines(settings: SopsSettings, *, sync: bool = False, config: ResolvedConfig | None = None) -> str:
    path = settings.age_key_file
    if sync:
        if config is None:
            _, config = load_sops_settings()
        sync_age_key(settings, config)
    elif not age_key_present(path):
        raise ElrError(
            f"age key file not found: {path}; run `elr age sync` or `elr sops source --sync`"
        )

    quoted = shlex.quote(str(path))
    return f"export SOPS_AGE_KEY_FILE={quoted}\n"


def exec_with_sops(
    settings: SopsSettings,
    config: ResolvedConfig,
    command: list[str],
    *,
    sync: bool = True,
    cwd: Path | None = None,
) -> int:
    if sync:
        sync_age_key(settings, config)

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


def print_shell_source(settings: SopsSettings, *, sync: bool, config: ResolvedConfig) -> None:
    print(shell_source_lines(settings, sync=sync, config=config), end="")


def print_sync_status(path: Path, *, created: bool) -> None:
    action = "Wrote" if created else "Age key already present at"
    print(f"{action}: {path}")
