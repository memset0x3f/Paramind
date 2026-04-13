from scheduler.ClusterPlanner import (
    plan_join_distribution,
    plan_drop_distribution,
    plan_static_distribution,
    replan_distribution,
)
from scheduler.ClusterTypes import PlacementPlan, ShardAssignment
from scheduler.NodeInventory import NodeState


def _assert_contiguous_cover(plan, total_layers):
    pairs = [
        (assignment.start_layer, assignment.end_layer)
        for assignment in plan.assignments
    ]
    assert pairs[0][0] == 0
    assert pairs[-1][1] == total_layers
    for left, right in zip(pairs, pairs[1:]):
        assert left[1] == right[0]


def test_cold_start_greedy_fills_faster_node_first():
    nodes = [
        NodeState(
            "fast",
            "10.0.0.1",
            "cuda",
            64.0,
            48.0,
            max_blocks_capacity=24,
            block_latency_ms=8.0,
        ),
        NodeState(
            "slow",
            "10.0.0.2",
            "cpu",
            32.0,
            24.0,
            max_blocks_capacity=24,
            block_latency_ms=32.0,
        ),
    ]

    plan = plan_static_distribution(
        model_id="Qwen/Qwen2.5-0.5B-Instruct",
        total_layers=24,
        nodes=nodes,
    )

    sizes = {
        assignment.node_id: assignment.num_layers for assignment in plan.assignments
    }
    assert sizes == {"fast": 24}
    _assert_contiguous_cover(plan, 24)


def test_cold_start_greedy_skips_weak_middle_when_strong_nodes_cover_model():
    nodes = [
        NodeState(
            "strong-a",
            "10.0.0.1",
            "cuda",
            64.0,
            48.0,
            max_blocks_capacity=12,
            block_latency_ms=8.0,
        ),
        NodeState(
            "weak",
            "10.0.0.2",
            "cpu",
            32.0,
            24.0,
            max_blocks_capacity=12,
            block_latency_ms=40.0,
        ),
        NodeState(
            "strong-b",
            "10.0.0.3",
            "cuda",
            64.0,
            48.0,
            max_blocks_capacity=12,
            block_latency_ms=9.0,
        ),
    ]

    plan = plan_static_distribution(
        model_id="Qwen/Qwen2.5-0.5B-Instruct",
        total_layers=24,
        nodes=nodes,
    )

    first = next(
        assignment for assignment in plan.assignments if assignment.role == "first"
    )
    last = next(
        assignment for assignment in plan.assignments if assignment.role == "last"
    )

    assert {first.node_id, last.node_id} == {"strong-a", "strong-b"}
    _assert_contiguous_cover(plan, 24)


def test_dp_cold_start_rejects_impossible_capacity():
    nodes = [
        NodeState(
            "node-a",
            "10.0.0.1",
            "cpu",
            32.0,
            8.0,
            max_blocks_capacity=4,
            block_latency_ms=20.0,
        ),
        NodeState(
            "node-b",
            "10.0.0.2",
            "cpu",
            32.0,
            8.0,
            max_blocks_capacity=4,
            block_latency_ms=20.0,
        ),
    ]

    try:
        plan_static_distribution(
            model_id="Qwen/Qwen2.5-0.5B-Instruct",
            total_layers=24,
            nodes=nodes,
        )
    except ValueError as exc:
        assert "capacity" in str(exc).lower()
    else:
        raise AssertionError("expected ValueError for impossible capacity")


def test_dp_cold_start_single_node_gets_full_range():
    nodes = [
        NodeState(
            "solo",
            "10.0.0.1",
            "cuda",
            64.0,
            48.0,
            max_blocks_capacity=24,
            block_latency_ms=10.0,
        ),
    ]

    plan = plan_static_distribution(
        model_id="Qwen/Qwen2.5-0.5B-Instruct",
        total_layers=24,
        nodes=nodes,
    )

    assert [
        (assignment.node_id, assignment.start_layer, assignment.end_layer)
        for assignment in plan.assignments
    ] == [("solo", 0, 24)]


def test_replanner_keeps_boundaries_when_departing_node_was_not_in_current_plan():
    current = plan_static_distribution(
        model_id="Qwen/Qwen2.5-0.5B-Instruct",
        total_layers=24,
        nodes=[
            NodeState(
                "a",
                "10.0.0.1",
                "cuda",
                64.0,
                48.0,
                max_blocks_capacity=12,
                block_latency_ms=8.0,
            ),
            NodeState(
                "b",
                "10.0.0.2",
                "cpu",
                32.0,
                24.0,
                max_blocks_capacity=12,
                block_latency_ms=16.0,
            ),
            NodeState(
                "c",
                "10.0.0.3",
                "cuda",
                64.0,
                48.0,
                max_blocks_capacity=12,
                block_latency_ms=10.0,
            ),
        ],
    )

    replanned = replan_distribution(
        current=current,
        nodes=[
            NodeState(
                "a",
                "10.0.0.1",
                "cuda",
                64.0,
                48.0,
                max_blocks_capacity=16,
                block_latency_ms=8.0,
                loaded_ranges=[(0, 8)],
            ),
            NodeState(
                "c",
                "10.0.0.3",
                "cuda",
                64.0,
                48.0,
                max_blocks_capacity=16,
                block_latency_ms=10.0,
                loaded_ranges=[(16, 24)],
            ),
        ],
        total_layers=24,
    )

    assert len(replanned.assignments) == 2
    assert [
        (assignment.node_id, assignment.start_layer, assignment.end_layer)
        for assignment in replanned.assignments
    ] == [
        (assignment.node_id, assignment.start_layer, assignment.end_layer)
        for assignment in current.assignments
    ]
    _assert_contiguous_cover(replanned, 24)


def test_drop_repair_lets_surviving_later_node_absorb_missing_range():
    current = PlacementPlan(
        model_id="Qwen/Qwen2.5-0.5B-Instruct",
        coordinator_id="a",
        assignments=[
            ShardAssignment("a", 0, 12, role="first"),
            ShardAssignment("b", 12, 18, role="middle"),
            ShardAssignment("c", 18, 24, role="last"),
        ],
    )

    repaired = plan_drop_distribution(
        current=current,
        nodes=[
            NodeState(
                "a",
                "10.0.0.1",
                "cuda",
                64.0,
                48.0,
                max_blocks_capacity=12,
                block_latency_ms=8.0,
            ),
            NodeState(
                "c",
                "10.0.0.3",
                "cpu",
                32.0,
                24.0,
                max_blocks_capacity=12,
                block_latency_ms=20.0,
            ),
        ],
        total_layers=24,
    )

    assert [
        (assignment.node_id, assignment.start_layer, assignment.end_layer)
        for assignment in repaired.assignments
    ] == [
        ("a", 0, 12),
        ("c", 12, 24),
    ]
    _assert_contiguous_cover(repaired, 24)


def test_drop_repair_inserts_standby_into_remaining_gap_after_local_absorption():
    current = PlacementPlan(
        model_id="Qwen/Qwen2.5-0.5B-Instruct",
        coordinator_id="a",
        assignments=[
            ShardAssignment("a", 0, 14, role="first"),
            ShardAssignment("b", 14, 20, role="middle"),
            ShardAssignment("c", 20, 24, role="last"),
        ],
    )

    repaired = plan_drop_distribution(
        current=current,
        nodes=[
            NodeState(
                "a",
                "10.0.0.1",
                "cuda",
                64.0,
                48.0,
                max_blocks_capacity=14,
                block_latency_ms=8.0,
            ),
            NodeState(
                "c",
                "10.0.0.3",
                "cpu",
                32.0,
                24.0,
                max_blocks_capacity=6,
                block_latency_ms=20.0,
            ),
            NodeState(
                "d",
                "10.0.0.4",
                "cpu",
                32.0,
                24.0,
                max_blocks_capacity=8,
                block_latency_ms=24.0,
            ),
        ],
        total_layers=24,
    )

    assert [
        (assignment.node_id, assignment.start_layer, assignment.end_layer)
        for assignment in repaired.assignments
    ] == [
        ("a", 0, 14),
        ("d", 14, 18),
        ("c", 18, 24),
    ]
    _assert_contiguous_cover(repaired, 24)


def test_join_absorbs_from_weakest_cluster_and_partially_from_weaker_neighbor():
    current = PlacementPlan(
        model_id="Qwen/Qwen2.5-0.5B-Instruct",
        coordinator_id="a",
        assignments=[
            ShardAssignment("a", 0, 10, role="first"),
            ShardAssignment("b", 10, 16, role="middle"),
            ShardAssignment("c", 16, 24, role="last"),
        ],
    )

    replanned = plan_join_distribution(
        current=current,
        nodes=[
            NodeState(
                "a",
                "10.0.0.1",
                "cuda",
                64.0,
                48.0,
                max_blocks_capacity=10,
                block_latency_ms=8.0,
            ),
            NodeState(
                "b",
                "10.0.0.2",
                "cpu",
                32.0,
                24.0,
                max_blocks_capacity=6,
                block_latency_ms=28.0,
            ),
            NodeState(
                "c",
                "10.0.0.3",
                "cpu",
                32.0,
                24.0,
                max_blocks_capacity=8,
                block_latency_ms=16.0,
            ),
            NodeState(
                "d",
                "10.0.0.4",
                "cuda",
                64.0,
                48.0,
                max_blocks_capacity=10,
                block_latency_ms=6.0,
            ),
        ],
        total_layers=24,
    )

    assert [
        (assignment.node_id, assignment.start_layer, assignment.end_layer)
        for assignment in replanned.assignments
    ] == [
        ("a", 0, 10),
        ("d", 10, 20),
        ("c", 20, 24),
    ]
    _assert_contiguous_cover(replanned, 24)
