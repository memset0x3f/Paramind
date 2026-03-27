# Local Demo Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a local CLI demo that prints loading diagnostics and runs both single-node and 2-shard simulated inference.

**Architecture:** Add one small script that reuses `LocalInferenceEngine`, `DistributedInferenceEngine`, `ModelRegistry`, and `ShardConfig.plan_split`. Keep demo printing outside the core engine classes so the feature stays localized.

**Tech Stack:** Python, argparse, current inference module, pytest

---

### Task 1: Define the demo output contract

**Files:**
- Create: `test/inference/test_local_demo.py`

**Step 1: Write the failing test**

Create a CLI-focused test that monkeypatches local/distributed engines and asserts the demo prints:

- the local demo heading
- the distributed simulation heading
- total layers
- local loaded layers
- shard ranges
- final output

**Step 2: Run test to verify it fails**

Run: `pytest test/inference/test_local_demo.py -v`

Expected: FAIL because `scripts.local_demo` does not exist yet.

### Task 2: Implement the minimal demo script

**Files:**
- Create: `scripts/__init__.py`
- Create: `scripts/local_demo.py`

**Step 1: Add a small CLI**

Support:

- `--mode` with `local`, `distributed-sim`, `both`
- `--model-id`
- `--family`
- `--device`
- `--prompt`
- `--max-tokens`
- `--num-shards`

**Step 2: Reuse existing inference engines**

- `local` mode: load the full model with `LocalInferenceEngine`
- `distributed-sim` mode: build shard configs with `ShardConfig.plan_split(...)` and load `DistributedInferenceEngine`

**Step 3: Print explicit demo diagnostics**

Print:

- total layers
- local loaded layer span
- distributed shard plan
- prompt
- streaming output
- final output

### Task 3: Verify with targeted tests and a real smoke run

**Files:**
- Modify: `test/inference/test_local_demo.py` if needed

**Step 1: Run targeted tests**

Run:

```bash
pytest test/inference/test_local_demo.py -v
```

Expected: PASS

**Step 2: Run a real smoke demo**

Run:

```bash
python -m scripts.local_demo --mode both --model-id Qwen/Qwen2.5-0.5B-Instruct --family qwen --device cpu --max-tokens 4 --prompt "用一句话介绍你自己"
```

Expected:

- local demo prints full-model layer coverage and generates tokens
- distributed simulation prints shard ranges and generates tokens
