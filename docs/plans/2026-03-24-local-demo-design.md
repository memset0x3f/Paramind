# Local Demo Design

## Goal

Add a minimal local CLI demo that shows what the current uncommitted inference stack can already do:

1. single-node local inference
2. 2-shard distributed simulation inference

The demo must print explicit loading information before generation and then run a real inference pass.

## Recommended approach

Create a small script at `scripts/local_demo.py` with one CLI entry point and two execution paths:

- `local`: uses `LocalInferenceEngine`
- `distributed-sim`: uses `DistributedInferenceEngine`
- `both`: runs the two demos back-to-back

## Why this approach

- Reuses the current engines directly, so the demo reflects the real current state
- Keeps changes localized and reviewable
- Gives the user one obvious command to run
- Avoids coupling demo-specific printing into the core inference classes

## Output contract

The script should print:

- demo mode
- model id
- model family
- device
- total layer count
- local mode loaded layer count
- distributed simulation shard ranges and per-shard layer counts
- prompt
- streaming output
- final concatenated output

## Testing strategy

Add a focused CLI test that monkeypatches the two engine classes so the demo output contract can be verified without downloading or loading a real model.

## Constraints

- No dependency changes
- No broad refactors
- Keep the demo CPU-friendly by defaulting to `Qwen/Qwen2.5-0.5B-Instruct`
