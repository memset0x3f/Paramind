# P2P Module

## What this module is

`p2p/` contains the transport-side client and socket primitives used to turn independent peers into an operational communication path. It is the networking counterpart to planning and execution: this module helps peers discover one another and exchange control or tensor payloads, but it does not decide placement and it does not execute model-specific inference logic.

## Directory structure

```text
p2p/
├── __init__.py
├── P2PClient.py   # signaling integration, peer table, and message dispatch
└── PeerSocket.py  # direct peer send/receive, tensor chunk buffering, resend support
```

## Key responsibilities / boundaries

This module owns:
- signaling-server connection and peer membership updates,
- endpoint selection and peer-address usage,
- UDP transport of control payloads and tensor chunks,
- timeout/NACK-based recovery for chunked tensor delivery.

This module does **not** own:
- cluster placement decisions (see [`../scheduler/README.md`](../scheduler/README.md)),
- shard loading or runtime shard lifecycle (see [`../inference/README.md`](../inference/README.md)),
- desktop application session state.

## Key entrypoints or important files

- `P2PClient.py`: top-level peer client, signaling handlers, and runtime handler registration
- `PeerSocket.py`: direct peer transport, torch-chunk buffering, and retransmission handling

## How to run / verify

There is no single standalone CLI entrypoint in this directory. The most relevant verification paths are:

```bash
python3.11 -m pytest -q --tb=short test/p2p
python3.11 demo.py
```

Use [`../test/p2p/README.md`](../test/p2p/README.md) to understand which tests are transport-focused and which require an external signaling environment.

## Dependencies / related modules

- Shared helpers from [`../common/README.md`](../common/README.md)
- Runtime integration with [`../inference/README.md`](../inference/README.md)
- Membership and profile inputs from [`../scheduler/README.md`](../scheduler/README.md)

## Current status / limitations

`p2p/` is still a narrower transport-focused module than the newer scheduler/inference split. It is useful for understanding how messages move across peers, but it is not the best first stop for overall system behavior. Treat it as the communication substrate, not as the authoritative description of cluster policy.
