from scheduler.ClusterPlanner import (
    plan_join_distribution,
    plan_drop_distribution,
    plan_static_distribution,
    range_bytes,
    replan_distribution,
)
from scheduler.ClusterTypes import PlacementPlan, ShardAssignment
from scheduler.NodeInventory import NodeState


MODEL_ID = "Qwen/Qwen2.5-0.5B-Instruct"
TOTAL_LAYERS = 24


def _budget(start: int, end: int) -> int:
    return range_bytes(MODEL_ID, start, end, TOTAL_LAYERS)


def _state(
    node_id: str,
    speed: float,
    budget_start: int,
    budget_end: int,
    device_type: str = "cpu",
) -> NodeState:
    return NodeState(
        node_id,
        f"{node_id}.local",
        device_type,
        64.0 if device_type == "cuda" else 32.0,
        48.0 if device_type == "cuda" else 24.0,
        max_usable_bytes=_budget(budget_start, budget_end),
        block_throughput=speed,
    )


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
        _state("fast", speed=125.0, budget_start=0, budget_end=24, device_type="cuda"),
        _state("slow", speed=31.25, budget_start=0, budget_end=24),
    ]

    plan = plan_static_distribution(
        model_id=MODEL_ID,
        total_layers=TOTAL_LAYERS,
        nodes=nodes,
    )

    sizes = {
        assignment.node_id: assignment.num_layers for assignment in plan.assignments
    }
    assert sizes == {"fast": 24}
    _assert_contiguous_cover(plan, 24)


def test_cold_start_greedy_skips_weak_middle_when_strong_nodes_cover_model():
    nodes = [
        _state(
            "strong-a", speed=125.0, budget_start=0, budget_end=12, device_type="cuda"
        ),
        _state("weak", speed=25.0, budget_start=6, budget_end=18),
        _state(
            "strong-b", speed=111.0, budget_start=12, budget_end=24, device_type="cuda"
        ),
    ]

    plan = plan_static_distribution(
        model_id=MODEL_ID,
        total_layers=TOTAL_LAYERS,
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
        _state("node-a", speed=50.0, budget_start=1, budget_end=5),
        _state("node-b", speed=50.0, budget_start=5, budget_end=9),
    ]

    try:
        plan_static_distribution(
            model_id=MODEL_ID,
            total_layers=TOTAL_LAYERS,
            nodes=nodes,
        )
    except ValueError as exc:
        assert "capacity" in str(exc).lower()
    else:
        raise AssertionError("expected ValueError for impossible capacity")


def test_dp_cold_start_single_node_gets_full_range():
    nodes = [
        _state("solo", speed=100.0, budget_start=0, budget_end=24, device_type="cuda"),
    ]

    plan = plan_static_distribution(
        model_id=MODEL_ID,
        total_layers=TOTAL_LAYERS,
        nodes=nodes,
    )

    assert [
        (assignment.node_id, assignment.start_layer, assignment.end_layer)
        for assignment in plan.assignments
    ] == [("solo", 0, 24)]


def test_replanner_keeps_boundaries_when_departing_node_was_not_in_current_plan():
    current = plan_static_distribution(
        model_id=MODEL_ID,
        total_layers=TOTAL_LAYERS,
        nodes=[
            _state("a", speed=125.0, budget_start=0, budget_end=12, device_type="cuda"),
            _state("b", speed=62.5, budget_start=12, budget_end=24),
            _state(
                "c", speed=100.0, budget_start=12, budget_end=24, device_type="cuda"
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
                max_usable_bytes=_budget(0, 16),
                block_throughput=125.0,
                loaded_ranges=[(0, 8)],
            ),
            NodeState(
                "c",
                "10.0.0.3",
                "cuda",
                64.0,
                48.0,
                max_usable_bytes=_budget(8, 24),
                block_throughput=100.0,
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
            _state("a", speed=125.0, budget_start=0, budget_end=12, device_type="cuda"),
            _state("c", speed=50.0, budget_start=12, budget_end=24),
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
            _state("a", speed=125.0, budget_start=0, budget_end=14, device_type="cuda"),
            _state("c", speed=50.0, budget_start=18, budget_end=24),
            _state("d", speed=41.7, budget_start=14, budget_end=22),
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
            _state("a", speed=125.0, budget_start=0, budget_end=10, device_type="cuda"),
            _state("b", speed=35.7, budget_start=10, budget_end=16),
            _state("c", speed=62.5, budget_start=16, budget_end=24),
            _state(
                "d", speed=166.7, budget_start=10, budget_end=20, device_type="cuda"
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
