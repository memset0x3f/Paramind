from inference.ModelRegistry import ModelRegistry, ModelFamily


def test_qwen_registry():
    info = ModelRegistry.get(ModelFamily.QWEN, "Qwen/Qwen2.5-1.5B-Instruct")
    assert info.total_layers == 28
    assert info.embed_key == "model.embed_tokens"
    assert info.norm_key == "model.norm"
    assert info.lm_head_key == "lm_head"
    assert info.layer_prefix == "model.layers"
    assert info.rotary_key == "model.rotary_emb"


def test_llama_registry():
    info = ModelRegistry.get(ModelFamily.LLAMA, "meta-llama/Llama-3.1-8B-Instruct")
    assert info.total_layers == 32
    assert info.embed_key == "model.embed_tokens"
    assert info.norm_key == "model.norm"
    assert info.lm_head_key == "lm_head"
    assert info.layer_prefix == "model.layers"
    assert (
        info.rotary_key is None
    )  # Llama uses RoPE inside attention, no separate module


def test_layer_key_pattern():
    info = ModelRegistry.get(ModelFamily.QWEN, "Qwen/Qwen2.5-1.5B-Instruct")
    keys = info.layer_weight_prefixes(start=0, end=3)
    assert "model.layers.0." in keys[0]
    assert "model.layers.2." in keys[-1]
    assert len(keys) == 3
