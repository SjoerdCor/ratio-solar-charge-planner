"""Laadt config.yaml vanuit de projectroot."""
from pathlib import Path
import yaml

_CONFIG_PAD = Path(__file__).parent.parent / "config.yaml"


def laad_config() -> dict:
    if not _CONFIG_PAD.exists():
        raise FileNotFoundError(
            "config.yaml niet gevonden — kopieer config.example.yaml naar config.yaml en vul in."
        )
    return yaml.safe_load(_CONFIG_PAD.read_text(encoding="utf-8"))
