from __future__ import annotations

from dataclasses import dataclass

from .config import ResolvedConfig
from .errors import ConfigError
from .providers.oci import OciSecretProvider


@dataclass(frozen=True)
class PlanEntry:
    name: str
    source_type: str
    provider: str | None = None
    location: str | None = None
    config_source: str | None = None


@dataclass
class EnvResolution:
    values: dict[str, str]
    plan: list[PlanEntry]


def resolve_env(config: ResolvedConfig, fetch: bool = True) -> EnvResolution:
    values: dict[str, str] = {}
    plan: list[PlanEntry] = []

    for key, value in config.local.items():
        values[key] = value
        plan.append(PlanEntry(name=key, source_type="local"))

    provider_instances: dict[str, object] = {}
    for spec in config.imports:
        provider = provider_instances.get(spec.provider)
        if provider is None:
            provider = _build_provider(spec.provider, config.providers[spec.provider])
            provider_instances[spec.provider] = provider

        for var in spec.vars:
            plan.append(
                PlanEntry(
                    name=var,
                    source_type="secret",
                    provider=spec.provider,
                    location=spec.location,
                    config_source=str(spec.source),
                )
            )

        if fetch:
            resolved = provider.resolve_import(spec)  # type: ignore[attr-defined]
            values.update(resolved)

    return EnvResolution(values=values, plan=plan)


def _build_provider(name: str, provider_config: dict):
    if name == "oci":
        return OciSecretProvider(provider_config)
    raise ConfigError(f"unsupported provider: {name}")
