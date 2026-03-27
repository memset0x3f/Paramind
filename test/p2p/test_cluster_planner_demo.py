from scripts.cluster_planner_demo import ClusterDemoSession


def _assignment_pairs(plan):
    return [(assignment.start_layer, assignment.end_layer) for assignment in plan.assignments]


def test_initial_render_has_empty_node_states_before_start():
    session = ClusterDemoSession()

    rendered = session.render_current(include_guide=True)

    assert "=== PARAMETER GUIDE ===" in rendered
    assert "[NODE STATES]" in rendered
    assert "(no cluster yet)" in rendered
    assert "node-a | active=" not in rendered
    assert "[PLACEMENT]" not in rendered


def test_start_builds_initial_plan_for_selected_nodes():
    session = ClusterDemoSession()

    rendered = session.start(["node-a", "node-b", "node-c"])

    assert session.current_plan is not None
    assert session.active_node_ids == {"node-a", "node-b", "node-c"}
    assert _assignment_pairs(session.current_plan)[0][0] == 0
    assert _assignment_pairs(session.current_plan)[-1][1] == 24
    assert session.node_profiles["node-a"].loaded_shards == [(0, 12)]
    assert session.node_profiles["node-b"].loaded_shards == [(16, 24)]
    assert session.node_profiles["node-c"].loaded_shards == [(12, 16)]
    assert "[PLACEMENT]" in rendered


def test_join_adds_node_and_updates_loaded_state_from_new_plan():
    session = ClusterDemoSession()
    session.start(["node-a", "node-b", "node-c"])

    rendered = session.join("node-d")

    assert "node-d | active=yes" in rendered
    assert session.current_plan is not None
    assert any(assignment.node_id == "node-d" for assignment in session.current_plan.assignments)
    node_d_ranges = [
        (assignment.start_layer, assignment.end_layer)
        for assignment in session.current_plan.assignments
        if assignment.node_id == "node-d"
    ]
    assert session.node_profiles["node-d"].loaded_shards == node_d_ranges


def test_drop_preserves_loaded_history_for_remaining_nodes():
    session = ClusterDemoSession()
    session.start(["node-a", "node-b", "node-c"])
    session.join("node-d")

    rendered = session.drop("node-b")

    assert "node-b | active=no" in rendered
    assert "node-d | active=yes" in rendered
    assert session.node_profiles["node-b"].loaded_shards == []
    assert session.current_plan is not None
    assert session.active_node_ids == {"node-a", "node-c", "node-d"}
    node_d_ranges = [
        (assignment.start_layer, assignment.end_layer)
        for assignment in session.current_plan.assignments
        if assignment.node_id == "node-d"
    ]
    assert session.node_profiles["node-d"].loaded_shards == node_d_ranges


def test_start_resets_session_state_instead_of_accumulating_ranges():
    session = ClusterDemoSession()
    session.start(["node-a", "node-b"])
    session.join("node-d")

    session.start(["node-a"])

    assert session.active_node_ids == {"node-a"}
    assert session.current_plan is not None
    assert _assignment_pairs(session.current_plan) == [(0, 24)]
    assert session.node_profiles["node-a"].loaded_shards == [(0, 24)]
    assert session.node_profiles["node-b"].loaded_shards == []
    assert session.node_profiles["node-d"].loaded_shards == []


def test_process_command_supports_show_join_drop_and_quit():
    session = ClusterDemoSession()

    output, keep_running = session.process_command("start node-a,node-b,node-c")
    assert keep_running is True
    assert "[PLACEMENT]" in output

    output, keep_running = session.process_command("join node-d")
    assert keep_running is True
    assert "node-d | active=yes" in output

    output, keep_running = session.process_command("drop node-b")
    assert keep_running is True
    assert "node-b | active=no" in output

    output, keep_running = session.process_command("quit")
    assert keep_running is False
    assert "bye" in output.lower()


def test_process_command_reports_invalid_input_without_crashing():
    session = ClusterDemoSession()

    output, keep_running = session.process_command("join node-d")
    assert keep_running is True
    assert "error:" in output.lower()

    output, keep_running = session.process_command("start node-a,node-z")
    assert keep_running is True
    assert "unknown nodes" in output.lower()


def test_scoring_output_uses_stage_time_metrics_instead_of_quality_maximization():
    session = ClusterDemoSession()
    rendered = session.start(["node-a", "node-b", "node-c"])

    assert "reference_stage_width=" in rendered
    assert "stage_time=" in rendered
    assert "coordinator_score=" in rendered
    assert "quality_score" not in rendered
    assert "coordinator = min(coordinator_score)" in rendered


def test_cluster_demo_uses_current_cluster_actions_and_event_log_sections():
    session = ClusterDemoSession()

    rendered = session.start(["node-a", "node-b", "node-c"])

    assert "=== CURRENT CLUSTER ===" in rendered
    assert "[ACTIONS]" in rendered
    assert "[EVENT LOG]" in rendered
    assert "[SHARD STATES]" in rendered
    assert "[SCORING]" in rendered


def test_rendered_coordinator_is_consistent_across_cluster_scoring_and_placement():
    session = ClusterDemoSession()
    session.start(["node-a", "node-b", "node-c"])

    rendered = session.join("node-d")

    assert session.current_plan is not None
    expected = session.current_plan.coordinator_id
    assert f"coordinator  : {expected}" in rendered
    assert f"coordinator = min(coordinator_score) = {expected}" in rendered
    assert f"coordinator={expected}" in rendered
