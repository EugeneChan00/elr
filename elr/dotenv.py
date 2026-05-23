from __future__ import annotations

from pathlib import Path

from .errors import ConfigError


def parse_dotenv_file(path: str | Path) -> dict[str, str]:
    dotenv_path = Path(path).expanduser()
    if not dotenv_path.is_file():
        raise ConfigError(f"env file not found: {dotenv_path}")
    return parse_dotenv_text(dotenv_path.read_text(encoding="utf-8"), source=dotenv_path)


def parse_dotenv_text(text: str, source: str | Path = "<dotenv>") -> dict[str, str]:
    values: dict[str, str] = {}
    for lineno, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            raise ConfigError(f"invalid dotenv line in {source}:{lineno}")
        key, value = line.split("=", 1)
        key = key.strip()
        if not key:
            raise ConfigError(f"empty dotenv key in {source}:{lineno}")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        values[key] = value
    return values
