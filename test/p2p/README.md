# P2P Test Group

## What this module is

`test/p2p/` validates the transport-side behavior of the peer-to-peer layer, especially direct P2P communication and reliable transport behavior on top of the repo’s transport primitives.

## Directory structure

```text
test/p2p/
├── test_p2p.py
└── test_reliable_transport.py
```

## Key responsibilities / boundaries

This group covers:
- P2P communication behavior,
- transport-side reliability expectations such as resend/recovery behavior.

It does **not** serve as the main home for planner policy or inference runtime correctness.

## How to run / verify

```bash
python3.11 -m pytest -q --tb=short test/p2p
```

Some tests may depend on signaling configuration, reachable networking conditions, or transport environment assumptions. If you need purely controlled planning/execution checks, use the scheduler or inference test groups instead.

## Dependencies / related modules

- Transport module: [`../../p2p/README.md`](../../p2p/README.md)
- Shared helpers: [`../../common/README.md`](../../common/README.md)

## Current status / limitations

This group is intentionally narrow. It is useful for transport-specific verification, but it does not by itself prove end-to-end distributed inference correctness.
