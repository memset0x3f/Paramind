from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .resolve import models_dir, resolve_weights_dir, safe_model_id

_LAYER_RE = re.compile(r"(?:^|\.)layers\.(\d+)\.")
_SPECIAL_NAMES = {"embeddings", "final_norm", "lm_head"}


@dataclass(frozen=True)
class ModelInfo:
    model_id: str
    total_layers: int
    total_bytes: int
    profile_path: Path
    profile: dict[str, Any]
    weights_dir: Path | None


def profile_path_for(model_id: str) -> Path:
    return models_dir() / "profiles" / f"{safe_model_id(model_id)}.json"


def load_profile(model_id: str) -> dict[str, Any] | None:
    path = profile_path_for(model_id)
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _dtype_nbytes(dtype_name: str) -> int:
    mapping = {
        "F64": 8,
        "F32": 4,
        "F16": 2,
        "BF16": 2,
        "I64": 8,
        "I32": 4,
        "I16": 2,
        "I8": 1,
        "U8": 1,
        "BOOL": 1,
    }
    if dtype_name not in mapping:
        raise KeyError(f"Unsupported safetensors dtype: {dtype_name}")
    return mapping[dtype_name]


def _tensor_nbytes(tensor_slice) -> int:
    shape = tensor_slice.get_shape()
    numel = 1
    for dim in shape:
        numel *= dim
    return numel * _dtype_nbytes(str(tensor_slice.get_dtype()))


def _bucket_for_key(key: str) -> tuple[str, int | None]:
    match = _LAYER_RE.search(key)
    if match:
        layer = int(match.group(1))
        return f"layers.{layer}", layer
    if key.startswith("model.embed_tokens.") or key.startswith("transformer.wte"):
        return "embeddings", None
    if key.startswith("model.norm.") or key.startswith("transformer.ln_f"):
        return "final_norm", None
    if key.startswith("lm_head."):
        return "lm_head", None
    return "other", None


def _build_sections(
    layer_bytes: dict[str, int], special_components: dict[str, int]
) -> dict[str, Any]:
    layer_ids = sorted((int(k) for k in layer_bytes.keys()))
    if not layer_ids:
        return {
            "first": {"total_bytes": 0, "layer_bytes": {}, "special_components": {}},
            "middle": {"total_bytes": 0, "layer_bytes": {}, "special_components": {}},
            "last": {"total_bytes": 0, "layer_bytes": {}, "special_components": {}},
        }

    first_layer = str(layer_ids[0])
    last_layer = str(layer_ids[-1])
    middle_layers = [str(i) for i in layer_ids[1:-1]]

    sections = {
        "first": {
            "layer_bytes": {first_layer: layer_bytes[first_layer]},
            "special_components": {},
        },
        "middle": {
            "layer_bytes": {k: layer_bytes[k] for k in middle_layers},
            "special_components": {},
        },
        "last": {
            "layer_bytes": {last_layer: layer_bytes[last_layer]},
            "special_components": {},
        },
    }

    if "embeddings" in special_components:
        sections["first"]["special_components"]["embeddings"] = special_components[
            "embeddings"
        ]
    for name in ("final_norm", "lm_head"):
        if name in special_components:
            sections["last"]["special_components"][name] = special_components[name]

    for section in sections.values():
        section["total_bytes"] = sum(section["layer_bytes"].values()) + sum(
            section["special_components"].values()
        )

    return sections


def analyze_model_dir(model_dir: str | Path) -> dict[str, Any]:
    try:
        from safetensors import safe_open
    except ImportError as exc:
        raise ImportError(
            "safetensors is required to analyze model directories. "
            "Install it with `pip install safetensors`."
        ) from exc

    model_path = Path(model_dir)
    files = sorted(model_path.glob("*.safetensors"))
    if not files:
        raise FileNotFoundError(f"No .safetensors files found in {model_path}")

    components: dict[str, int] = defaultdict(int)
    layer_bytes: dict[str, int] = defaultdict(int)
    total_bytes = 0

    for file_path in files:
        with safe_open(str(file_path), framework="pt") as handle:
            for key in handle.keys():
                tensor_slice = handle.get_slice(key)
                nbytes = _tensor_nbytes(tensor_slice)
                bucket, layer = _bucket_for_key(key)
                components[bucket] += nbytes
                if layer is not None:
                    layer_bytes[str(layer)] += nbytes
                total_bytes += nbytes

    ordered_layer_bytes = dict(
        sorted(layer_bytes.items(), key=lambda item: int(item[0]))
    )
    special_components = {
        name: components[name] for name in sorted(_SPECIAL_NAMES) if name in components
    }
    other_components = {
        name: value
        for name, value in sorted(components.items())
        if name not in _SPECIAL_NAMES and not name.startswith("layers.")
    }

    return {
        "model_path": str(model_path),
        "total_bytes": total_bytes,
        "layer_bytes": ordered_layer_bytes,
        "special_components": special_components,
        "other_components": other_components,
        "sections": _build_sections(ordered_layer_bytes, special_components),
    }


def write_profile(model_id: str, payload: dict[str, Any]) -> Path:
    out_path = profile_path_for(model_id)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return out_path


def ensure_model_profile(model_id: str) -> Path:
    existing = profile_path_for(model_id)
    if existing.is_file():
        return existing
    weights_dir = resolve_weights_dir(model_id)
    if weights_dir is None:
        raise FileNotFoundError(
            f"No profile or local weights found for model_id={model_id!r}. "
            "Expected profile at "
            f"{existing} or absolute path mapping in models/registry.json."
        )
    payload = analyze_model_dir(weights_dir)
    return write_profile(model_id, payload)


def get_model_info(model_id: str, ensure_profile: bool = True) -> ModelInfo:
    if ensure_profile:
        profile_path = ensure_model_profile(model_id)
    else:
        profile_path = profile_path_for(model_id)
    payload = load_profile(model_id)
    if payload is None:
        raise FileNotFoundError(
            f"Missing profile for model_id={model_id!r}: {profile_path_for(model_id)}"
        )
    total_layers = len(payload.get("layer_bytes", {}))
    if total_layers <= 0:
        raise ValueError(f"Profile for {model_id!r} has no layer_bytes entries")
    return ModelInfo(
        model_id=model_id,
        total_layers=total_layers,
        total_bytes=int(payload.get("total_bytes", 0)),
        profile_path=profile_path,
        profile=payload,
        weights_dir=resolve_weights_dir(model_id),
    )


def ensure_model_info(model_id: str) -> ModelInfo:
    return get_model_info(model_id, ensure_profile=True)
