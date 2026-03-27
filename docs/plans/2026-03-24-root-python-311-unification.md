# Root Python 3.11 Unification Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Standardize the repository root workflow on Python 3.11 without touching the desktop subproject or changing dependency versions.

**Architecture:** Add one root-level command entrypoint that always shells through `python3.11`, then document install, run, and test commands around that entrypoint. Keep the change localized to root-facing workflow files so existing in-progress feature work is not disturbed.

**Tech Stack:** Python 3.11, pip, pytest, Make

---

### Task 1: Add a root workflow entrypoint

**Files:**
- Create: `Makefile`

**Step 1: Add explicit Python 3.11 command aliases**
- Define `PYTHON ?= python3.11`
- Define install / test / demo / p2p targets using `$(PYTHON)`

**Step 2: Keep the target set small**
- `install`: `$(PYTHON) -m pip install -r requirements.txt`
- `test-inference`: `$(PYTHON) -m pytest test/inference -v`
- `test-backend`: `$(PYTHON) -m pytest test/backend -v`
- `local-demo`: `$(PYTHON) scripts/local_demo.py`
- `p2p-demo`: `$(PYTHON) main.py`
- `help`: print the canonical commands

**Step 3: Verify the entrypoint parses**
Run: `make help`
Expected: target list prints and every command references `python3.11`

### Task 2: Add concise root documentation

**Files:**
- Create: `README.md`

**Step 1: Document the root standard**
- State that the repository root currently standardizes on Python 3.11
- Explicitly note that this does not change `paramind/apps/desktop`

**Step 2: Document canonical commands**
- Install: `make install`
- Inference tests: `make test-inference`
- Backend tests: `make test-backend`
- Local demo: `make local-demo`
- P2P demo: `make p2p-demo`

**Step 3: Note why `python3` is avoided**
- Explain that the current machine default points at Python 3.14, while the root project dependencies are presently working on Python 3.11

### Task 3: Verify and summarize

**Files:**
- Modify: none

**Step 1: Run lightweight verification**
Run: `make help`
Expected: PASS

**Step 2: Optionally dry-run one target command**
Run: `make -n test-inference`
Expected: printed command begins with `python3.11 -m pytest`

**Step 3: Report status**
- Summarize changed files
- Include verification output
- Include `git status --short`
