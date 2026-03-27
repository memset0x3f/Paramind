from inference.ClusterTypes import NodeReconfigurationAction, ReconfigurationPlan, ShardAssignment
from inference.NodeRuntime import NodeRuntime
from inference.ShardConfig import ModelFamily
from inference.ShardRegistry import ShardRecord, ShardRegistry


class FakeLoader:
    def __init__(self):
        self.loaded = None

    def load_assignment(self, assignment):
        self.loaded = assignment


class FakeTransport:
    def __init__(self):
        self.sent = []
        self.handlers = {}

    def broadcast(self, event, payload):
        self.sent.append((event, payload))
        return [handler(payload) for handler in self.handlers.get(event, [])]

    def register_handler(self, event, handler):
        self.handlers.setdefault(event, []).append(handler)
        return handler


def test_shard_registry_tracks_lifecycle_states_by_range():
    registry = ShardRegistry()
    registry.put(
        ShardRecord(
            shard_key=(0, 12),
            role="first",
            state="loading",
            shard_obj="demo-shard",
            active_owner=False,
        )
    )

    record = registry.get((0, 12))

    assert record is not None
    assert record.state == "loading"
    assert record.active_owner is False


def test_shard_record_defaults_include_inflight_and_timestamps():
    record = ShardRecord(shard_key=(0, 12), role="first", state="serving")

    assert record.inflight_requests == 0
    assert record.pending_unload is False
    assert record.last_prepare_ts is None
    assert record.last_commit_ts is None


def test_registry_replace_updates_existing_record_for_same_shard_key():
    registry = ShardRegistry()
    registry.put(ShardRecord((0, 12), role="first", state="ready", inflight_requests=0))
    registry.put(ShardRecord((0, 12), role="first", state="serving", inflight_requests=2))

    record = registry.get((0, 12))
    assert record is not None
    assert record.state == "serving"
    assert record.inflight_requests == 2


def test_prepare_load_transitions_absent_to_ready_and_stores_shard():
    loader = FakeLoader()
    runtime = NodeRuntime(node_id="node-a", family=ModelFamily.QWEN, total_layers=24, loader=loader)

    actions = [
        NodeReconfigurationAction(
            node_id="node-a",
            action="load",
            start_layer=0,
            end_layer=12,
            role="first",
        )
    ]

    result = runtime.prepare_reconfiguration(model_id="Qwen/Qwen2.5-0.5B-Instruct", actions=actions)

    record = runtime.registry.get((0, 12))
    assert result[0]["status"] == "ready"
    assert loader.loaded.start_layer == 0
    assert record is not None
    assert record.state == "ready"


def test_prepare_keep_validates_existing_serving_shard_without_reloading():
    loader = FakeLoader()
    runtime = NodeRuntime(node_id="node-a", family=ModelFamily.QWEN, total_layers=24, loader=loader)
    runtime.registry.put(ShardRecord((0, 12), role="first", state="serving", shard_obj="warm", active_owner=True))

    actions = [NodeReconfigurationAction("node-a", "keep", 0, 12, role="first")]
    result = runtime.prepare_reconfiguration(model_id="Qwen/Qwen2.5-0.5B-Instruct", actions=actions)

    assert result[0]["status"] == "ready"
    assert loader.loaded is None
    assert runtime.registry.get((0, 12)).state == "serving"


def test_prepare_move_in_uses_local_reload_and_stays_non_active_until_commit():
    loader = FakeLoader()
    runtime = NodeRuntime(node_id="node-c", family=ModelFamily.QWEN, total_layers=24, loader=loader)

    actions = [NodeReconfigurationAction("node-c", "move_in", 0, 12, role="first", from_node_id="node-a")]
    runtime.prepare_reconfiguration(model_id="Qwen/Qwen2.5-0.5B-Instruct", actions=actions)

    record = runtime.registry.get((0, 12))
    assert loader.loaded.start_layer == 0
    assert record.state == "ready"
    assert record.active_owner is False


def test_prepare_unload_marks_pending_but_does_not_remove_serving_shard():
    runtime = NodeRuntime(node_id="node-a", family=ModelFamily.QWEN, total_layers=24, loader=FakeLoader())
    runtime.registry.put(ShardRecord((0, 12), role="first", state="serving", shard_obj="warm", active_owner=True))

    actions = [NodeReconfigurationAction("node-a", "unload", 0, 12, role="first")]
    runtime.prepare_reconfiguration(model_id="Qwen/Qwen2.5-0.5B-Instruct", actions=actions)

    record = runtime.registry.get((0, 12))
    assert record is not None
    assert record.pending_unload is True
    assert record.state == "serving"


def test_commit_promotes_ready_move_in_to_serving_owner():
    runtime = NodeRuntime(node_id="node-c", family=ModelFamily.QWEN, total_layers=24, loader=FakeLoader())
    runtime.registry.put(ShardRecord((0, 12), role="first", state="ready", shard_obj="warm", active_owner=False))

    runtime.commit_reconfiguration([NodeReconfigurationAction("node-c", "move_in", 0, 12, role="first", from_node_id="node-a")])

    record = runtime.registry.get((0, 12))
    assert record.state == "serving"
    assert record.active_owner is True


def test_commit_unload_releases_pending_shard():
    runtime = NodeRuntime(node_id="node-a", family=ModelFamily.QWEN, total_layers=24, loader=FakeLoader())
    runtime.registry.put(ShardRecord((0, 12), role="first", state="serving", shard_obj="warm", active_owner=True, pending_unload=True))

    runtime.commit_reconfiguration([NodeReconfigurationAction("node-a", "unload", 0, 12, role="first")])

    assert runtime.registry.get((0, 12)) is None


def test_prepare_handler_loads_and_signals_ready_for_this_node():
    transport = FakeTransport()
    ready = []
    runtime = NodeRuntime(
        node_id="node-a",
        family=ModelFamily.QWEN,
        total_layers=24,
        loader=FakeLoader(),
        on_ready=ready.append,
    )
    runtime.attach_transport(transport)

    reconfiguration = ReconfigurationPlan(
        model_id="Qwen/Qwen2.5-0.5B-Instruct",
        coordinator_id="node-a",
        actions_by_node={
            "node-a": [NodeReconfigurationAction("node-a", "load", 0, 12, role="first")],
            "node-b": [NodeReconfigurationAction("node-b", "keep", 12, 24, role="last")],
        },
    )

    transport.broadcast(
        "cluster_reconfigure_prepare",
        {
            "model_id": reconfiguration.model_id,
            "coordinator_id": reconfiguration.coordinator_id,
            "actions_by_node": {
                node_id: [action.to_dict() for action in actions]
                for node_id, actions in reconfiguration.actions_by_node.items()
            },
        },
    )

    assert ready == ["node-a"]
    assert runtime.registry.get((0, 12)).state == "ready"


def test_commit_handler_promotes_and_releases_for_this_node_only():
    transport = FakeTransport()
    runtime = NodeRuntime(node_id="node-a", family=ModelFamily.QWEN, total_layers=24, loader=FakeLoader())
    runtime.registry.put(ShardRecord((0, 12), role="first", state="ready", shard_obj="warm", active_owner=False))
    runtime.registry.put(ShardRecord((12, 24), role="last", state="serving", shard_obj="old", active_owner=True, pending_unload=True))
    runtime.attach_transport(transport)

    transport.broadcast(
        "cluster_reconfigure_commit",
        {
            "model_id": "Qwen/Qwen2.5-0.5B-Instruct",
            "coordinator_id": "node-c",
            "actions_by_node": {
                "node-a": [
                    NodeReconfigurationAction("node-a", "move_in", 0, 12, role="first", from_node_id="node-b").to_dict(),
                    NodeReconfigurationAction("node-a", "unload", 12, 24, role="last").to_dict(),
                ],
                "node-b": [NodeReconfigurationAction("node-b", "keep", 12, 24, role="last").to_dict()],
            },
        },
    )

    assert runtime.registry.get((0, 12)).state == "serving"
    assert runtime.registry.get((12, 24)) is None


def test_begin_request_increments_inflight_on_serving_shard():
    runtime = NodeRuntime(node_id="node-a", family=ModelFamily.QWEN, total_layers=24, loader=FakeLoader())
    runtime.registry.put(ShardRecord((0, 12), role="first", state="serving", active_owner=True))

    runtime.begin_request((0, 12))

    assert runtime.registry.get((0, 12)).inflight_requests == 1


def test_finish_request_releases_draining_shard_when_inflight_reaches_zero():
    runtime = NodeRuntime(node_id="node-a", family=ModelFamily.QWEN, total_layers=24, loader=FakeLoader())
    runtime.registry.put(
        ShardRecord(
            (0, 12),
            role="first",
            state="draining",
            shard_obj="warm",
            active_owner=False,
            pending_unload=True,
            inflight_requests=1,
        )
    )

    runtime.finish_request((0, 12))

    assert runtime.registry.get((0, 12)) is None


def test_commit_unload_keeps_shard_when_inflight_requests_exist_and_marks_draining():
    runtime = NodeRuntime(node_id="node-a", family=ModelFamily.QWEN, total_layers=24, loader=FakeLoader())
    runtime.registry.put(
        ShardRecord(
            (0, 12),
            role="first",
            state="serving",
            shard_obj="warm",
            active_owner=True,
            pending_unload=True,
            inflight_requests=2,
        )
    )

    runtime.commit_reconfiguration([NodeReconfigurationAction("node-a", "unload", 0, 12, role="first")])

    record = runtime.registry.get((0, 12))
    assert record is not None
    assert record.state == "draining"
    assert record.active_owner is False


def test_commit_unload_drops_immediately_when_no_inflight_requests_exist():
    runtime = NodeRuntime(node_id="node-a", family=ModelFamily.QWEN, total_layers=24, loader=FakeLoader())
    runtime.registry.put(
        ShardRecord(
            (0, 12),
            role="first",
            state="serving",
            shard_obj="warm",
            active_owner=True,
            pending_unload=True,
            inflight_requests=0,
        )
    )

    runtime.commit_reconfiguration([NodeReconfigurationAction("node-a", "unload", 0, 12, role="first")])

    assert runtime.registry.get((0, 12)) is None


def test_node_runtime_debug_snapshot_reports_shard_state_and_inflight():
    runtime = NodeRuntime(node_id="node-a", family=ModelFamily.QWEN, total_layers=24, loader=FakeLoader())
    runtime.registry.put(
        ShardRecord(
            (0, 12),
            role="first",
            state="draining",
            active_owner=False,
            pending_unload=True,
            inflight_requests=2,
        )
    )

    snapshot = runtime.debug_snapshot()

    assert snapshot["node_id"] == "node-a"
    assert snapshot["shards"][0]["state"] == "draining"
    assert snapshot["shards"][0]["inflight_requests"] == 2
    assert snapshot["shards"][0]["pending_unload"] is True


def test_old_owner_can_finish_existing_request_after_commit_while_new_owner_serves_new_work():
    old_runtime = NodeRuntime(node_id="node-a", family=ModelFamily.QWEN, total_layers=24, loader=FakeLoader())
    new_runtime = NodeRuntime(node_id="node-c", family=ModelFamily.QWEN, total_layers=24, loader=FakeLoader())

    old_runtime.registry.put(
        ShardRecord(
            (0, 12),
            role="first",
            state="serving",
            active_owner=True,
            pending_unload=True,
            inflight_requests=1,
        )
    )
    new_runtime.registry.put(
        ShardRecord(
            (0, 12),
            role="first",
            state="ready",
            active_owner=False,
            inflight_requests=0,
        )
    )

    old_runtime.commit_reconfiguration([NodeReconfigurationAction("node-a", "unload", 0, 12, role="first")])
    new_runtime.commit_reconfiguration([NodeReconfigurationAction("node-c", "move_in", 0, 12, role="first", from_node_id="node-a")])

    assert old_runtime.registry.get((0, 12)).state == "draining"
    assert new_runtime.registry.get((0, 12)).state == "serving"

    new_runtime.begin_request((0, 12))
    old_runtime.finish_request((0, 12))

    assert new_runtime.registry.get((0, 12)).inflight_requests == 1
    assert old_runtime.registry.get((0, 12)) is None
