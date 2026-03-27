import asyncio
from types import SimpleNamespace

from paramind.apps.desktop.python.app.inference import InferenceRunner


class _FakeEngine:
    def generate_stream(self, prompt: str, max_tokens: int = 0):
        assert prompt == "hello"
        assert max_tokens == 1024
        yield "hi"


def test_stream_tokens_uses_higher_generation_cap():
    settings = SimpleNamespace(
        test_mode=False,
        family="qwen",
        model_id="fake",
        device="cpu",
        mock_inference_delay=0.0,
    )
    runner = InferenceRunner(settings)
    runner._engine = _FakeEngine()
    async def collect():
        tokens = []
        async for token in runner.stream_tokens("hello"):
            tokens.append(token)
        return tokens

    assert asyncio.run(collect()) == ["hi"]


def test_preload_is_safe_in_test_mode():
    settings = SimpleNamespace(
        test_mode=True,
        family="qwen",
        model_id="fake",
        device="cpu",
        mock_inference_delay=0.0,
    )
    runner = InferenceRunner(settings)

    asyncio.run(runner.preload())
    assert runner._engine is None


def test_stream_tokens_raises_when_engine_preload_failed_instead_of_using_mock_tokens():
    settings = SimpleNamespace(
        test_mode=False,
        family="qwen",
        model_id="fake",
        device="cpu",
        mock_inference_delay=0.0,
    )
    runner = InferenceRunner(settings)
    runner._engine_error = RuntimeError("engine unavailable")

    async def collect():
        tokens = []
        async for token in runner.stream_tokens("介绍你自己"):
            tokens.append(token)
        return tokens

    import pytest
    with pytest.raises(RuntimeError, match="engine unavailable"):
        asyncio.run(collect())
