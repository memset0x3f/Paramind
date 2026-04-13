from scheduler.ClusterCoordinator import ClusterCoordinator
from scheduler.ClusterTypes import NodeProfile


def test_reconfiguration_action_round_trip_preserves_action_fields():
    from scheduler.ClusterTypes import NodeReconfigurationAction

    action = NodeReconfigurationAction(
        node_id="node-c",
        action="move_in",
        start_layer=0,
        end_layer=12,
        role="first",
        from_node_id="node-a",
    )

    restored = NodeReconfigurationAction.from_dict(action.to_dict())

    assert restored == action


def test_reconfiguration_plan_groups_actions_by_node():
    from scheduler.ClusterTypes import NodeReconfigurationAction, ReconfigurationPlan

    plan = ReconfigurationPlan(
        model_id="Qwen/Qwen2.5-0.5B-Instruct",
        coordinator_id="node-a",
        actions_by_node={
            "node-a": [NodeReconfigurationAction("node-a", "unload", 0, 12)],
            "node-c": [
                NodeReconfigurationAction(
                    "node-c", "move_in", 0, 12, from_node_id="node-a"
                )
            ],
        },
    )

    assert plan.actions_by_node["node-a"][0].action == "unload"
    assert plan.actions_by_node["node-c"][0].from_node_id == "node-a"


def test_diff_assignment_changes_marks_keep_move_in_and_unload():
    from scheduler.ClusterPlanner import diff_assignment_changes
    from scheduler.ClusterTypes import PlacementPlan, ShardAssignment

    current = PlacementPlan(
        model_id="Qwen/Qwen2.5-0.5B-Instruct",
        coordinator_id="node-a",
        assignments=[
            ShardAssignment("node-a", 0, 12, role="first"),
            ShardAssignment("node-b", 12, 24, role="last"),
        ],
    )
    new = PlacementPlan(
        model_id="Qwen/Qwen2.5-0.5B-Instruct",
        coordinator_id="node-c",
        assignments=[
            ShardAssignment("node-c", 0, 12, role="first", source_node_id="node-a"),
            ShardAssignment("node-b", 12, 24, role="last"),
        ],
    )

    diff = diff_assignment_changes(current, new)

    assert diff["node-b"][0].action == "keep"
    assert diff["node-c"][0].action == "move_in"
    assert diff["node-c"][0].from_node_id == "node-a"
    assert diff["node-a"][0].action == "unload"


def test_diff_assignment_changes_marks_load_when_new_owner_has_no_source():
    from scheduler.ClusterPlanner import diff_assignment_changes
    from scheduler.ClusterTypes import PlacementPlan, ShardAssignment

    current = PlacementPlan(
        model_id="Qwen/Qwen2.5-0.5B-Instruct",
        coordinator_id="node-a",
        assignments=[ShardAssignment("node-a", 0, 24, role="first")],
    )
    new = PlacementPlan(
        model_id="Qwen/Qwen2.5-0.5B-Instruct",
        coordinator_id="node-a",
        assignments=[
            ShardAssignment("node-a", 0, 12, role="first"),
            ShardAssignment("node-c", 12, 24, role="last", source_node_id=None),
        ],
    )

    diff = diff_assignment_changes(current, new)

    assert diff["node-c"][0].action == "load"


def test_coordinator_builds_reconfiguration_plan_after_replan():
    from scheduler.ClusterTypes import ReconfigurationPlan

    coordinator = ClusterCoordinator(
        transport=None,
        model_id="Qwen/Qwen2.5-0.5B-Instruct",
        total_layers=24,
    )
    initial_profiles = [
        NodeProfile("node-a", "10.0.0.1", "cpu", 32, 28, 30, loaded_shards=[(0, 12)]),
        NodeProfile("node-b", "10.0.0.2", "cpu", 32, 27, 29, loaded_shards=[(12, 24)]),
    ]
    coordinator.build_plan(initial_profiles)

    changed_profiles = initial_profiles + [
        NodeProfile("node-c", "10.0.0.3", "cuda", 64, 40, 80, loaded_shards=[]),
    ]

    reconfig = coordinator.build_reconfiguration(changed_profiles)

    assert isinstance(reconfig, ReconfigurationPlan)
    assert reconfig.model_id == coordinator.model_id
    assert reconfig.actions_by_node == {
        "node-a": [reconfig.actions_by_node["node-a"][0]]
    }
    assert reconfig.actions_by_node["node-a"][0].action == "keep"


def test_join_reconfiguration_exposes_move_or_load_actions_for_new_node():
    coordinator = ClusterCoordinator(
        transport=None,
        model_id="Qwen/Qwen2.5-0.5B-Instruct",
        total_layers=24,
    )
    initial_profiles = [
        NodeProfile("node-a", "10.0.0.1", "cpu", 32, 28, 30, loaded_shards=[(0, 12)]),
        NodeProfile("node-b", "10.0.0.2", "cpu", 32, 27, 29, loaded_shards=[(12, 24)]),
    ]
    coordinator.build_plan(initial_profiles)

    expanded_profiles = initial_profiles + [
        NodeProfile("node-c", "10.0.0.3", "cuda", 64, 40, 80, loaded_shards=[]),
    ]

    reconfig = coordinator.build_reconfiguration(expanded_profiles)

    assert reconfig.actions_by_node["node-c"][0].action == "move_in"
    assert reconfig.actions_by_node["node-c"][0].from_node_id == "node-a"


def test_leave_reconfiguration_skips_unload_for_node_without_owned_shard():
    coordinator = ClusterCoordinator(
        transport=None,
        model_id="Qwen/Qwen2.5-0.5B-Instruct",
        total_layers=24,
    )
    initial_profiles = [
        NodeProfile("node-a", "10.0.0.1", "cpu", 32, 28, 30, loaded_shards=[(0, 12)]),
        NodeProfile("node-b", "10.0.0.2", "cpu", 32, 27, 29, loaded_shards=[(12, 24)]),
    ]
    coordinator.build_plan(initial_profiles)

    remaining_profiles = [
        NodeProfile("node-a", "10.0.0.1", "cpu", 32, 28, 30, loaded_shards=[(0, 12)]),
    ]

    reconfig = coordinator.build_reconfiguration(remaining_profiles)

    assert "node-b" not in reconfig.actions_by_node


def test_diff_assignment_changes_is_exported():
    from inference import diff_assignment_changes

    assert callable(diff_assignment_changes)


def test_compute_next_plan_without_current_plan_uses_cold_start(monkeypatch):
    import scheduler.ClusterPlanner as planner
    from scheduler.ClusterTypes import PlacementPlan, ShardAssignment

    sentinel = PlacementPlan(
        model_id="Qwen/Qwen2.5-0.5B-Instruct",
        assignments=[ShardAssignment("node-a", 0, 24, role="first")],
        coordinator_id="node-a",
    )

    called = {"cold_start": False}

    def fake_cold_start(model_id, total_layers, nodes, layer_work=None):
        called["cold_start"] = True
        return sentinel

    monkeypatch.setattr(planner, "plan_static_distribution", fake_cold_start)

    result = planner.compute_next_plan(
        current=None,
        nodes=[NodeProfile("node-a", "10.0.0.1", "cpu", 32, 28, 30)],
        model_id="Qwen/Qwen2.5-0.5B-Instruct",
        total_layers=24,
    )

    assert called["cold_start"] is True
    assert result is sentinel


def test_compute_next_plan_uses_join_strategy_when_node_set_grows(monkeypatch):
    import scheduler.ClusterPlanner as planner
    from scheduler.ClusterTypes import PlacementPlan, ShardAssignment

    current = PlacementPlan(
        model_id="Qwen/Qwen2.5-0.5B-Instruct",
        assignments=[
            ShardAssignment("node-a", 0, 12, role="first"),
            ShardAssignment("node-b", 12, 24, role="last"),
        ],
        coordinator_id="node-a",
    )
    sentinel = PlacementPlan(
        model_id=current.model_id,
        assignments=current.assignments,
        coordinator_id="node-c",
    )

    called = {"join": False}

    def fake_join(current, nodes, total_layers, layer_work=None):
        called["join"] = True
        return sentinel

    monkeypatch.setattr(planner, "plan_join_distribution", fake_join)

    result = planner.compute_next_plan(
        current=current,
        nodes=[
            NodeProfile("node-a", "10.0.0.1", "cpu", 32, 28, 30),
            NodeProfile("node-b", "10.0.0.2", "cpu", 32, 27, 29),
            NodeProfile("node-c", "10.0.0.3", "cuda", 64, 40, 80),
        ],
        model_id=current.model_id,
        total_layers=24,
    )

    assert called["join"] is True
    assert result is sentinel


def test_compute_next_plan_uses_drop_strategy_when_node_set_shrinks(monkeypatch):
    import scheduler.ClusterPlanner as planner
    from scheduler.ClusterTypes import PlacementPlan, ShardAssignment

    current = PlacementPlan(
        model_id="Qwen/Qwen2.5-0.5B-Instruct",
        assignments=[
            ShardAssignment("node-a", 0, 12, role="first"),
            ShardAssignment("node-b", 12, 24, role="last"),
        ],
        coordinator_id="node-a",
    )
    sentinel = PlacementPlan(
        model_id=current.model_id,
        assignments=[ShardAssignment("node-a", 0, 24, role="first")],
        coordinator_id="node-a",
    )

    called = {"drop": False}

    def fake_drop(current, nodes, total_layers, layer_work=None):
        called["drop"] = True
        return sentinel

    monkeypatch.setattr(planner, "plan_drop_distribution", fake_drop)

    result = planner.compute_next_plan(
        current=current,
        nodes=[NodeProfile("node-a", "10.0.0.1", "cpu", 32, 28, 30)],
        model_id=current.model_id,
        total_layers=24,
    )

    assert called["drop"] is True
    assert result is sentinel


def test_compute_next_plan_uses_replan_strategy_when_node_set_is_unchanged(
    monkeypatch,
):
    import scheduler.ClusterPlanner as planner
    from scheduler.ClusterTypes import PlacementPlan, ShardAssignment

    current = PlacementPlan(
        model_id="Qwen/Qwen2.5-0.5B-Instruct",
        assignments=[
            ShardAssignment("node-a", 0, 12, role="first"),
            ShardAssignment("node-b", 12, 24, role="last"),
        ],
        coordinator_id="node-a",
    )
    sentinel = PlacementPlan(
        model_id=current.model_id,
        assignments=current.assignments,
        coordinator_id="node-b",
    )

    called = {"replan": False}

    def fake_replan(current, nodes, total_layers, layer_work=None):
        called["replan"] = True
        return sentinel

    monkeypatch.setattr(planner, "plan_membership_stable_distribution", fake_replan)

    result = planner.compute_next_plan(
        current=current,
        nodes=[
            NodeProfile("node-a", "10.0.0.1", "cpu", 32, 28, 30),
            NodeProfile("node-b", "10.0.0.2", "cpu", 32, 27, 29),
        ],
        model_id=current.model_id,
        total_layers=24,
    )

    assert called["replan"] is True
    assert result is sentinel


def _assert_contiguous_cover(plan, total_layers):
    pairs = [
        (assignment.start_layer, assignment.end_layer)
        for assignment in plan.assignments
    ]
    assert pairs[0][0] == 0
    assert pairs[-1][1] == total_layers
    for left, right in zip(pairs, pairs[1:]):
        assert left[1] == right[0]


def test_coordinator_can_replan_from_current_plan_when_profiles_change():
    coordinator = ClusterCoordinator(
        transport=None,
        model_id="Qwen/Qwen2.5-0.5B-Instruct",
        total_layers=24,
    )
    initial_profiles = [
        NodeProfile("node-a", "10.0.0.1", "cpu", 32, 28, 30, loaded_shards=[(0, 12)]),
        NodeProfile("node-b", "10.0.0.2", "cpu", 32, 27, 29, loaded_shards=[(12, 24)]),
    ]
    old_plan = coordinator.build_plan(initial_profiles)

    changed_profiles = [
        NodeProfile("node-a", "10.0.0.1", "cpu", 32, 4, 10, loaded_shards=[]),
        NodeProfile("node-b", "10.0.0.2", "cpu", 32, 27, 30, loaded_shards=[(12, 24)]),
    ]

    new_plan = coordinator.replan(changed_profiles)

    _assert_contiguous_cover(new_plan, 24)
    assert new_plan is not old_plan
    assert new_plan.assignments[0].node_id == "node-b"
    assert any(
        assignment.source_node_id == "node-a" for assignment in new_plan.assignments
    )


def test_coordinator_replan_keeps_current_plan_when_shards_still_fit():
    coordinator = ClusterCoordinator(
        transport=None,
        model_id="Qwen/Qwen2.5-0.5B-Instruct",
        total_layers=24,
    )
    profiles = [
        NodeProfile("node-a", "10.0.0.1", "cpu", 32, 28, 30, loaded_shards=[(0, 12)]),
        NodeProfile("node-b", "10.0.0.2", "cpu", 32, 27, 29, loaded_shards=[(12, 24)]),
    ]

    initial = coordinator.build_plan(profiles)
    replanned = coordinator.replan(profiles)

    assert replanned is initial


def test_coordinator_replan_handles_node_leave_by_falling_back_to_static():
    coordinator = ClusterCoordinator(
        transport=None,
        model_id="Qwen/Qwen2.5-0.5B-Instruct",
        total_layers=24,
    )
    initial_profiles = [
        NodeProfile("node-a", "10.0.0.1", "cpu", 32, 28, 30, loaded_shards=[(0, 12)]),
        NodeProfile("node-b", "10.0.0.2", "cpu", 32, 27, 29, loaded_shards=[(12, 24)]),
    ]
    coordinator.build_plan(initial_profiles)

    remaining = [
        NodeProfile("node-a", "10.0.0.1", "cpu", 32, 28, 30, loaded_shards=[(0, 12)]),
    ]

    new_plan = coordinator.replan(remaining)

    assert [(a.node_id, a.start_layer, a.end_layer) for a in new_plan.assignments] == [
        ("node-a", 0, 24),
    ]


def test_coordinator_replan_can_shift_full_range_to_stronger_joining_node():
    coordinator = ClusterCoordinator(
        transport=None,
        model_id="Qwen/Qwen2.5-0.5B-Instruct",
        total_layers=24,
    )
    initial_profiles = [
        NodeProfile("node-a", "10.0.0.1", "cpu", 32, 28, 30, loaded_shards=[(0, 12)]),
        NodeProfile("node-b", "10.0.0.2", "cpu", 32, 27, 29, loaded_shards=[(12, 24)]),
    ]
    current = coordinator.build_plan(initial_profiles)

    expanded_profiles = initial_profiles + [
        NodeProfile("node-c", "10.0.0.3", "cuda", 64, 40, 80, loaded_shards=[]),
    ]

    new_plan = coordinator.replan(expanded_profiles)

    assert new_plan is not current
    assert [
        (assignment.node_id, assignment.start_layer, assignment.end_layer)
        for assignment in new_plan.assignments
    ] == [("node-c", 0, 24)]
