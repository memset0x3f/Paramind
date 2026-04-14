from scheduler.ClusterPlanner import range_bytes, plan_static_distribution
from scheduler.NodeInventory import NodeState


def test_range_bytes_uses_model_profile_for_qwen_0_5b():
    model_id = "Qwen/Qwen2.5-0.5B-Instruct"
    first = range_bytes(model_id, 0, 1, total_layers=24)
    middle = range_bytes(model_id, 1, 2, total_layers=24)
    last = range_bytes(model_id, 23, 24, total_layers=24)

    assert first > middle
    assert last >= middle


def test_static_distribution_uses_byte_budget_not_uniform_layer_count():
    model_id = "Qwen/Qwen2.5-0.5B-Instruct"
    node_a_budget_bytes = range_bytes(model_id, 0, 10, total_layers=24)
    node_b_budget_bytes = range_bytes(model_id, 10, 24, total_layers=24)
    nodes = [
        NodeState(
            "node-a",
            "10.0.0.1",
            "cpu",
            32.0,
            32.0,
            max_usable_bytes=node_a_budget_bytes,
            block_throughput=50.0,
        ),
        NodeState(
            "node-b",
            "10.0.0.2",
            "cpu",
            32.0,
            32.0,
            max_usable_bytes=node_b_budget_bytes,
            block_throughput=20.0,
        ),
    ]

    plan = plan_static_distribution(model_id=model_id, total_layers=24, nodes=nodes)

    assert [(a.node_id, a.start_layer, a.end_layer) for a in plan.assignments] == [
        ("node-a", 0, 10),
        ("node-b", 10, 24),
    ]
