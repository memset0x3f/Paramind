# Common Test Group

## What this module is

`test/common/` validates the small shared abstractions in `common/`, such as shared concepts, constants, and helper behavior that multiple subsystems depend on.

## Directory structure

```text
test/common/
└── test_common.py
```

## Key responsibilities / boundaries

This group covers the shared layer only. It does **not** replace subsystem-specific tests for inference, scheduling, or transport.

## How to run / verify

```bash
python3.11 -m pytest -q --tb=short test/common
```

## Dependencies / related modules

- Shared module: [`../../common/README.md`](../../common/README.md)

## Current status / limitations

This is a small test group because `common/` is intentionally small. Its role is to catch regressions in foundational helpers, not to provide broad end-to-end coverage.
