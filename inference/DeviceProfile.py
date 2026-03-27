from __future__ import annotations

import math

from inference.ClusterTypes import NodeProfile
from inference.NodeInventory import (
    NodeState,
    _detect_device_type,
    _detect_memory_gb as _inventory_detect_memory_gb,
    benchmark_block_speed,
    build_node_state,
    from_local_snapshot,
)


def _detect_device() -> str:
    return _detect_device_type()


def _detect_memory_gb() -> tuple[float, float]:
    return _inventory_detect_memory_gb()


def snapshot_local_node_profile(
    node_id: str,
    host: str | None = None,
    loaded_shards: list[tuple[int, int]] | None = None,
) -> NodeProfile:
    device = _detect_device()
    total_memory_gb, free_memory_gb = _detect_memory_gb()
    state = NodeState(
        node_id=node_id,
        host=host or node_id,
        device_type=device,
        total_memory_gb=total_memory_gb,
        free_memory_gb=free_memory_gb,
        max_blocks_capacity=max(int(math.floor(free_memory_gb)), 0),
        block_throughput=benchmark_block_speed(device),
        loaded_ranges=loaded_shards or [],
    )
    return NodeProfile(
        node_id=state.node_id,
        host=state.host,
        device=state.device_type,
        total_memory_gb=state.total_memory_gb,
        free_memory_gb=state.free_memory_gb,
        compute_score=state.effective_speed(),
        loaded_shards=state.loaded_ranges,
    )


def build_node_profile(
    node_id: str,
    host: str | None = None,
    loaded_shards: list[tuple[int, int]] | None = None,
) -> NodeProfile:
    return snapshot_local_node_profile(
        node_id=node_id,
        host=host,
        loaded_shards=loaded_shards,
    )


def snapshot_local_node_state(
    node_id: str,
    host: str | None = None,
    kv_headroom_gb: float = 0.0,
    loaded_ranges: list[tuple[int, int]] | None = None,
):
    return from_local_snapshot(
        node_id=node_id,
        host=host,
        kv_headroom_gb=kv_headroom_gb,
        loaded_ranges=loaded_ranges,
    )


def build_node_state_profile(
    node_id: str,
    host: str | None = None,
    kv_headroom_gb: float = 0.0,
    loaded_ranges: list[tuple[int, int]] | None = None,
):
    return build_node_state(
        node_id=node_id,
        host=host,
        kv_headroom_gb=kv_headroom_gb,
        loaded_ranges=loaded_ranges,
    )
