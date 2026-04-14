import importlib.util
import json
from pathlib import Path

import pytest


def _load_model_resolve():
    path = Path(__file__).resolve().parents[2] / "models" / "resolve.py"
    spec = importlib.util.spec_from_file_location("model_resolve_standalone", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


mr = _load_model_resolve()


def test_safe_model_id():
    assert (
        mr.safe_model_id("Qwen/Qwen2.5-0.5B-Instruct") == "Qwen__Qwen2.5-0.5B-Instruct"
    )


def test_resolve_weights_dir_from_registry(monkeypatch, tmp_path):
    root = tmp_path / "repo"
    models = root / "models"
    models.mkdir(parents=True)
    weights = tmp_path / "weights" / "Qwen__Qwen2.5-0.5B-Instruct"
    weights.mkdir(parents=True)
    (weights / "dummy.safetensors").write_bytes(b"x")
    (models / "registry.json").write_text(
        json.dumps({"Qwen/Qwen2.5-0.5B-Instruct": {"path": str(weights)}}),
        encoding="utf-8",
    )

    monkeypatch.setattr(mr, "repo_root", lambda: root)
    monkeypatch.setattr(mr, "models_dir", lambda: models)

    found = mr.resolve_weights_dir("Qwen/Qwen2.5-0.5B-Instruct")
    assert found == weights.resolve()


def test_resolve_weights_dir_rejects_relative_registry_path(monkeypatch, tmp_path):
    root = tmp_path / "repo"
    models = root / "models"
    models.mkdir(parents=True)
    monkeypatch.setattr(mr, "repo_root", lambda: root)
    monkeypatch.setattr(mr, "models_dir", lambda: models)

    (models / "registry.json").write_text(
        json.dumps({"Qwen/Qwen2.5-0.5B-Instruct": {"path": "relative/path"}}),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="must be absolute"):
        mr.resolve_weights_dir("Qwen/Qwen2.5-0.5B-Instruct")


def test_resolve_weights_dir_returns_none_when_path_has_no_weights(
    monkeypatch, tmp_path
):
    root = tmp_path / "repo"
    models = root / "models"
    models.mkdir(parents=True)
    monkeypatch.setattr(mr, "repo_root", lambda: root)
    monkeypatch.setattr(mr, "models_dir", lambda: models)

    empty_dir = tmp_path / "empty"
    empty_dir.mkdir(parents=True)
    (models / "registry.json").write_text(
        json.dumps({"Qwen/Qwen2.5-0.5B-Instruct": {"path": str(empty_dir)}}),
        encoding="utf-8",
    )
    assert mr.resolve_weights_dir("Qwen/Qwen2.5-0.5B-Instruct") is None


def test_resolve_weights_dir_returns_none_when_missing(monkeypatch, tmp_path):
    root = tmp_path / "repo"
    models = root / "models"
    models.mkdir(parents=True)
    monkeypatch.setattr(mr, "repo_root", lambda: root)
    monkeypatch.setattr(mr, "models_dir", lambda: models)
    (models / "registry.json").write_text("{}", encoding="utf-8")

    assert mr.resolve_weights_dir("unknown/model") is None


def test_shardloader_resolve_model_path_uses_resolver(monkeypatch, tmp_path):
    pytest.importorskip("torch")

    weights = tmp_path / "w"
    weights.mkdir()
    (weights / "x.safetensors").write_bytes(b"1")

    import importlib

    shard_mod = importlib.import_module("inference.ShardLoader")

    model_id = "Qwen/Qwen2.5-0.5B-Instruct"

    def fake_resolve(mid: str):
        return weights if mid == model_id else None

    monkeypatch.setattr(shard_mod, "resolve_weights_dir", fake_resolve)

    from inference.ShardConfig import ModelFamily, ShardConfig
    from inference.ShardLoader import ShardLoader

    cfg = ShardConfig(
        model_id=model_id,
        family=ModelFamily.QWEN,
        start_layer=0,
        end_layer=1,
        total_layers=24,
    )
    loader = ShardLoader(cfg, device="cpu")
    assert Path(loader._resolve_model_path()) == weights.resolve()
