import pytest

from inference.DistributedEngine import DistributedInferenceEngine
from inference.ShardConfig import ModelFamily, ShardConfig


@pytest.mark.slow
def test_distributed_two_process_simulation():
    """Simulate 2-node inference in same process."""
    engine = DistributedInferenceEngine(
        model_id="Qwen/Qwen2.5-0.5B-Instruct",
        family=ModelFamily.QWEN,
        shard_configs=[
            ShardConfig(
                "Qwen/Qwen2.5-0.5B-Instruct",
                ModelFamily.QWEN,
                0,
                12,
                24,
                "float16",
            ),
            ShardConfig(
                "Qwen/Qwen2.5-0.5B-Instruct",
                ModelFamily.QWEN,
                12,
                24,
                24,
                "float16",
            ),
        ],
        device="cpu",
    )

    tokens = list(engine.generate_stream("Hello", max_tokens=5))
    assert len(tokens) >= 1


def test_distributed_engine_defers_tokenizer_loading(monkeypatch):
    import inference.DistributedEngine as dist_mod

    class FakeShard:
        def forward(self, x, past_key_values=None):
            return x, past_key_values

    class FakeLoader:
        def __init__(self, cfg, device="cpu"):
            self.cfg = cfg
            self.device = device

        def load(self):
            return FakeShard()

    calls = []
    dummy_tokenizer = object()

    def fake_from_pretrained(*args, **kwargs):
        calls.append((args, kwargs))
        return dummy_tokenizer

    monkeypatch.setattr(dist_mod, "ShardLoader", FakeLoader)
    monkeypatch.setattr(dist_mod.AutoTokenizer, "from_pretrained", fake_from_pretrained)

    engine = DistributedInferenceEngine(
        model_id="Qwen/Qwen2.5-0.5B-Instruct",
        family=ModelFamily.QWEN,
        shard_configs=[
            ShardConfig(
                "Qwen/Qwen2.5-0.5B-Instruct",
                ModelFamily.QWEN,
                0,
                24,
                24,
                "float16",
            ),
        ],
        device="cpu",
    )

    assert engine._tokenizer is None
    assert calls == []

    tokenizer = engine._get_tokenizer()

    assert tokenizer is dummy_tokenizer
    assert len(calls) == 1
