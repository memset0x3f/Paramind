"""Resolve local filesystem directories for model weights from logical model_id.

``models/registry.json`` is the single source of truth and must contain absolute
paths:

{
  "<model_id>": {"path": "/absolute/path/to/weights"}
}
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_REGISTRY_FILENAME = "registry.json"


def repo_root() -> Path:
    """Repository root (parent of ``models/``)."""
    return Path(__file__).resolve().parent.parent


def models_dir() -> Path:
    return repo_root() / "models"


def safe_model_id(model_id: str) -> str:
    return model_id.replace("/", "__")


def _load_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _registry() -> dict[str, Any]:
    return _load_json(models_dir() / _REGISTRY_FILENAME)


def _dir_has_weights(path: Path) -> bool:
    if not path.is_dir():
        return False
    index = path / "model.safetensors.index.json"
    if index.is_file():
        return True
    try:
        for child in path.iterdir():
            if child.is_file() and child.suffix == ".safetensors":
                return True
    except OSError:
        return False
    return False


def resolve_weights_dir(model_id: str) -> Path | None:
    """Return a local directory containing safetensors weights, or ``None``."""
    entry = _registry().get(model_id)
    if not isinstance(entry, dict) or not entry.get("path"):
        return None
    raw_path = Path(str(entry["path"])).expanduser()
    if not raw_path.is_absolute():
        raise ValueError(
            f"{_REGISTRY_FILENAME} path for {model_id!r} must be absolute: {raw_path}"
        )
    if _dir_has_weights(raw_path):
        return raw_path.resolve()
    return None
