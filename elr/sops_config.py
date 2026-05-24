from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import (
    SYSTEM_CONFIG,
    USER_CONFIG,
    _deep_merge,
    _resolve_relative_path,
    expand_path,
    find_git_root,
    load_yaml_file,
)
from .errors import ConfigError

PROJECT_SOPS_CONFIG_NAMES = ("sops.oci.yaml", ".sops.oci.yaml")
DEFAULT_KEY_ID = "default"
DEFAULT_AGE_KEY_DIR = Path("~/.config/sops/age")
DEFAULT_SOPS_ENV_FILE = ".env.sops"
DEFAULT_OCI_SECRET = "sops-age-key"
DEFAULT_OCI_LOCATION = "dev-env"
DEFAULT_OCI_PROVIDER = "oci"


@dataclass(frozen=True)
class SopsKeySpec:
    key_id: str
    provider: str
    location: str
    secret: str
    age_key_file: Path
    env_file: Path
    source: Path


@dataclass
class ResolvedSopsConfig:
    providers: dict[str, Any] = field(default_factory=dict)
    keys: dict[str, SopsKeySpec] = field(default_factory=dict)
    active_key: str = DEFAULT_KEY_ID
    loaded_files: list[Path] = field(default_factory=list)


def find_project_sops_config(start: Path | None = None) -> Path | None:
    cur = (start or Path.cwd()).resolve()
    git_root = find_git_root(cur)
    directories = _search_directories(cur, git_root)
    for directory in directories:
        for name in PROJECT_SOPS_CONFIG_NAMES:
            candidate = directory / name
            if candidate.is_file():
                return candidate
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


def default_sops_config_paths(
    explicit: str | None = None,
    *,
    include_project: bool = True,
) -> list[Path]:
    paths: list[Path] = []
    if SYSTEM_CONFIG.is_file():
        paths.append(SYSTEM_CONFIG)
    if USER_CONFIG.is_file():
        paths.append(USER_CONFIG)
    if include_project and not explicit:
        project = find_project_sops_config()
        if project:
            paths.append(project)
    if explicit:
        paths.append(expand_path(explicit))
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


def load_sops_config(
    explicit: str | None = None,
    *,
    include_project: bool = True,
    key_id: str | None = None,
) -> ResolvedSopsConfig:
    paths = default_sops_config_paths(explicit, include_project=include_project)
    if not paths:
        raise ConfigError(
            "no sops config found; create ~/.config/elr/config.yaml or sops.oci.yaml"
        )

    merged_providers: dict[str, Any] = {}
    merged_keys: dict[str, dict[str, Any]] = {}
    defaults: dict[str, Any] = {
        "key": DEFAULT_KEY_ID,
        "env_file": DEFAULT_SOPS_ENV_FILE,
        "age_key_dir": str(DEFAULT_AGE_KEY_DIR),
    }
    sync_block: dict[str, Any] = {}
    loaded_files: list[Path] = []
    seen: set[Path] = set()

    for path in paths:
        _load_sops_file(
            path,
            merged_providers=merged_providers,
            merged_keys=merged_keys,
            defaults=defaults,
            sync_block=sync_block,
            loaded_files=loaded_files,
            seen=seen,
        )

    active_key = str(sync_block.get("key") or defaults.get("key") or DEFAULT_KEY_ID)
    if key_id:
        active_key = key_id

    age_key_dir = Path(str(defaults.get("age_key_dir", DEFAULT_AGE_KEY_DIR))).expanduser()
    default_env_file = Path(str(defaults.get("env_file", DEFAULT_SOPS_ENV_FILE)))

    specs: dict[str, SopsKeySpec] = {}
    for kid, entry in merged_keys.items():
        specs[kid] = _build_key_spec(
            kid,
            entry,
            age_key_dir=age_key_dir,
            default_env_file=default_env_file,
            sync_block=sync_block,
            source=loaded_files[-1] if loaded_files else USER_CONFIG,
        )

    if active_key not in specs:
        raise ConfigError(
            f"sops key {active_key!r} is not defined; known keys: {', '.join(sorted(specs)) or '(none)'}"
        )

    _validate_providers(specs, merged_providers, loaded_files)
    return ResolvedSopsConfig(
        providers=merged_providers,
        keys=specs,
        active_key=active_key,
        loaded_files=loaded_files,
    )


def _load_sops_file(
    path: Path,
    *,
    merged_providers: dict[str, Any],
    merged_keys: dict[str, dict[str, Any]],
    defaults: dict[str, Any],
    sync_block: dict[str, Any],
    loaded_files: list[Path],
    seen: set[Path],
) -> None:
    path = path.expanduser().resolve()
    if path in seen:
        raise ConfigError(f"cyclic sops config import at {path}")
    seen.add(path)
    data = load_yaml_file(path)

    for imported in _config_imports(data.get("imports"), path):
        _load_sops_file(
            imported,
            merged_providers=merged_providers,
            merged_keys=merged_keys,
            defaults=defaults,
            sync_block=sync_block,
            loaded_files=loaded_files,
            seen=seen,
        )

    providers = data.get("providers")
    if isinstance(providers, dict):
        merged_providers.update(_deep_merge(merged_providers, providers))

    sops_section = data.get("sops")
    if isinstance(sops_section, dict):
        _merge_sops_section(
            sops_section,
            merged_keys=merged_keys,
            defaults=defaults,
            source=path,
        )

    project_sync = data.get("sync")
    if isinstance(project_sync, dict):
        sync_block.clear()
        sync_block.update(project_sync)

    loaded_files.append(path)
    seen.remove(path)


def _config_imports(imports_field: Any, source: Path) -> list[Path]:
    if imports_field in (None, []):
        return []
    if not isinstance(imports_field, list):
        raise ConfigError(f"imports must be a list in {source}")

    paths: list[Path] = []
    for item in imports_field:
        if isinstance(item, str):
            paths.append(_resolve_relative_path(item, source))
            continue
        if isinstance(item, dict) and "sops" in item:
            continue
        if isinstance(item, dict):
            raise ConfigError(
                f"unsupported import entry in {source}; use a config path string or sops key reference in sync"
            )
    return paths


def _merge_sops_section(
    section: dict[str, Any],
    *,
    merged_keys: dict[str, dict[str, Any]],
    defaults: dict[str, Any],
    source: Path,
) -> None:
    nested_defaults = section.get("defaults")
    if isinstance(nested_defaults, dict):
        defaults.update(nested_defaults)

    keys = section.get("keys")
    if isinstance(keys, dict):
        for key_id, entry in keys.items():
            if not isinstance(entry, dict):
                raise ConfigError(f"sops.keys.{key_id} must be a mapping in {source}")
            merged_keys[str(key_id)] = _deep_merge(merged_keys.get(str(key_id), {}), entry)
        return

    if _is_flat_legacy_sops(section):
        merged_keys[DEFAULT_KEY_ID] = _deep_merge(
            merged_keys.get(DEFAULT_KEY_ID, {}),
            {
                "provider": section.get("provider", DEFAULT_OCI_PROVIDER),
                "location": section.get("location", DEFAULT_OCI_LOCATION),
                "secret": section.get("secret", DEFAULT_OCI_SECRET),
                "age_key_file": section.get("age_key_file"),
                "env_file": section.get("env_file"),
            },
        )
        if section.get("env_file"):
            defaults["env_file"] = section["env_file"]
        if section.get("age_key_file"):
            pass


def _is_flat_legacy_sops(section: dict[str, Any]) -> bool:
    return any(
        key in section
        for key in ("provider", "location", "secret", "age_key_file", "env_file")
    )


def _build_key_spec(
    key_id: str,
    entry: dict[str, Any],
    *,
    age_key_dir: Path,
    default_env_file: Path,
    sync_block: dict[str, Any],
    source: Path,
) -> SopsKeySpec:
    provider = str(entry.get("provider", DEFAULT_OCI_PROVIDER))
    location = str(entry.get("location", DEFAULT_OCI_LOCATION))
    secret = str(entry.get("secret", DEFAULT_OCI_SECRET))

    age_key_raw = entry.get("age_key_file")
    if age_key_raw:
        age_key_file = Path(str(age_key_raw)).expanduser()
        if not age_key_file.is_absolute():
            age_key_file = (source.parent / age_key_file).resolve()
        else:
            age_key_file = age_key_file.resolve()
    else:
        age_key_file = _default_age_key_path(key_id, age_key_dir)

    env_raw = entry.get("env_file")
    if env_raw:
        env_file = Path(str(env_raw))
    elif key_id == sync_block.get("key") and sync_block.get("env_file"):
        env_file = Path(str(sync_block["env_file"]))
    else:
        env_file = default_env_file

    return SopsKeySpec(
        key_id=key_id,
        provider=provider,
        location=location,
        secret=secret,
        age_key_file=age_key_file,
        env_file=env_file,
        source=source,
    )


def _default_age_key_path(key_id: str, age_key_dir: Path) -> Path:
    if key_id == DEFAULT_KEY_ID:
        return (age_key_dir.expanduser() / "keys.txt").resolve()
    return (age_key_dir.expanduser() / f"{key_id}.txt").resolve()


def _validate_providers(
    specs: dict[str, SopsKeySpec],
    providers: dict[str, Any],
    loaded_files: list[Path],
) -> None:
    for spec in specs.values():
        provider_config = providers.get(spec.provider)
        if not isinstance(provider_config, dict):
            files = ", ".join(str(path) for path in loaded_files)
            raise ConfigError(
                f"provider {spec.provider!r} required by sops key {spec.key_id!r} "
                f"is not configured (loaded: {files})"
            )
        locations = provider_config.get("locations", {})
        if not isinstance(locations, dict) or spec.location not in locations:
            raise ConfigError(
                f"location {spec.location!r} for provider {spec.provider!r} "
                f"(sops key {spec.key_id!r}) is not configured"
            )


def list_sync_plan(resolved: ResolvedSopsConfig) -> list[tuple[str, SopsKeySpec]]:
    return sorted(resolved.keys.items(), key=lambda item: item[0])
