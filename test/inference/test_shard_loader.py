import pytest
import torch
from inference.ShardLoader import ShardLoader
from inference.ShardConfig import ShardConfig, ModelFamily


@pytest.fixture
def small_qwen_first_shard():
    """First half of Qwen2.5-0.5B for fast testing."""
    return ShardConfig(
        model_id="Qwen/Qwen2.5-0.5B-Instruct",
        family=ModelFamily.QWEN,
        start_layer=0,
        end_layer=12,
        total_layers=24,
        dtype="float16",
    )


@pytest.fixture
def small_qwen_last_shard():
    """Second half of Qwen2.5-0.5B for fast testing."""
    return ShardConfig(
        model_id="Qwen/Qwen2.5-0.5B-Instruct",
        family=ModelFamily.QWEN,
        start_layer=12,
        end_layer=24,
        total_layers=24,
        dtype="float16",
    )


@pytest.mark.slow
def test_load_first_shard(small_qwen_first_shard):
    """First shard should have embed_tokens, layers 0-11, NO lm_head."""
    loader = ShardLoader(small_qwen_first_shard, device="cpu")
    shard = loader.load()

    assert shard.embed_tokens is not None
    assert len(shard.layers) == 12
    assert shard.lm_head is None
    assert shard.norm is None  # norm only on last shard


@pytest.mark.slow
def test_load_last_shard(small_qwen_last_shard):
    """Last shard should have layers 12-23, norm, lm_head, NO embed_tokens."""
    loader = ShardLoader(small_qwen_last_shard, device="cpu")
    shard = loader.load()

    assert shard.embed_tokens is None
    assert len(shard.layers) == 12
    assert shard.lm_head is not None
    assert shard.norm is not None


@pytest.mark.slow
def test_forward_first_shard(small_qwen_first_shard):
    """First shard takes input_ids and returns hidden_states."""
    loader = ShardLoader(small_qwen_first_shard, device="cpu")
    shard = loader.load()

    input_ids = torch.tensor([[1, 2, 3]], dtype=torch.long)
    hidden, kv = shard.forward(input_ids)

    # hidden_states shape: [batch, seq_len, hidden_size]
    assert hidden.shape[0] == 1
    assert hidden.shape[1] == 3
    # Qwen2.5-0.5B uses bfloat16 internally
    assert hidden.dtype == torch.bfloat16


@pytest.mark.slow
def test_two_shard_pipeline(small_qwen_first_shard, small_qwen_last_shard):
    """Two shards chained produce logits."""
    shard1 = ShardLoader(small_qwen_first_shard, device="cpu").load()
    shard2 = ShardLoader(small_qwen_last_shard, device="cpu").load()

    input_ids = torch.tensor([[1, 2, 3]], dtype=torch.long)
    hidden, kv1 = shard1.forward(input_ids)
    logits, kv2 = shard2.forward(hidden)

    # logits shape: [batch, seq_len, vocab_size]
    assert logits.shape[0] == 1
    assert logits.shape[1] == 3
    assert logits.shape[2] > 100  # vocab size


@pytest.mark.slow
def test_qwen_output_matches_reference():
    """Distributed output matches single-model output for Qwen2.5-0.5B."""
    from transformers import AutoModelForCausalLM

    model_id = "Qwen/Qwen2.5-0.5B-Instruct"

    ref_model = AutoModelForCausalLM.from_pretrained(model_id, dtype=torch.bfloat16).to(
        "cpu"
    )
    input_ids = torch.tensor([[1, 2, 3]], dtype=torch.long)
    with torch.no_grad():
        ref_logits = ref_model(input_ids).logits

    s1 = ShardLoader(
        ShardConfig(model_id, ModelFamily.QWEN, 0, 12, 24, "float16"), "cpu"
    ).load()
    s2 = ShardLoader(
        ShardConfig(model_id, ModelFamily.QWEN, 12, 24, 24, "float16"), "cpu"
    ).load()

    with torch.no_grad():
        h, _ = s1.forward(input_ids)
        dist_logits, _ = s2.forward(h)

    # Allow small tolerance for bfloat16 vs reference precision
    print(ref_logits.dtype, h.dtype, dist_logits.dtype)
    assert torch.allclose(ref_logits, dist_logits, atol=1e-2)


def test_model_shard_forward_passes_cache_with_current_transformers_kwarg_names():
    import torch
    import torch.nn as nn

    from inference.ShardConfig import ModelFamily, ShardConfig
    from inference.ShardLoader import ModelShard

    class FakeCache:
        def __init__(self, seq_len=5):
            self.seq_len = seq_len

        def get_seq_length(self):
            return self.seq_len

    class FakeLayer(nn.Module):
        def __init__(self):
            super().__init__()
            self.calls = []

        def forward(
            self,
            hidden_states,
            attention_mask=None,
            position_ids=None,
            past_key_values=None,
            use_cache=False,
            cache_position=None,
            position_embeddings=None,
            **kwargs,
        ):
            self.calls.append(
                {
                    "past_key_values": past_key_values,
                    "cache_position": (
                        cache_position.clone() if cache_position is not None else None
                    ),
                    "position_ids": (
                        position_ids.clone() if position_ids is not None else None
                    ),
                    "unexpected_kwargs": dict(kwargs),
                }
            )
            return (hidden_states,)

    cfg = ShardConfig(
        model_id="Qwen/Qwen2.5-0.5B-Instruct",
        family=ModelFamily.QWEN,
        start_layer=0,
        end_layer=1,
        total_layers=1,
        dtype="float16",
    )
    layer = FakeLayer()
    shard = ModelShard(
        config=cfg,
        layers=nn.ModuleList([layer]),
        embed_tokens=nn.Embedding(32, 4),
        norm=None,
        lm_head=None,
        rotary_emb=None,
        model_config=None,
    )
    cache = FakeCache(seq_len=5)
    x = torch.tensor([[1]])

    shard.forward(x, past_key_values=cache)

    assert len(layer.calls) == 1
    assert layer.calls[0]["past_key_values"] is cache
    assert layer.calls[0]["unexpected_kwargs"] == {}
    assert layer.calls[0]["position_ids"].tolist() == [[5]]
    assert layer.calls[0]["cache_position"].tolist() == [5]
