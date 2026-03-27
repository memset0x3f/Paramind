import pytest
import torch
from pathlib import Path
from inference.ShardLoader import ShardLoader
from inference.ShardConfig import ShardConfig, ModelFamily


def _hf_token():
    """Match huggingface_hub: env vars, then token from `huggingface-cli login` cache."""
    import os

    env = (
        os.environ.get("HF_TOKEN") or os.environ.get("HUGGINGFACE_HUB_TOKEN") or ""
    ).strip()
    if env:
        return env
    try:
        from huggingface_hub import get_token

        cached = get_token()
        return (cached or "").strip()
    except Exception:
        return ""


@pytest.fixture(scope="module", autouse=True)
def require_hf_token():
    if not _hf_token():
        pytest.fail(
            "No Hugging Face token visible to this process. Llama shard tests need the same "
            "credential that huggingface_hub uses: export HF_TOKEN=... or "
            "HUGGINGFACE_HUB_TOKEN=..., or run `huggingface-cli login` once. "
            'If you already exported in a terminal but tests still fail: IDE / "Run Test" '
            "often does not inherit that shell — run pytest in the same terminal, or set "
            "HF_TOKEN in your IDE env / .env. Token: https://huggingface.co/settings/tokens"
        )


def _local_llama_snapshot():
    env_override = (
        Path.home()
        / ".cache"
        / "huggingface"
        / "hub"
        / "models--unsloth--Llama-3.2-1B-Instruct"
        / "snapshots"
        / "5a8abab4a5d6f164389b1079fb721cfab8d7126c"
    )
    required = [
        "config.json",
        "model.safetensors",
        "tokenizer.json",
        "tokenizer_config.json",
        "special_tokens_map.json",
        "generation_config.json",
    ]
    if env_override.is_dir() and all(
        (env_override / name).exists() for name in required
    ):
        return str(env_override)
    return None


def _llama_model_source():
    return _local_llama_snapshot() or "meta-llama/Llama-3.2-1B-Instruct"


@pytest.mark.slow
def test_llama_two_shard_pipeline():
    """Llama 3.2-1B (16 layers) split into 2 shards produces valid logits."""
    model_id = _llama_model_source()
    shard1_cfg = ShardConfig(
        model_id=model_id,
        family=ModelFamily.LLAMA,
        start_layer=0,
        end_layer=8,
        total_layers=16,
    )
    shard2_cfg = ShardConfig(
        model_id=model_id,
        family=ModelFamily.LLAMA,
        start_layer=8,
        end_layer=16,
        total_layers=16,
    )

    shard1 = ShardLoader(shard1_cfg, device="cpu").load()
    shard2 = ShardLoader(shard2_cfg, device="cpu").load()

    input_ids = torch.tensor([[1, 2, 3]], dtype=torch.long)
    hidden, kv1 = shard1.forward(input_ids)
    logits, kv2 = shard2.forward(hidden)

    assert logits.ndim == 3
    assert logits.shape[2] > 100  # vocab size


@pytest.mark.slow
def test_llama_first_shard_loads():
    """Llama first shard has embed_tokens and first half of layers."""
    model_id = _llama_model_source()
    cfg = ShardConfig(
        model_id=model_id,
        family=ModelFamily.LLAMA,
        start_layer=0,
        end_layer=8,
        total_layers=16,
    )
    shard = ShardLoader(cfg, device="cpu").load()

    assert shard.embed_tokens is not None
    assert len(shard.layers) == 8
    assert shard.lm_head is None
    assert shard.norm is None
    assert shard.rotary_emb is not None


@pytest.mark.slow
def test_llama_last_shard_loads():
    """Llama last shard has second half of layers, norm, and lm_head."""
    model_id = _llama_model_source()
    cfg = ShardConfig(
        model_id=model_id,
        family=ModelFamily.LLAMA,
        start_layer=8,
        end_layer=16,
        total_layers=16,
    )
    shard = ShardLoader(cfg, device="cpu").load()

    assert shard.embed_tokens is None
    assert len(shard.layers) == 8
    assert shard.lm_head is not None
    assert shard.norm is not None
    assert shard.rotary_emb is not None
