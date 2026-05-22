from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
import os

import yaml

from .errors import ConfigError

PROJECT_CONFIG_NAMES = ("env.oci.yaml", ".env.oci.yaml")
SYSTEM_CONFIG = Path("/etc/elr/config.yaml")
USER_CONFIG = Path("~/.config/elr/config.yaml").expanduser()


@dataclass(frozen=True)
class ImportSpec:
    provider: str
    location: str
    vars: tuple[str, ...]
    source: Path


@dataclass
class ResolvedConfig:
    providers: dict[str, Any] = field(default_factory=dict)
    imports: list[ImportSpec] = field(default_factory=list)
    local: dict[str, str] = field(default_factory=dict)
    loaded_files: list[Path] = field(default_factory=list)


def expand_path(path: str | os.PathLike[str]) -> Path:
    return Path(path).expanduser().resolve()


def find_project_config(start: Path | None = None) -> Path | None:
    cur = (start or Path.cwd()).resolve()
    candidates = (cur, *cur.parents)
    for directory in candidates:
        for name in PROJECT_CONFIG_NAMES:
            candidate = directory / name
            if candidate.is_file():
                return candidate
    return None


def default_config_paths(explicit_env: str | None = None) -> list[Path]:
    paths: list[Path] = []
    if SYSTEM_CONFIG.is_file():
        paths.append(SYSTEM_CONFIG)
    if USER_CONFIG.is_file():
        paths.append(USER_CONFIG)
    project = find_project_config()
    if project:
        paths.append(project)
    if explicit_env:
        paths.append(expand_path(explicit_env))
    return _dedupe_paths(paths)


def _dedupe_paths(paths: list[Path]) -> list[Path]:
    result: list[Path] = []
    seen: set[Path] = set()
    for path in paths:
        resolved = path.expanduser().resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        result.append(resolved)
    return result


def load_yaml_file(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ConfigError(f"config file not found: {path}")
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid YAML in {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"config root must be a mapping: {path}")
    return data


def load_config(explicit_env: str | None = None) -> ResolvedConfig:
    paths = default_config_paths(explicit_env)
    if not paths:
        raise ConfigError("no env config found; pass --env or use --no-env")

    resolved = ResolvedConfig()
    seen: set[Path] = set()
    for path in paths:
        _load_config_recursive(path, resolved, seen)
    _validate_resolved(resolved)
    return resolved


def _load_config_recursive(path: Path, resolved: ResolvedConfig, seen: set[Path]) -> None:
    path = path.expanduser().resolve()
    if path in seen:
        raise ConfigError(f"cyclic import detected at {path}")
    seen.add(path)
    data = load_yaml_file(path)

    imports_field = data.get("imports", [])
    config_imports, secret_imports = _split_imports(imports_field, path)
    for imported in config_imports:
        _load_config_recursive(imported, resolved, seen)

    providers = data.get("providers", {})
    if providers is not None:
        if not isinstance(providers, dict):
            raise ConfigError(f"providers must be a mapping in {path}")
        resolved.providers = _deep_merge(resolved.providers, providers)

    local = data.get("local", {})
    if local is not None:
        if not isinstance(local, dict):
            raise ConfigError(f"local must be a mapping in {path}")
        for key, value in local.items():
            _validate_env_name(str(key), path)
            resolved.local[str(key)] = str(value)

    resolved.imports.extend(secret_imports)
    resolved.loaded_files.append(path)
    seen.remove(path)


def _split_imports(imports_field: Any, source: Path) -> tuple[list[Path], list[ImportSpec]]:
    if imports_field in (None, []):
        return [], []
    if not isinstance(imports_field, list):
        raise ConfigError(f"imports must be a list in {source}")

    config_imports: list[Path] = []
    secret_imports: list[ImportSpec] = []
    for item in imports_field:
        if isinstance(item, str):
            config_imports.append(_resolve_relative_path(item, source))
            continue
        if not isinstance(item, dict):
            raise ConfigError(f"import entries must be strings or mappings in {source}")

        provider = item.get("provider")
        location = item.get("location")
        vars_field = item.get("vars")
        if not provider or not location or not isinstance(vars_field, list):
            raise ConfigError(
                f"secret import requires provider, location, and vars list in {source}"
            )
        var_names: list[str] = []
        for var in vars_field:
            if not isinstance(var, str):
                raise ConfigError(f"import var names must be strings in {source}")
            _validate_env_name(var, source)
            var_names.append(var)
        secret_imports.append(
            ImportSpec(
                provider=str(provider),
                location=str(location),
                vars=tuple(var_names),
                source=source,
            )
        )
    return config_imports, secret_imports


def _resolve_relative_path(value: str, source: Path) -> Path:
    expanded = Path(value).expanduser()
    if expanded.is_absolute():
        return expanded
    return (source.parent / expanded).resolve()


def _deep_merge(base: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in incoming.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _validate_env_name(name: str, source: Path) -> None:
    if not name:
        raise ConfigError(f"empty env var name in {source}")
    if not (name[0].isalpha() or name[0] == "_"):
        raise ConfigError(f"invalid env var name {name!r} in {source}")
    for char in name:
        if not (char.isalnum() or char == "_"):
            raise ConfigError(f"invalid env var name {name!r} in {source}")


def _validate_resolved(config: ResolvedConfig) -> None:
    for spec in config.imports:
        provider_config = config.providers.get(spec.provider)
        if not provider_config:
            raise ConfigError(
                f"provider {spec.provider!r} required by {spec.source} is not configured"
            )
        locations = provider_config.get("locations", {}) if isinstance(provider_config, dict) else {}
        if spec.location not in locations:
            raise ConfigError(
                f"location {spec.location!r} for provider {spec.provider!r} is not configured"
            )
