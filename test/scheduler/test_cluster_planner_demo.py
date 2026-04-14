from scripts.cluster_planner_demo import ClusterDemoSession


def _assignment_pairs(plan):
    return [
        (assignment.start_layer, assignment.end_layer)
        for assignment in plan.assignments
    ]


def test_initial_render_shows_model_and_idle_cluster_table():
    session = ClusterDemoSession()

    rendered = session.render_current(include_guide=True)

    assert "=== HELP ===" in rendered
    assert "model: demo/TenLayerToy" in rendered
    assert (
        "head:" in rendered and "middle (1 layer):" in rendered and "tail:" in rendered
    )
    assert "[CLUSTER]" in rendered
    assert "last: initialized" in rendered
    assert "a    | n " in rendered
    assert "weights:" not in rendered.lower()
    assert rendered.count("| n  |") == 4


def test_start_builds_initial_plan_for_selected_nodes():
    session = ClusterDemoSession()

    rendered = session.start(["a", "b", "c"])

    assert session.current_plan is not None
    assert session.active_node_ids == {"a", "b", "c"}
    assert _assignment_pairs(session.current_plan)[0][0] == 0
    assert _assignment_pairs(session.current_plan)[-1][1] == 10
    assert [
        (assignment.node_id, assignment.start_layer, assignment.end_layer)
        for assignment in session.current_plan.assignments
    ] == [
        ("a", 0, 3),
        ("b", 3, 7),
        ("c", 7, 10),
    ]
    assert session.node_profiles["a"].loaded_shards == [(0, 3)]
    assert session.node_profiles["b"].loaded_shards == [(3, 7)]
    assert session.node_profiles["c"].loaded_shards == [(7, 10)]
    assert "[0, 3)" in rendered and "[3, 7)" in rendered and "[7, 10)" in rendered
    assert "right-open" in rendered


def test_join_allows_stronger_new_node_to_take_over_from_current_weakest_owner():
    session = ClusterDemoSession()
    session.start(["a", "b", "c"])

    rendered = session.join("d")

    assert "d    | y " in rendered
    assert session.current_plan is not None
    assert [
        (assignment.node_id, assignment.start_layer, assignment.end_layer)
        for assignment in session.current_plan.assignments
    ] == [
        ("a", 0, 3),
        ("b", 3, 7),
        ("d", 7, 9),
        ("c", 9, 10),
    ]
    assert session.node_profiles["a"].loaded_shards == [(0, 3)]
    assert session.node_profiles["b"].loaded_shards == [(3, 7)]
    assert session.node_profiles["c"].loaded_shards == [(9, 10)]
    assert session.node_profiles["d"].loaded_shards == [(7, 9)]


def test_drop_repair_absorbs_gap_without_global_reshuffle():
    session = ClusterDemoSession()
    session.start(["a", "b", "c"])
    session.join("d")

    session.drop("d")

    assert session.node_profiles["d"].loaded_shards == []
    assert session.current_plan is not None
    assert session.active_node_ids == {"a", "b", "c"}
    assert [
        (assignment.node_id, assignment.start_layer, assignment.end_layer)
        for assignment in session.current_plan.assignments
    ] == [
        ("a", 0, 3),
        ("b", 3, 7),
        ("c", 7, 10),
    ]
    assert [
        (a.node_id, a.source_node_id) for a in session.current_plan.assignments
    ] == [
        ("a", None),
        ("b", None),
        ("c", "d"),
    ]
    assert session.node_profiles["b"].loaded_shards == [(3, 7)]
    assert session.node_profiles["c"].loaded_shards == [(7, 10)]


def test_start_resets_session_state_instead_of_accumulating_ranges():
    session = ClusterDemoSession()
    session.start(["a", "b", "c"])
    session.join("d")

    session.start(["a", "b", "c"])

    assert session.active_node_ids == {"a", "b", "c"}
    assert session.current_plan is not None
    assert _assignment_pairs(session.current_plan) == [(0, 3), (3, 7), (7, 10)]
    assert session.node_profiles["a"].loaded_shards == [(0, 3)]
    assert session.node_profiles["b"].loaded_shards == [(3, 7)]
    assert session.node_profiles["c"].loaded_shards == [(7, 10)]
    assert session.node_profiles["d"].loaded_shards == []


def test_process_command_supports_show_join_drop_and_quit():
    session = ClusterDemoSession()

    output, keep_running = session.process_command("start a,b,c")
    assert keep_running is True
    assert "[CLUSTER]" in output

    output, keep_running = session.process_command("join d")
    assert keep_running is True
    assert "[7, 9)" in output

    output, keep_running = session.process_command("drop d")
    assert keep_running is True
    assert "[7, 10)" in output

    output, keep_running = session.process_command("quit")
    assert keep_running is False
    assert "bye" in output.lower()


def test_process_command_reports_invalid_input_without_crashing():
    session = ClusterDemoSession()

    output, keep_running = session.process_command("join d")
    assert keep_running is True
    assert "error:" in output.lower()

    output, keep_running = session.process_command("start a,z")
    assert keep_running is True
    assert "unknown nodes" in output.lower()


def test_start_with_single_node_keeps_session_pending_instead_of_crashing():
    session = ClusterDemoSession()

    rendered = session.start(["a"])

    assert session.current_plan is None
    assert session.active_node_ids == {"a"}
    assert session.planning_error is not None
    assert "plan_status: pending" in rendered


def test_join_recovers_from_pending_state_once_capacity_is_enough():
    session = ClusterDemoSession()
    session.start(["a"])

    rendered = session.join("b")
    assert session.current_plan is None
    assert session.planning_error is not None
    assert "plan_status: pending" in rendered

    rendered = session.join("c")

    assert session.current_plan is not None
    assert session.active_node_ids == {"a", "b", "c"}
    assert session.planning_error is None
    assert "plan_status: pending" not in rendered


def test_drop_that_makes_cluster_infeasible_reports_error_without_mutating_state():
    session = ClusterDemoSession()
    session.start(["a", "b", "c"])

    before_plan = [
        (assignment.node_id, assignment.start_layer, assignment.end_layer)
        for assignment in session.current_plan.assignments
    ]
    before_active = set(session.active_node_ids)
    before_loaded = {
        node_id: list(profile.loaded_shards)
        for node_id, profile in session.node_profiles.items()
    }

    output, keep_running = session.process_command("drop c")

    assert keep_running is True
    assert "insufficient cluster capacity" in output.lower()
    assert session.current_plan is not None
    assert [
        (assignment.node_id, assignment.start_layer, assignment.end_layer)
        for assignment in session.current_plan.assignments
    ] == before_plan
    assert session.active_node_ids == before_active
    assert {
        node_id: list(profile.loaded_shards)
        for node_id, profile in session.node_profiles.items()
    } == before_loaded


def test_compact_output_has_no_legacy_sections():
    session = ClusterDemoSession()
    rendered = session.start(["a", "b", "c"])

    assert "cap_mb" in rendered
    assert "reference_stage_width=" not in rendered
    assert "stage_time=" not in rendered
    assert "coordinator_score=" not in rendered
    assert "[EVENT LOG]" not in rendered
    assert "[SCORING]" not in rendered
    assert "[PLACEMENT]" not in rendered
    assert "role" not in rendered.split("[CLUSTER]")[1]


def test_render_includes_model_header_and_cluster_table():
    session = ClusterDemoSession()

    rendered = session.start(["a", "b", "c"])

    assert "head:" in rendered and "middle (1 layer):" in rendered
    assert "[CLUSTER]" in rendered
    assert "last: start" in rendered


def test_plan_has_no_coordinator_in_session():
    session = ClusterDemoSession()
    session.start(["a", "b", "c"])

    session.join("d")

    assert session.current_plan is not None
    assert session.current_plan.coordinator_id is None
