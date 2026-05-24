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

DEFAULT_KEYS_FILE = Path("~/.config/sops/age/keys.txt")
DEFAULT_OCI_LOCATION = "dev-env"
DEFAULT_OCI_PROVIDER = "oci"
DEFAULT_VAULT_KEY = "sops-age-key-example"

LAYER_ETC = 0
LAYER_USER = 1
LAYER_REPO = 2
LAYER_EXPLICIT = 3


@dataclass(frozen=True)
class ImportSpec:
    provider: str
    location: str
    vars: tuple[str, ...]
    source: Path


@dataclass(frozen=True)
class SopsKeySpec:
    catalog_id: str
    provider: str
    location: str
    vault_key: str
    source: Path


@dataclass(frozen=True)
class SopsEnvEntry:
    alias: str
    path: Path
    layer: int
    source: Path


@dataclass
class ResolvedConfig:
    providers: dict[str, Any] = field(default_factory=dict)
    imports: list[ImportSpec] = field(default_factory=list)
    local: dict[str, str] = field(default_factory=dict)
    keys_file: Path = field(default_factory=lambda: DEFAULT_KEYS_FILE.expanduser().resolve())
    sops_keys: dict[str, SopsKeySpec] = field(default_factory=dict)
    sops_env: list[SopsEnvEntry] = field(default_factory=list)
    loaded_files: list[Path] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def expand_path(path: str | os.PathLike[str]) -> Path:
    return Path(path).expanduser().resolve()


def find_project_config(start: Path | None = None) -> Path | None:
    cur = (start or Path.cwd()).resolve()
    git_root = find_git_root(cur)
    candidates = _search_directories(cur, git_root)
    for directory in candidates:
        for name in PROJECT_CONFIG_NAMES:
            candidate = directory / name
            if candidate.is_file():
                return candidate
    return None


def find_git_root(start: Path | None = None) -> Path | None:
    cur = (start or Path.cwd()).resolve()
    for directory in (cur, *cur.parents):
        marker = directory / ".git"
        if marker.is_dir() or marker.is_file():
            return directory
    return None


def _search_directories(start: Path, stop: Path | None) -> tuple[Path, ...]:
    directories = (start, *start.parents)
    if stop is None:
        return directories
    result: list[Path] = []
    for directory in directories:
        result.append(directory)
        if directory == stop:
            break
    return tuple(result)


def default_config_paths(
    explicit_env: str | None = None,
    *,
    include_project: bool = True,
) -> list[Path]:
    paths: list[Path] = []
    if SYSTEM_CONFIG.is_file():
        paths.append(SYSTEM_CONFIG)
    if USER_CONFIG.is_file():
        paths.append(USER_CONFIG)
    if include_project and not explicit_env:
        project = find_project_config()
        if project:
            paths.append(project)
    if explicit_env:
        paths.append(expand_path(explicit_env))
    return _dedupe_paths(paths)


def layer_for_path(path: Path) -> int:
    resolved = path.expanduser().resolve()
    if resolved == SYSTEM_CONFIG.expanduser().resolve():
        return LAYER_ETC
    if resolved == USER_CONFIG.expanduser().resolve():
        return LAYER_USER
    return LAYER_REPO


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


def load_config(explicit_env: str | None = None, *, include_project: bool = True) -> ResolvedConfig:
    paths = default_config_paths(explicit_env, include_project=include_project)
    if not paths:
        raise ConfigError("no config found; create ~/.config/elr/config.yaml or env.oci.yaml")

    resolved = ResolvedConfig()
    seen: set[Path] = set()
    sops_keys_raw: dict[str, dict[str, Any]] = {}
    sops_env_by_alias: dict[str, SopsEnvEntry] = {}

    for path in paths:
        layer = LAYER_EXPLICIT if explicit_env and path == expand_path(explicit_env) else layer_for_path(path)
        _load_config_recursive(
            path,
            resolved,
            seen,
            layer=layer,
            sops_keys_raw=sops_keys_raw,
            sops_env_by_alias=sops_env_by_alias,
        )

    resolved.sops_keys = _build_sops_key_specs(sops_keys_raw, resolved.warnings)
    resolved.sops_env = sorted(sops_env_by_alias.values(), key=lambda item: (item.layer, item.alias))
    _validate_resolved(resolved)
    return resolved


def _load_config_recursive(
    path: Path,
    resolved: ResolvedConfig,
    seen: set[Path],
    *,
    layer: int,
    sops_keys_raw: dict[str, dict[str, Any]],
    sops_env_by_alias: dict[str, SopsEnvEntry],
) -> None:
    path = path.expanduser().resolve()
    if path in seen:
        raise ConfigError(f"cyclic import detected at {path}")
    seen.add(path)
    data = load_yaml_file(path)

    imports_field = data.get("imports", [])
    config_imports, secret_imports = _split_imports(imports_field, path)
    for imported in config_imports:
        _load_config_recursive(
            imported,
            resolved,
            seen,
            layer=layer,
            sops_keys_raw=sops_keys_raw,
            sops_env_by_alias=sops_env_by_alias,
        )

    providers = data.get("providers", {})
    if providers is not None:
        if not isinstance(providers, dict):
            raise ConfigError(f"providers must be a mapping in {path}")
        resolved.providers = _merge_providers(resolved.providers, providers)

    local = data.get("local", {})
    if local is not None:
        if not isinstance(local, dict):
            raise ConfigError(f"local must be a mapping in {path}")
        for key, value in local.items():
            _validate_env_name(str(key), path)
            resolved.local[str(key)] = str(value)

    resolved.imports.extend(secret_imports)

    sops_section = data.get("sops")
    if isinstance(sops_section, dict):
        _merge_sops_section(
            sops_section,
            path=path,
            layer=layer,
            resolved=resolved,
            sops_keys_raw=sops_keys_raw,
            sops_env_by_alias=sops_env_by_alias,
        )

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


def _merge_sops_section(
    section: dict[str, Any],
    *,
    path: Path,
    layer: int,
    resolved: ResolvedConfig,
    sops_keys_raw: dict[str, dict[str, Any]],
    sops_env_by_alias: dict[str, SopsEnvEntry],
) -> None:
    keys_file = section.get("keys_file")
    if keys_file:
        resolved.keys_file = _resolve_env_path(str(keys_file), path)

    env_map = section.get("env")
    if isinstance(env_map, dict):
        for alias, raw_path in env_map.items():
            if not isinstance(alias, str) or not isinstance(raw_path, str):
                raise ConfigError(f"sops.env entries must be string paths in {path}")
            sops_env_by_alias[alias] = SopsEnvEntry(
                alias=alias,
                path=_resolve_env_path(raw_path, path),
                layer=layer,
                source=path,
            )

    keys = section.get("keys")
    if isinstance(keys, dict):
        for catalog_id, entry in keys.items():
            if not isinstance(entry, dict):
                raise ConfigError(f"sops.keys.{catalog_id} must be a mapping in {path}")
            merged = dict(sops_keys_raw.get(str(catalog_id), {}))
            merged.update(entry)
            merged["_source"] = path
            sops_keys_raw[str(catalog_id)] = merged
        return

    if _is_flat_legacy_sops(section):
        merged = dict(sops_keys_raw.get("default", {}))
        vault_key = section.get("key") or section.get("secret", DEFAULT_VAULT_KEY)
        if section.get("secret") and not section.get("key"):
            resolved.warnings.append(
                f"sops.secret in {path} is deprecated; use sops.keys.default.key instead"
            )
        merged.update(
            {
                "provider": section.get("provider", DEFAULT_OCI_PROVIDER),
                "location": section.get("location", DEFAULT_OCI_LOCATION),
                "key": vault_key,
                "_source": path,
            }
        )
        if section.get("keys_file"):
            resolved.keys_file = _resolve_env_path(str(section["keys_file"]), path)
        sops_keys_raw["default"] = merged


def _is_flat_legacy_sops(section: dict[str, Any]) -> bool:
    return any(
        key in section
        for key in ("provider", "location", "key", "secret", "keys_file", "env")
    ) and "keys" not in section


def _vault_key_from_entry(entry: dict[str, Any], warnings: list[str], source: Path) -> str:
    if entry.get("key"):
        if entry.get("secret"):
            warnings.append(
                f"sops.keys entry in {source} has both .key and .secret; using .key"
            )
        return str(entry["key"])
    if entry.get("secret"):
        warnings.append(
            f"sops.keys entry in {source} uses deprecated .secret; use .key instead"
        )
        return str(entry["secret"])
    return DEFAULT_VAULT_KEY


def _build_sops_key_specs(
    raw: dict[str, dict[str, Any]],
    warnings: list[str],
) -> dict[str, SopsKeySpec]:
    specs: dict[str, SopsKeySpec] = {}
    for catalog_id, entry in raw.items():
        source = entry.get("_source", USER_CONFIG)
        if not isinstance(source, Path):
            source = Path(str(source))
        specs[catalog_id] = SopsKeySpec(
            catalog_id=catalog_id,
            provider=str(entry.get("provider", DEFAULT_OCI_PROVIDER)),
            location=str(entry.get("location", DEFAULT_OCI_LOCATION)),
            vault_key=_vault_key_from_entry(entry, warnings, source),
            source=source,
        )
    return specs


def _resolve_env_path(value: str, source: Path) -> Path:
    expanded = Path(value).expanduser()
    if expanded.is_absolute():
        return expanded.resolve()
    return (source.parent / expanded).resolve()


def _resolve_relative_path(value: str, source: Path) -> Path:
    expanded = Path(value).expanduser()
    if expanded.is_absolute():
        return expanded
    return (source.parent / expanded).resolve()


def _merge_providers(base: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    merged = _deep_merge(dict(base), incoming)
    base_oci = base.get("oci", {}) if isinstance(base.get("oci"), dict) else {}
    incoming_oci = incoming.get("oci", {}) if isinstance(incoming.get("oci"), dict) else {}
    base_locations = base_oci.get("locations", {}) if isinstance(base_oci.get("locations"), dict) else {}
    incoming_locations = (
        incoming_oci.get("locations", {}) if isinstance(incoming_oci.get("locations"), dict) else {}
    )
    if not incoming_locations:
        return merged

    oci = merged.setdefault("oci", {})
    locations = oci.setdefault("locations", {})
    for name, incoming_loc in incoming_locations.items():
        if not isinstance(incoming_loc, dict):
            continue
        base_loc = base_locations.get(name, {})
        if not isinstance(base_loc, dict):
            base_loc = {}
        merged_loc = _deep_merge(dict(base_loc), incoming_loc)
        base_secrets = base_loc.get("secrets")
        incoming_secrets = incoming_loc.get("secrets")
        if isinstance(base_secrets, list) or isinstance(incoming_secrets, list):
            merged_loc["secrets"] = _union_strings(
                base_secrets if isinstance(base_secrets, list) else [],
                incoming_secrets if isinstance(incoming_secrets, list) else [],
            )
        locations[name] = merged_loc
    return merged


def _union_strings(first: list[Any], second: list[Any]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in [*first, *second]:
        text = str(value)
        if text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


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

    for spec in config.sops_keys.values():
        provider_config = config.providers.get(spec.provider)
        if not isinstance(provider_config, dict):
            raise ConfigError(
                f"provider {spec.provider!r} required by sops key {spec.catalog_id!r} is not configured"
            )
        locations = provider_config.get("locations", {})
        if not isinstance(locations, dict) or spec.location not in locations:
            raise ConfigError(
                f"location {spec.location!r} for provider {spec.provider!r} "
                f"(sops key {spec.catalog_id!r}) is not configured"
            )
