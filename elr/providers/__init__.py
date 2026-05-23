from __future__ import annotations

from typing import Protocol

from elr.config import ImportSpec


class SecretProvider(Protocol):
    def resolve_import(self, spec: ImportSpec) -> dict[str, str]:
        """Resolve all variables declared by an import spec."""
