from inference.ClusterPlanner import (
    estimate_assignment_cost,
    replan_distribution,
    score_assignment_move_cost,
)
from inference.ClusterTypes import NodeProfile, PlacementPlan, ShardAssignment


def _assert_contiguous_cover(plan, total_layers):
    pairs = [(assignment.start_layer, assignment.end_layer) for assignment in plan.assignments]
    assert pairs[0][0] == 0
    assert pairs[-1][1] == total_layers
    for left, right in zip(pairs, pairs[1:]):
        assert left[1] == right[0]


def test_move_cost_prefers_existing_shard_owner():
    cost_same = score_assignment_move_cost(
        current_owner="node-a",
        target_owner="node-a",
        shard=(0, 12),
    )
    cost_move = score_assignment_move_cost(
        current_owner="node-a",
        target_owner="node-b",
        shard=(0, 12),
    )

    assert cost_same < cost_move


def test_move_cost_penalizes_wider_shards_more_than_narrower_shards():
    narrow_cost = score_assignment_move_cost(
        current_owner="node-a",
        target_owner="node-b",
        shard=(0, 8),
    )
    wide_cost = score_assignment_move_cost(
        current_owner="node-a",
        target_owner="node-b",
        shard=(0, 16),
    )

    assert wide_cost > narrow_cost


def test_estimate_assignment_cost_prefers_loaded_cuda_node_over_cold_cpu_node():
    hot_node = NodeProfile(
        "node-a",
        "10.0.0.1",
        "cuda",
        64.0,
        40.0,
        200.0,
        loaded_shards=[(0, 12)],
    )
    cold_node = NodeProfile(
        "node-b",
        "10.0.0.2",
        "cpu",
        64.0,
        40.0,
        40.0,
        loaded_shards=[],
    )
    hot_assignment = ShardAssignment("node-a", 0, 12, role="first")
    cold_assignment = ShardAssignment("node-b", 0, 12, role="first")

    hot_cost = estimate_assignment_cost(
        node=hot_node,
        assignment=hot_assignment,
        shard_param_gb=8.0,
    )
    cold_cost = estimate_assignment_cost(
        node=cold_node,
        assignment=cold_assignment,
        shard_param_gb=8.0,
    )

    assert hot_cost < cold_cost


def test_replanner_prefers_lower_cost_owner_instead_of_static_sort_order():
    current = PlacementPlan(
        model_id="Qwen/Qwen2.5-0.5B-Instruct",
        coordinator_id="node-a",
        assignments=[
            ShardAssignment("node-a", 0, 12, role="first"),
            ShardAssignment("node-b", 12, 24, role="last"),
        ],
    )
    nodes = [
        NodeProfile("node-a", "10.0.0.1", "cpu", 32.0, 4.0, 10.0, loaded_shards=[]),
        NodeProfile(
            "node-b",
            "10.0.0.2",
            "cuda",
            64.0,
            40.0,
            200.0,
            loaded_shards=[(12, 24)],
        ),
        NodeProfile(
            "node-c",
            "10.0.0.3",
            "cpu",
            32.0,
            30.0,
            20.0,
            loaded_shards=[(0, 12)],
        ),
    ]

    new_plan = replan_distribution(current=current, nodes=nodes, total_layers=24)

    _assert_contiguous_cover(new_plan, 24)
    assert any(assignment.node_id == "node-b" for assignment in new_plan.assignments)
    assert any(assignment.source_node_id == "node-a" for assignment in new_plan.assignments)
    assert all(assignment.node_id != "node-a" or assignment.end_layer - assignment.start_layer < 12 for assignment in new_plan.assignments)


def test_replanner_rejects_lower_cost_node_once_capacity_is_used():
    current = PlacementPlan(
        model_id="Qwen/Qwen2.5-0.5B-Instruct",
        coordinator_id="node-a",
        assignments=[
            ShardAssignment("node-a", 0, 12, role="first"),
            ShardAssignment("node-c", 12, 24, role="last"),
        ],
    )
    nodes = [
        NodeProfile("node-a", "10.0.0.1", "cpu", 32.0, 4.0, 10.0, loaded_shards=[]),
        NodeProfile(
            "node-b",
            "10.0.0.2",
            "cuda",
            64.0,
            18.0,
            200.0,
            loaded_shards=[(0, 12)],
        ),
        NodeProfile(
            "node-c",
            "10.0.0.3",
            "cpu",
            32.0,
            30.0,
            20.0,
            loaded_shards=[],
        ),
    ]

    new_plan = replan_distribution(current=current, nodes=nodes, total_layers=24)

    _assert_contiguous_cover(new_plan, 24)
    assigned_by_node = {}
    for assignment in new_plan.assignments:
        assigned_by_node[assignment.node_id] = assigned_by_node.get(assignment.node_id, 0) + assignment.num_layers
    assert assigned_by_node["node-b"] <= 18
    assert any(assignment.node_id == "node-c" for assignment in new_plan.assignments)


def test_replanner_keeps_existing_assignments_when_capacity_still_fits():
    current = PlacementPlan(
        model_id="Qwen/Qwen2.5-0.5B-Instruct",
        coordinator_id="node-a",
        assignments=[
            ShardAssignment("node-a", 0, 12, role="first"),
            ShardAssignment("node-b", 12, 24, role="last"),
        ],
    )
    nodes = [
        NodeProfile(
            "node-a",
            "10.0.0.1",
            "cpu",
            32.0,
            28.0,
            30.0,
            loaded_shards=[(0, 12)],
        ),
        NodeProfile(
            "node-b",
            "10.0.0.2",
            "cpu",
            32.0,
            27.0,
            29.0,
            loaded_shards=[(12, 24)],
        ),
    ]

    new_plan = replan_distribution(current=current, nodes=nodes, total_layers=24)

    assert [
        (assignment.node_id, assignment.start_layer, assignment.end_layer)
        for assignment in new_plan.assignments
    ] == [("node-a", 0, 12), ("node-b", 12, 24)]


def test_replanner_uses_cost_helper_when_capacity_breaks():
    current = PlacementPlan(
        model_id="Qwen/Qwen2.5-0.5B-Instruct",
        coordinator_id="node-a",
        assignments=[
            ShardAssignment("node-a", 0, 12, role="first"),
            ShardAssignment("node-b", 12, 24, role="last"),
        ],
    )
    nodes = [
        NodeProfile(
            "node-a",
            "10.0.0.1",
            "cpu",
            32.0,
            4.0,
            10.0,
            loaded_shards=[],
        ),
        NodeProfile(
            "node-b",
            "10.0.0.2",
            "cpu",
            32.0,
            27.0,
            30.0,
            loaded_shards=[(12, 24)],
        ),
    ]

    new_plan = replan_distribution(current=current, nodes=nodes, total_layers=24)

    _assert_contiguous_cover(new_plan, 24)
    assert new_plan is not current
    assert any(assignment.node_id == "node-b" and assignment.start_layer == 0 for assignment in new_plan.assignments)
    assert any(assignment.source_node_id == "node-a" for assignment in new_plan.assignments)
