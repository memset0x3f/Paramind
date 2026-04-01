from __future__ import annotations

import asyncio
import os
import threading


_END = object()


class InferenceRunner:
    def __init__(self, settings):
        self.settings = settings
        self._engine = None
        self._engine_error = None
        self._load_lock = threading.Lock()

    def _load_engine(self):
        if self._engine is not None or self._engine_error is not None:
            return self._engine

        with self._load_lock:
            if self._engine is not None or self._engine_error is not None:
                return self._engine

            if self.settings.test_mode or os.environ.get("PARAMIND_FORCE_MOCK_AI", "0") == "1":
                self._engine_error = RuntimeError("mock inference requested")
                return None

            try:
                from inference import LocalInferenceEngine, ModelFamily

                family = ModelFamily(self.settings.family)
                self._engine = LocalInferenceEngine(
                    model_id=self.settings.model_id,
                    family=family,
                    device=self.settings.device,
                )
                return self._engine
            except Exception as exc:  # pragma: no cover - environment dependent
                self._engine_error = exc
                return None

    async def preload(self):
        await asyncio.to_thread(self._load_engine)

    async def stream_tokens(self, prompt: str):
        engine = await asyncio.to_thread(self._load_engine)
        if engine is None:
            if self._engine_error is not None and not (
                self.settings.test_mode or os.environ.get("PARAMIND_FORCE_MOCK_AI", "0") == "1"
            ):
                raise self._engine_error
            async for cumulative_text in self._mock_stream(prompt):
                yield cumulative_text
            return

        async for cumulative_text in self._threaded_generate(engine, prompt):
            yield cumulative_text

    async def _threaded_generate(self, engine, prompt: str):
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[object] = asyncio.Queue()

        def worker():
            try:
                for cumulative_text in engine.generate_stream(prompt, max_tokens=1024):
                    loop.call_soon_threadsafe(queue.put_nowait, cumulative_text)
            except Exception as exc:  # pragma: no cover - environment dependent
                loop.call_soon_threadsafe(queue.put_nowait, exc)
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, _END)

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()

        while True:
            item = await queue.get()
            if item is _END:
                break
            if isinstance(item, Exception):
                raise item
            yield item

    async def _mock_stream(self, prompt: str):
        if "hello" in prompt.lower():
            pieces = ["Hello", " ", "back"]
        elif "介绍" in prompt or "你自己" in prompt:
            pieces = ["我是", " ", "ParaMind"]
        else:
            pieces = ["已收到", "：", prompt[:18] or "消息"]

        acc = ""
        for piece in pieces:
            if self.settings.mock_inference_delay:
                await asyncio.sleep(self.settings.mock_inference_delay)
            acc += piece
            yield acc
