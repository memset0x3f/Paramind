from inference.ClusterTypes import NodeProfile, ShardAssignment, PlacementPlan
from inference.ClusterPlanner import choose_coordinator, plan_static_distribution


def test_placement_plan_preserves_layer_order():
    plan = PlacementPlan(
        model_id="Qwen/Qwen2.5-0.5B-Instruct",
        assignments=[
            ShardAssignment(node_id="node-a", start_layer=0, end_layer=12),
            ShardAssignment(node_id="node-b", start_layer=12, end_layer=24),
        ],
    )

    assert [a.start_layer for a in plan.assignments] == [0, 12]
    assert [a.end_layer for a in plan.assignments] == [12, 24]


def test_static_distribution_assigns_contiguous_ranges():
    nodes = [
        NodeProfile("node-a", "10.0.0.1", "cpu", 32, 28, 28),
        NodeProfile("node-b", "10.0.0.2", "cpu", 32, 20, 20),
        NodeProfile("node-c", "10.0.0.3", "cpu", 32, 18, 18),
    ]

    plan = plan_static_distribution(
        model_id="Qwen/Qwen2.5-0.5B-Instruct",
        total_layers=24,
        nodes=nodes,
    )

    assert [(a.start_layer, a.end_layer) for a in plan.assignments][0][0] == 0
    assert [(a.start_layer, a.end_layer) for a in plan.assignments][-1][1] == 24
    for left, right in zip(plan.assignments, plan.assignments[1:]):
        assert left.end_layer == right.start_layer
    sizes = {assignment.node_id: assignment.num_layers for assignment in plan.assignments}
    assert sizes["node-a"] >= sizes["node-c"]
    assert plan.coordinator_id == "node-a"
    assert choose_coordinator(nodes) == "node-a"
