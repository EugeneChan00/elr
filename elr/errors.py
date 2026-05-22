class ElrError(Exception):
    """Base exception for expected ELR failures."""


class ConfigError(ElrError):
    """Raised when configuration is missing or invalid."""


class SecretResolutionError(ElrError):
    """Raised when a requested secret cannot be resolved."""
