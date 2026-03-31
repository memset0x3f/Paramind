from scheduler.ClusterPlanner import (
    _arrange_nodes_for_pipeline,
    choose_coordinator,
    coordinator_score,
    estimate_reference_stage_width,
    estimate_stage_time_for_node,
)
from scheduler.ClusterTypes import PlacementPlan, ShardAssignment
from scheduler.NodeInventory import NodeState


def _node(node_id: str, speed: float, capacity: int, free_memory_gb: float = 32.0):
    return NodeState(
        node_id=node_id,
        host=f"{node_id}.local",
        device_type="cpu",
        total_memory_gb=64.0,
        free_memory_gb=free_memory_gb,
        max_blocks_capacity=capacity,
        block_throughput=speed,
    )


def test_faster_node_has_lower_stage_time_for_same_width():
    fast = _node("fast", speed=20.0, capacity=24)
    slow = _node("slow", speed=10.0, capacity=24)

    fast_time = estimate_stage_time_for_node(fast, width=8, total_layers=24)
    slow_time = estimate_stage_time_for_node(slow, width=8, total_layers=24)

    assert fast_time < slow_time


def test_more_capacity_does_not_worsen_reference_coordinator_score():
    smaller = _node("small", speed=20.0, capacity=8, free_memory_gb=8.0)
    larger = _node("large", speed=20.0, capacity=16, free_memory_gb=16.0)

    small_score = coordinator_score(smaller, total_layers=24, active_nodes=3)
    large_score = coordinator_score(larger, total_layers=24, active_nodes=3)

    assert large_score <= small_score


def test_choose_coordinator_is_deterministic_for_fixed_profiles():
    nodes = [
        _node("node-a", speed=24.0, capacity=12),
        _node("node-b", speed=18.0, capacity=12),
        _node("node-c", speed=12.0, capacity=12),
    ]

    assert choose_coordinator(nodes, total_layers=24) == "node-a"
    assert choose_coordinator(nodes, total_layers=24) == "node-a"


def test_plan_aware_coordinator_score_can_differ_from_reference_fallback():
    node_a = _node("node-a", speed=30.0, capacity=24)
    node_b = _node("node-b", speed=20.0, capacity=24)
    node_c = _node("node-c", speed=12.0, capacity=24)
    plan = PlacementPlan(
        model_id="Qwen/Qwen2.5-0.5B-Instruct",
        assignments=[
            ShardAssignment("node-a", 0, 4, role="first"),
            ShardAssignment("node-b", 4, 12, role="middle"),
            ShardAssignment("node-c", 12, 24, role="last"),
        ],
    )

    reference_score = coordinator_score(node_a, total_layers=24, active_nodes=3)
    plan_aware_score = coordinator_score(
        node_a,
        total_layers=24,
        active_nodes=3,
        current_plan=plan,
    )

    assert plan_aware_score != reference_score


def test_reference_width_scales_with_layer_count_and_active_nodes():
    assert estimate_reference_stage_width(total_layers=24, active_nodes=3) == 8
    assert estimate_reference_stage_width(total_layers=25, active_nodes=4) == 7


def test_pipeline_order_uses_stage_time_not_legacy_quality_product():
    nodes = [
        _node("product-winner", speed=90.0, capacity=2, free_memory_gb=2.0),
        _node("balanced", speed=20.0, capacity=8, free_memory_gb=8.0),
        _node("capacity-heavy", speed=22.0, capacity=12, free_memory_gb=12.0),
    ]

    ordered = _arrange_nodes_for_pipeline(nodes, total_layers=24)

    assert ordered[0].node_id == "capacity-heavy"
    assert ordered[0].node_id != "product-winner"
