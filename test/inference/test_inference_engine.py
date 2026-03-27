import pytest

from inference.InferenceEngine import LocalInferenceEngine
from inference.ShardConfig import ModelFamily


@pytest.mark.slow
def test_local_inference_streaming():
    """Single-node inference produces streaming tokens."""
    engine = LocalInferenceEngine(
        model_id="Qwen/Qwen2.5-0.5B-Instruct",
        family=ModelFamily.QWEN,
        device="cpu",
    )

    tokens = list(engine.generate_stream("Hello", max_tokens=5))

    assert len(tokens) >= 1
    assert all(isinstance(t, str) for t in tokens)


@pytest.mark.slow
def test_local_inference_full_response():
    """Can collect full response."""
    engine = LocalInferenceEngine(
        model_id="Qwen/Qwen2.5-0.5B-Instruct",
        family=ModelFamily.QWEN,
        device="cpu",
    )

    response = engine.generate("Why is sky blue?", max_tokens=20)

    assert isinstance(response, str)
    assert len(response) > 0
