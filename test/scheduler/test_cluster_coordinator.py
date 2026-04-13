import pytest

from scheduler.ClusterCoordinator import ClusterCoordinator
from scheduler.ClusterTypes import (
    NodeProfile,
    NodeReconfigurationAction,
    ReconfigurationPlan,
    ShardAssignment,
)
from inference.NodeRuntime import NodeRuntime
from inference.ShardConfig import ModelFamily
from inference.ShardRegistry import ShardRecord


class FakeTransport:
    def __init__(self):
        self.sent = []
        self.handlers = {}

    def broadcast(self, event, payload):
        self.sent.append((event, payload))
        handlers = self.handlers.get(event, [])
        results = []
        for handler in handlers:
            results.append(handler(payload))
        return results

    def register_handler(self, event, handler):
        self.handlers.setdefault(event, []).append(handler)
        return handler


class FakeLoader:
    def __init__(self):
        self.loaded = None

    def load_assignment(self, assignment):
        self.loaded = assignment


@pytest.fixture(autouse=True)
def _set_dummy_signal_server(monkeypatch):
    monkeypatch.setenv("PARAMIND_SIGSERVER", "ws://127.0.0.1:9999")

    class _FakeTokenizer:
        all_special_ids = []
        eos_token_id = None

        def apply_chat_template(self, messages, tokenize=False, add_generation_prompt=True):
            return ""

        def __call__(self, text, return_tensors="pt"):
            raise RuntimeError("Tokenizer call is not expected in scheduler tests")

        def decode(self, token_ids, skip_special_tokens=True, clean_up_tokenization_spaces=False):
            return ""

    monkeypatch.setattr(
        "inference.NodeRuntime.AutoTokenizer.from_pretrained",
        lambda *args, **kwargs: _FakeTokenizer(),
    )


def test_coordinator_broadcasts_plan_after_collecting_profiles():
    transport = FakeTransport()
    coordinator = ClusterCoordinator(
        transport=transport,
        model_id="Qwen/Qwen2.5-0.5B-Instruct",
        total_layers=24,
    )

    profiles = [
        NodeProfile("node-a", "10.0.0.1", "cpu", 32, 28, 28),
        NodeProfile("node-b", "10.0.0.2", "cpu", 32, 24, 24),
    ]

    plan = coordinator.build_plan(profiles)
    coordinator.broadcast_plan(plan)

    assert transport.sent[0][0] == "cluster_plan"
    assert transport.sent[0][1]["coordinator_id"] == plan.coordinator_id


def test_coordinator_broadcast_plan_includes_route_and_shard_owner_index():
    transport = FakeTransport()
    coordinator = ClusterCoordinator(
        transport=transport,
        model_id="Qwen/Qwen2.5-0.5B-Instruct",
        total_layers=24,
    )

    profiles = [
        NodeProfile("node-a", "10.0.0.1", "cpu", 32, 28, 28),
        NodeProfile("node-b", "10.0.0.2", "cpu", 32, 24, 24),
    ]
    plan = coordinator.build_plan(profiles)

    coordinator.broadcast_plan(plan)

    payload = transport.sent[-1][1]
    ordered = sorted(plan.assignments, key=lambda assignment: assignment.start_layer)
    expected_route = [assignment.node_id for assignment in ordered]
    expected_owner_index = {
        f"{assignment.start_layer}-{assignment.end_layer}": assignment.node_id
        for assignment in ordered
    }

    assert payload["route"] == expected_route
    assert payload["shard_owner_index"] == expected_owner_index


def test_coordinator_build_plan_accepts_profile_payloads():
    coordinator = ClusterCoordinator(
        transport=FakeTransport(),
        model_id="Qwen/Qwen2.5-0.5B-Instruct",
        total_layers=24,
    )
    payloads = [
        NodeProfile("node-a", "10.0.0.1", "cpu", 32, 28, 28).to_dict(),
        NodeProfile("node-b", "10.0.0.2", "cpu", 32, 24, 24).to_dict(),
    ]

    plan = coordinator.build_plan(payloads)

    assert [assignment.node_id for assignment in plan.assignments] == ["node-a"]


def test_node_runtime_loads_only_its_assignment():
    loader = FakeLoader()
    runtime = NodeRuntime(
        node_id="node-b",
        node_group="test-group",
        family=ModelFamily.QWEN,
        total_layers=24,
        model_id="Qwen/Qwen2.5-0.5B-Instruct",
        loader=loader,
    )
    assignment = ShardAssignment(
        node_id="node-b", start_layer=12, end_layer=24, role="last"
    )

    runtime.apply_assignment(
        model_id="Qwen/Qwen2.5-0.5B-Instruct", assignment=assignment
    )

    assert loader.loaded == assignment


def test_node_runtime_rejects_foreign_assignment():
    loader = FakeLoader()
    runtime = NodeRuntime(
        node_id="node-b",
        node_group="test-group",
        family=ModelFamily.QWEN,
        total_layers=24,
        model_id="Qwen/Qwen2.5-0.5B-Instruct",
        loader=loader,
    )
    assignment = ShardAssignment(
        node_id="node-a", start_layer=12, end_layer=24, role="last"
    )

    with pytest.raises(ValueError, match="does not match runtime node_id"):
        runtime.apply_assignment(
            model_id="Qwen/Qwen2.5-0.5B-Instruct", assignment=assignment
        )

    assert loader.loaded is None


def test_transport_driven_cluster_plan_marks_ready():
    transport = FakeTransport()
    coordinator = ClusterCoordinator(
        transport=transport,
        model_id="Qwen/Qwen2.5-0.5B-Instruct",
        total_layers=24,
    )
    loader = FakeLoader()
    runtime = NodeRuntime(
        node_id="node-b",
        node_group="test-group",
        family=ModelFamily.QWEN,
        total_layers=24,
        model_id="Qwen/Qwen2.5-0.5B-Instruct",
        loader=loader,
        on_ready=coordinator.mark_ready,
    )
    runtime.attach_transport(transport)

    plan = coordinator.build_plan(
        [
            NodeProfile("node-b", "10.0.0.2", "cpu", 32, 24, 24),
        ]
    )

    coordinator.broadcast_plan(plan)

    assert loader.loaded == plan.assignments[0]
    assert coordinator.all_ready(plan) is True
    assert coordinator.wait_for_ready(plan, timeout_seconds=0.1) is True
    assert transport.sent[0][0] == "cluster_plan"


def test_node_runtime_persists_global_shard_route_and_owner_index_from_plan():
    transport = FakeTransport()
    coordinator = ClusterCoordinator(
        transport=transport,
        model_id="Qwen/Qwen2.5-0.5B-Instruct",
        total_layers=24,
    )
    runtime = NodeRuntime(
        node_id="node-b",
        node_group="test-group",
        family=ModelFamily.QWEN,
        total_layers=24,
        model_id="Qwen/Qwen2.5-0.5B-Instruct",
        loader=FakeLoader(),
    )
    runtime.attach_transport(transport)

    plan = coordinator.build_plan(
        [
            NodeProfile("node-a", "10.0.0.1", "cpu", 32, 28, 28),
            NodeProfile("node-b", "10.0.0.2", "cpu", 32, 24, 24),
        ]
    )
    coordinator.broadcast_plan(plan)

    snapshot = runtime.debug_snapshot()
    ordered = sorted(plan.assignments, key=lambda assignment: assignment.start_layer)
    expected_route = [assignment.node_id for assignment in ordered]
    expected_owner_index = {
        f"{assignment.start_layer}-{assignment.end_layer}": assignment.node_id
        for assignment in ordered
    }

    assert len(snapshot["global_assignments"]) == len(plan.assignments)
    assert snapshot["route"] == expected_route
    assert snapshot["shard_owner_index"] == expected_owner_index


def test_multi_runtime_static_cluster_smoke():
    transport = FakeTransport()
    coordinator = ClusterCoordinator(
        transport=transport,
        model_id="Qwen/Qwen2.5-0.5B-Instruct",
        total_layers=24,
    )

    loaders = {"node-a": FakeLoader(), "node-b": FakeLoader()}
    runtimes = [
        NodeRuntime(
            node_id=node_id,
            node_group="test-group",
            family=ModelFamily.QWEN,
            total_layers=24,
            model_id="Qwen/Qwen2.5-0.5B-Instruct",
            loader=loader,
            on_ready=coordinator.mark_ready,
        )
        for node_id, loader in loaders.items()
    ]

    for runtime in runtimes:
        runtime.attach_transport(transport)

    plan = coordinator.build_plan(
        [
            NodeProfile("node-a", "10.0.0.1", "cpu", 32, 28, 28),
            NodeProfile("node-b", "10.0.0.2", "cpu", 32, 24, 24),
        ]
    )

    coordinator.broadcast_plan(plan)

    assert loaders["node-a"].loaded == plan.assignments[0]
    assert loaders["node-b"].loaded == plan.assignments[1]
    assert coordinator.all_ready(plan) is True
    assert coordinator.wait_for_ready(plan, timeout_seconds=0.1) is True
    assert len(transport.sent) == 1


def test_static_cluster_can_plan_load_and_mark_ready():
    coordinator = ClusterCoordinator(
        transport=FakeTransport(),
        model_id="Qwen/Qwen2.5-0.5B-Instruct",
        total_layers=24,
    )
    plan = coordinator.build_plan(
        [
            NodeProfile("node-a", "10.0.0.1", "cpu", 32, 28, 28),
            NodeProfile("node-b", "10.0.0.2", "cpu", 32, 24, 24),
        ]
    )

    for assignment in plan.assignments:
        coordinator.mark_ready(assignment.node_id)

    assert coordinator.all_ready(plan) is True


def test_runtime_ignores_other_nodes_reconfiguration_actions_and_keeps_own_subset():
    transport = FakeTransport()
    runtime = NodeRuntime(
        node_id="node-b",
        node_group="test-group",
        family=ModelFamily.QWEN,
        total_layers=24,
        model_id="Qwen/Qwen2.5-0.5B-Instruct",
        loader=FakeLoader(),
    )
    runtime.registry.put(
        ShardRecord(
            (12, 24), role="last", state="serving", shard_obj="warm", active_owner=True
        )
    )
    runtime.attach_transport(transport)

    payload = {
        "model_id": "Qwen/Qwen2.5-0.5B-Instruct",
        "coordinator_id": "node-a",
        "actions_by_node": {
            "node-a": [
                {
                    "node_id": "node-a",
                    "action": "unload",
                    "start_layer": 0,
                    "end_layer": 12,
                }
            ],
            "node-b": [
                {
                    "node_id": "node-b",
                    "action": "keep",
                    "start_layer": 12,
                    "end_layer": 24,
                    "role": "last",
                }
            ],
        },
    }

    actions = runtime.handle_reconfiguration_prepare(payload)

    assert len(actions) == 1
    assert actions[0]["action"] == "keep"


def test_coordinator_broadcasts_reconfiguration_payload():
    transport = FakeTransport()
    coordinator = ClusterCoordinator(
        transport=transport,
        model_id="Qwen/Qwen2.5-0.5B-Instruct",
        total_layers=24,
    )

    coordinator.build_plan(
        [
            NodeProfile(
                "node-a", "10.0.0.1", "cpu", 32, 28, 28, loaded_shards=[(0, 12)]
            ),
            NodeProfile(
                "node-b", "10.0.0.2", "cpu", 32, 24, 24, loaded_shards=[(12, 24)]
            ),
        ]
    )
    reconfiguration = coordinator.build_reconfiguration(
        [
            NodeProfile(
                "node-a", "10.0.0.1", "cpu", 32, 28, 28, loaded_shards=[(0, 12)]
            ),
            NodeProfile(
                "node-b", "10.0.0.2", "cpu", 32, 24, 24, loaded_shards=[(12, 24)]
            ),
            NodeProfile("node-c", "10.0.0.3", "cuda", 64, 40, 80, loaded_shards=[]),
        ]
    )

    coordinator.broadcast_reconfiguration(reconfiguration)

    assert transport.sent[-1][0] == "cluster_reconfigure"
    assert "actions_by_node" in transport.sent[-1][1]


def test_prepare_reconfiguration_waits_for_ready_before_commit():
    transport = FakeTransport()
    coordinator = ClusterCoordinator(
        transport=transport, model_id="Qwen/Qwen2.5-0.5B-Instruct", total_layers=24
    )
    reconfiguration = ReconfigurationPlan(
        model_id="Qwen/Qwen2.5-0.5B-Instruct",
        coordinator_id="node-a",
        actions_by_node={
            "node-a": [
                NodeReconfigurationAction("node-a", "keep", 0, 12, role="first")
            ],
            "node-c": [
                NodeReconfigurationAction(
                    "node-c", "move_in", 12, 24, role="last", from_node_id="node-b"
                )
            ],
        },
    )

    coordinator.begin_reconfiguration(reconfiguration)
    assert transport.sent[0][0] == "cluster_reconfigure_prepare"

    coordinator.mark_reconfig_ready("node-a")
    assert coordinator.reconfiguration_committed is False

    coordinator.mark_reconfig_ready("node-c")
    assert transport.sent[-1][0] == "cluster_reconfigure_commit"
    assert coordinator.reconfiguration_committed is True


def test_reconfiguration_prepare_then_commit_keeps_old_until_new_ready():
    transport = FakeTransport()
    coordinator = ClusterCoordinator(
        transport=transport, model_id="Qwen/Qwen2.5-0.5B-Instruct", total_layers=24
    )
    loader_a = FakeLoader()
    loader_c = FakeLoader()
    runtime_a = NodeRuntime(
        node_id="node-a",
        node_group="test-group",
        family=ModelFamily.QWEN,
        total_layers=24,
        model_id="Qwen/Qwen2.5-0.5B-Instruct",
        loader=loader_a,
    )
    runtime_c = NodeRuntime(
        node_id="node-c",
        node_group="test-group",
        family=ModelFamily.QWEN,
        total_layers=24,
        model_id="Qwen/Qwen2.5-0.5B-Instruct",
        loader=loader_c,
    )
    runtime_a.attach_transport(transport, on_ready=coordinator.mark_reconfig_ready)
    runtime_c.attach_transport(transport, on_ready=coordinator.mark_reconfig_ready)

    runtime_a.registry.put(
        ShardRecord(
            (0, 12), role="first", state="serving", shard_obj="warm", active_owner=True
        )
    )

    reconfiguration = ReconfigurationPlan(
        model_id="Qwen/Qwen2.5-0.5B-Instruct",
        coordinator_id="node-c",
        actions_by_node={
            "node-a": [
                NodeReconfigurationAction("node-a", "unload", 0, 12, role="first")
            ],
            "node-c": [
                NodeReconfigurationAction(
                    "node-c", "move_in", 0, 12, role="first", from_node_id="node-a"
                )
            ],
        },
    )

    coordinator.begin_reconfiguration(reconfiguration)

    assert runtime_a.registry.get((0, 12)) is None
    promoted = runtime_c.registry.get((0, 12))
    assert promoted is not None
    assert promoted.state == "serving"
    assert promoted.active_owner is True


def test_begin_reconfiguration_sets_phase_and_timestamps():
    transport = FakeTransport()
    coordinator = ClusterCoordinator(
        transport=transport, model_id="Qwen/Qwen2.5-0.5B-Instruct", total_layers=24
    )
    reconfiguration = ReconfigurationPlan(
        model_id="Qwen/Qwen2.5-0.5B-Instruct",
        coordinator_id="node-a",
        actions_by_node={
            "node-a": [NodeReconfigurationAction("node-a", "keep", 0, 12, role="first")]
        },
    )

    coordinator.begin_reconfiguration(reconfiguration)

    snapshot = coordinator.debug_snapshot()
    assert snapshot["phase"] == "prepare"
    assert snapshot["reconfiguration_id"] is not None
    assert snapshot["prepare_started_at"] is not None


def test_mark_reconfig_ready_moves_phase_to_commit_when_all_nodes_ready():
    transport = FakeTransport()
    coordinator = ClusterCoordinator(
        transport=transport, model_id="Qwen/Qwen2.5-0.5B-Instruct", total_layers=24
    )
    reconfiguration = ReconfigurationPlan(
        model_id="Qwen/Qwen2.5-0.5B-Instruct",
        coordinator_id="node-a",
        actions_by_node={
            "node-a": [NodeReconfigurationAction("node-a", "keep", 0, 12, role="first")]
        },
    )

    coordinator.begin_reconfiguration(reconfiguration)
    coordinator.mark_reconfig_ready("node-a")

    snapshot = coordinator.debug_snapshot()
    assert snapshot["phase"] in {"commit", "complete"}
    assert snapshot["commit_started_at"] is not None
