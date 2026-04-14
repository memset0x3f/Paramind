from scheduler.ClusterTypes import NodeProfile, ShardAssignment, PlacementPlan
from scheduler.ClusterPlanner import plan_static_distribution, range_bytes


MODEL_ID = "Qwen/Qwen2.5-0.5B-Instruct"
TOTAL_LAYERS = 24


def _budget(start: int, end: int) -> int:
    return range_bytes(MODEL_ID, start, end, TOTAL_LAYERS)


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
        NodeProfile(
            "node-a", "10.0.0.1", "cpu", 32, 28, max_usable_bytes=_budget(0, 24)
        ),
        NodeProfile(
            "node-b", "10.0.0.2", "cpu", 32, 20, max_usable_bytes=_budget(10, 18)
        ),
        NodeProfile(
            "node-c", "10.0.0.3", "cpu", 32, 18, max_usable_bytes=_budget(18, 24)
        ),
    ]

    plan = plan_static_distribution(
        model_id=MODEL_ID,
        total_layers=TOTAL_LAYERS,
        nodes=nodes,
    )

    assert [(a.start_layer, a.end_layer) for a in plan.assignments][0][0] == 0
    assert [(a.start_layer, a.end_layer) for a in plan.assignments][-1][1] == 24
    for left, right in zip(plan.assignments, plan.assignments[1:]):
        assert left.end_layer == right.start_layer
    sizes = {
        assignment.node_id: assignment.num_layers for assignment in plan.assignments
    }
    # Best-quality node is filled first; 24 layers fit entirely on node-a.
    assert sizes == {"node-a": 24}
    assert plan.coordinator_id is None
