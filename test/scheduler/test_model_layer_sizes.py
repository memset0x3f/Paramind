from __future__ import annotations

import json
from pathlib import Path

import pytest

torch = pytest.importorskip("torch")
from safetensors.torch import save_file

from models.model_info import analyze_model_dir, ensure_model_profile, profile_path_for


def _write_tiny_model(model_dir: Path) -> None:
    model_dir.mkdir(parents=True, exist_ok=True)
    tensors_a = {
        "model.embed_tokens.weight": torch.zeros((10, 4), dtype=torch.float16),
        "model.layers.0.self_attn.q_proj.weight": torch.zeros(
            (4, 4), dtype=torch.float16
        ),
        "model.layers.0.mlp.up_proj.weight": torch.zeros((4, 8), dtype=torch.float16),
    }
    tensors_b = {
        "model.layers.1.self_attn.q_proj.weight": torch.zeros(
            (4, 4), dtype=torch.float16
        ),
        "model.norm.weight": torch.zeros((4,), dtype=torch.float16),
        "lm_head.weight": torch.zeros((10, 4), dtype=torch.float16),
    }
    save_file(tensors_a, str(model_dir / "model-00001-of-00002.safetensors"))
    save_file(tensors_b, str(model_dir / "model-00002-of-00002.safetensors"))


def test_script_reports_ordered_sections_layer_sizes_and_special_components(
    tmp_path: Path,
):
    model_dir = tmp_path / "tiny-model"
    _write_tiny_model(model_dir)

    payload = analyze_model_dir(model_dir)

    assert payload["total_bytes"] == 296
    assert payload["layer_bytes"] == {"0": 96, "1": 32}
    assert payload["special_components"] == {
        "embeddings": 80,
        "final_norm": 8,
        "lm_head": 80,
    }

    assert payload["sections"]["first"]["total_bytes"] == 176
    assert payload["sections"]["first"]["layer_bytes"] == {"0": 96}
    assert payload["sections"]["first"]["special_components"] == {"embeddings": 80}

    assert payload["sections"]["middle"]["total_bytes"] == 0
    assert payload["sections"]["middle"]["layer_bytes"] == {}
    assert payload["sections"]["middle"]["special_components"] == {}

    assert payload["sections"]["last"]["total_bytes"] == 120
    assert payload["sections"]["last"]["layer_bytes"] == {"1": 32}
    assert payload["sections"]["last"]["special_components"] == {
        "final_norm": 8,
        "lm_head": 80,
    }


def test_ensure_model_profile_generates_profile_from_resolved_weights(
    monkeypatch, tmp_path: Path
):
    repo = tmp_path / "repo"
    models = repo / "models"
    models.mkdir(parents=True, exist_ok=True)
    weight_dir = tmp_path / "weights" / "demo_tiny"
    weight_dir.mkdir(parents=True, exist_ok=True)
    _write_tiny_model(weight_dir)
    (models / "registry.json").write_text(
        json.dumps({"demo/Tiny": {"path": str(weight_dir)}}),
        encoding="utf-8",
    )

    import models.model_info as mi
    import models.resolve as mr

    monkeypatch.setattr(mr, "repo_root", lambda: repo)
    monkeypatch.setattr(mr, "models_dir", lambda: models)
    monkeypatch.setattr(mi, "models_dir", lambda: models)

    out_path = ensure_model_profile("demo/Tiny")
    assert out_path == profile_path_for("demo/Tiny")
    payload = json.loads(out_path.read_text(encoding="utf-8"))
    assert payload["layer_bytes"] == {"0": 96, "1": 32}
