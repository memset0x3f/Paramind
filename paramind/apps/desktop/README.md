# ParaMind desktop app

This directory contains the Electron shell, renderer, and FastAPI backend for the ParaMind desktop demo.

## Frontend testing workflow

Use the browser harness by default when you are working on renderer behavior.

### Fast loop — no Electron required

From the repo root:

```bash
make test-frontend-fast
```

This runs:
- renderer state tests in `/Users/acropolis/Github_Project/Paramind/test/backend/test_chat_state.mjs`
- harness unit tests in `/Users/acropolis/Github_Project/Paramind/test/backend/test_renderer_harness.mjs`
- Playwright browser tests in `/Users/acropolis/Github_Project/Paramind/test/backend/test_renderer_harness.spec.mjs`

### Open the renderer harness manually

```bash
make open-frontend-harness
```

Then open:

- `http://127.0.0.1:4173/dev_harness.html?harness=1`

Useful query fixtures:
- `?fixture=bootstrap_two_peers`
- `?fixture=accepted_dm`
- `?fixture=ai_streaming_draft`
- `?fixture=published_ai_message`

### When to use Electron smoke instead

Use the smoke path only when your change depends on:
- preload bridge behavior
- backend bootstrap payload shape
- real inference startup
- final integration before merge

```bash
make test-frontend-smoke
```

## Files added for the frontend-first test loop

- `/Users/acropolis/Github_Project/Paramind/paramind/apps/desktop/renderer/dev_harness.html`
- `/Users/acropolis/Github_Project/Paramind/paramind/apps/desktop/renderer/dev_harness.js`
- `/Users/acropolis/Github_Project/Paramind/paramind/apps/desktop/renderer/harness_fixtures.js`
- `/Users/acropolis/Github_Project/Paramind/test/backend/test_renderer_harness.mjs`
- `/Users/acropolis/Github_Project/Paramind/test/backend/test_renderer_harness.spec.mjs`
- `/Users/acropolis/Github_Project/Paramind/paramind/apps/desktop/playwright.config.mjs`

## 打包运行时

desktop 打包不再依赖开发机 `.venv` 或 repo root。Python 运行时现在约定为：

- `python-dist/`：portable Python 发行版
- `python/`：desktop backend/coordinator 源码

构建 portable Python：

```bash
cd /Users/acropolis/Github_Project/Paramind/paramind/apps/desktop
npm run build:python
```

打包后的 Electron 主进程会在 packaged 模式下从 `process.resourcesPath` 解析：

- `python-dist/bin/python3`
- `python/backend.py`
- `python/app/coordinator.py`

开发态和打包态都通过 `PYTHONPATH=<...>/python` 加载 desktop 本地 Python 包，不再依赖 `paramind.apps.desktop.python...` 的源码树包路径。
