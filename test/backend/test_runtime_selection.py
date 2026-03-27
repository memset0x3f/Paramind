import asyncio
import sys
import time
import types
from types import SimpleNamespace

from paramind.apps.desktop.python.app.inference import InferenceRunner


def _make_settings(**overrides):
    base = dict(
        test_mode=False,
        model_id="Qwen/Qwen2.5-0.5B-Instruct",
        family="qwen",
        device="cpu",
        mock_inference_delay=0.0,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_desktop_runtime_can_import_root_inference_entrypoints():
    from inference import LocalInferenceEngine, ModelFamily

    assert LocalInferenceEngine.__name__ == "LocalInferenceEngine"
    assert ModelFamily.QWEN.value == "qwen"


def test_inference_runner_uses_real_engine_when_available(monkeypatch):
    calls = {}

    class FakeEngine:
        def __init__(self, model_id, family, device):
            calls["model_id"] = model_id
            calls["family"] = family
            calls["device"] = device

        def generate_stream(self, prompt, max_tokens=80):
            yield "real"
            yield ":"
            yield prompt

    fake_inference = types.ModuleType("inference")
    fake_inference.LocalInferenceEngine = FakeEngine
    fake_inference.ModelFamily = lambda value: f"resolved:{value}"
    monkeypatch.setitem(sys.modules, "inference", fake_inference)
    monkeypatch.delenv("PARAMIND_FORCE_MOCK_AI", raising=False)

    runner = InferenceRunner(_make_settings())

    async def collect():
        return [token async for token in runner.stream_tokens("hello")]

    tokens = asyncio.run(collect())

    assert tokens == ["real", ":", "hello"]
    assert calls == {
        "model_id": "Qwen/Qwen2.5-0.5B-Instruct",
        "family": "resolved:qwen",
        "device": "cpu",
    }


def test_inference_runner_raises_when_real_engine_cannot_initialize(monkeypatch):
    class FailingEngine:
        def __init__(self, *args, **kwargs):
            raise RuntimeError("boom")

    fake_inference = types.ModuleType("inference")
    fake_inference.LocalInferenceEngine = FailingEngine
    fake_inference.ModelFamily = lambda value: value
    monkeypatch.setitem(sys.modules, "inference", fake_inference)
    monkeypatch.delenv("PARAMIND_FORCE_MOCK_AI", raising=False)

    runner = InferenceRunner(_make_settings())

    async def collect():
        return [token async for token in runner.stream_tokens("hello desktop")]

    import pytest

    with pytest.raises(RuntimeError, match="boom"):
        asyncio.run(collect())


def test_inference_runner_keeps_event_loop_responsive_during_sync_generation(monkeypatch):
    class SlowEngine:
        def __init__(self, *args, **kwargs):
            pass

        def generate_stream(self, prompt, max_tokens=80):
            time.sleep(0.2)
            yield "slow"
            time.sleep(0.2)
            yield "-"
            time.sleep(0.2)
            yield prompt

    fake_inference = types.ModuleType("inference")
    fake_inference.LocalInferenceEngine = SlowEngine
    fake_inference.ModelFamily = lambda value: value
    monkeypatch.setitem(sys.modules, "inference", fake_inference)
    monkeypatch.delenv("PARAMIND_FORCE_MOCK_AI", raising=False)

    runner = InferenceRunner(_make_settings())
    ticks = 0

    async def ticker():
        nonlocal ticks
        for _ in range(5):
            await asyncio.sleep(0.05)
            ticks += 1

    async def collect():
        return [token async for token in runner.stream_tokens("hello")]

    async def main():
        return await asyncio.gather(collect(), ticker())

    tokens, _ = asyncio.run(main())

    assert tokens == ["slow", "-", "hello"]
    assert ticks == 5
