from __future__ import annotations

from collections import defaultdict
import json
from pathlib import Path
import sys
from typing import Callable, Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scheduler.ClusterCoordinator import ClusterCoordinator
from scheduler.ClusterTypes import (
    NodeReconfigurationAction,
    ReconfigurationPlan,
    ShardAssignment,
)
from inference.NodeRuntime import NodeRuntime
from inference.ShardConfig import ModelFamily
from inference.ShardRegistry import ShardRecord
from scripts.demo_rendering import render_key_value_block, render_section, render_table

MODEL_ID = "Qwen/Qwen2.5-0.5B-Instruct"
TOTAL_LAYERS = 12


class DemoTransport:
    def __init__(self):
        self.handlers: dict[str, list[Callable[[dict], object]]] = defaultdict(list)
        self.events: list[str] = []

    def register_handler(self, event: str, handler: Callable[[dict], object]):
        self.handlers[event].append(handler)

    def broadcast(self, event: str, payload: dict):
        self.events.append(f"{event}: model={payload['model_id']}")
        for handler in self.handlers.get(event, []):
            handler(payload)


class DemoLoader:
    def __init__(self, node_id: str):
        self.node_id = node_id
        self.loaded = None

    def load_assignment(self, assignment: ShardAssignment):
        self.loaded = {
            "node_id": self.node_id,
            "shard_key": (assignment.start_layer, assignment.end_layer),
            "role": assignment.role,
        }


def render_parameter_guide() -> str:
    return "\n".join(
        [
            "=== PARAMETER GUIDE ===",
            "cluster_reconfigure_prepare: coordinator 开始 prepare 阶段，目标 owner 先把 shard 载入到 ready 状态。",
            "cluster_reconfigure_commit: 所有目标节点 ready 后进入 commit；新 owner 变为 serving，旧 owner 进入 draining 或释放。",
            "state: serving 表示可接新请求，ready 表示已准备但未接流量，draining 表示只等待旧请求完成。",
            "pending_unload: commit 后需要释放的旧 shard 标记；只有 inflight_requests 归零后才真正删除。",
            "inflight_requests: 该 shard 当前仍在处理的请求数。",
            "reconfiguration_committed: coordinator 是否已经完成 commit 广播。",
        ]
    )


def _render_event_log(transport: DemoTransport) -> str:
    body = "\n".join(transport.events) if transport.events else "(no events yet)"
    return render_section("EVENT LOG", body)


def _render_shard_states(runtimes: list[NodeRuntime]) -> str:
    rows: list[tuple[object, ...]] = []
    for runtime in runtimes:
        snapshot = runtime.debug_snapshot()
        if not snapshot["shards"]:
            rows.append((runtime.node_id, "-", "-", "-", "-", "-", "-"))
            continue
        for shard in snapshot["shards"]:
            start_layer, end_layer = shard["shard_key"]
            rows.append(
                (
                    runtime.node_id,
                    f"{start_layer}-{end_layer}",
                    shard["state"],
                    shard["active_owner"],
                    shard["pending_unload"],
                    shard["inflight_requests"],
                    shard["role"],
                )
            )
    return render_table(
        "SHARD STATES",
        columns=[
            "node",
            "shard",
            "state",
            "active_owner",
            "pending_unload",
            "inflight_requests",
            "role",
        ],
        rows=rows,
    )


def _render_ready_commit_status(
    coordinator: ClusterCoordinator, prepared_nodes: list[str]
) -> str:
    snapshot = coordinator.debug_snapshot()
    return render_table(
        "READY / COMMIT STATUS",
        columns=["field", "value"],
        rows=[
            ("phase", snapshot["phase"]),
            ("ready_nodes", snapshot["ready_nodes"]),
            ("reconfig_ready_nodes", snapshot["reconfig_ready_nodes"]),
            ("prepared_nodes", prepared_nodes),
            ("reconfiguration_committed", snapshot["reconfiguration_committed"]),
            ("reconfiguration_id", snapshot["reconfiguration_id"]),
        ],
    )


def _render_current_cluster(
    coordinator: ClusterCoordinator, runtimes: list[NodeRuntime]
) -> str:
    snapshot = coordinator.debug_snapshot()
    return render_key_value_block(
        "CURRENT CLUSTER",
        {
            "model": snapshot["model_id"],
            "total_layers": snapshot["total_layers"],
            "current_coordinator_id": snapshot["current_coordinator_id"],
            "runtime_nodes": ", ".join(runtime.node_id for runtime in runtimes),
            "phase": snapshot["phase"],
        },
    )


def _render_actions(plan: ReconfigurationPlan) -> str:
    rows = []
    for node_id, actions in sorted(plan.actions_by_node.items()):
        for action in actions:
            rows.append(
                (
                    node_id,
                    action.action,
                    f"{action.start_layer}-{action.end_layer}",
                    action.role,
                    action.from_node_id or "-",
                )
            )
    return render_table(
        "ACTIONS",
        columns=["node", "action", "shard", "role", "from_node_id"],
        rows=rows,
    )


def render_runtime_state(
    title: str,
    coordinator: ClusterCoordinator,
    runtimes: list[NodeRuntime],
    transport: DemoTransport,
    plan: ReconfigurationPlan,
    prepared_nodes: list[str],
) -> str:
    return "\n".join(
        [
            f"=== {title} ===",
            _render_current_cluster(coordinator, runtimes),
            _render_actions(plan),
            _render_event_log(transport),
            _render_shard_states(runtimes),
            _render_ready_commit_status(coordinator, prepared_nodes),
        ]
    )


def _snapshot_state(
    title: str,
    coordinator: ClusterCoordinator,
    runtimes: list[NodeRuntime],
    transport: DemoTransport,
    plan: ReconfigurationPlan,
    prepared_nodes: list[str],
) -> dict:
    return {
        "title": title,
        "coordinator": coordinator.debug_snapshot(),
        "runtimes": [runtime.debug_snapshot() for runtime in runtimes],
        "event_log": list(transport.events),
        "prepared_nodes": list(prepared_nodes),
        "actions_by_node": {
            node_id: [action.to_dict() for action in actions]
            for node_id, actions in plan.actions_by_node.items()
        },
    }


def render_runtime_snapshot(snapshot: dict) -> str:
    coordinator_snapshot = snapshot["coordinator"]
    rows = []
    for runtime in snapshot["runtimes"]:
        if not runtime["shards"]:
            rows.append((runtime["node_id"], "-", "-", "-", "-", "-", "-"))
            continue
        for shard in runtime["shards"]:
            start_layer, end_layer = shard["shard_key"]
            rows.append(
                (
                    runtime["node_id"],
                    f"{start_layer}-{end_layer}",
                    shard["state"],
                    shard["active_owner"],
                    shard["pending_unload"],
                    shard["inflight_requests"],
                    shard["role"],
                )
            )
    action_rows = []
    for node_id, actions in sorted(snapshot["actions_by_node"].items()):
        for action in actions:
            action_rows.append(
                (
                    node_id,
                    action["action"],
                    f"{action['start_layer']}-{action['end_layer']}",
                    action["role"],
                    action.get("from_node_id") or "-",
                )
            )
    return "\n".join(
        [
            f"=== {snapshot['title']} ===",
            render_key_value_block(
                "CURRENT CLUSTER",
                {
                    "model": coordinator_snapshot["model_id"],
                    "total_layers": coordinator_snapshot["total_layers"],
                    "current_coordinator_id": coordinator_snapshot[
                        "current_coordinator_id"
                    ],
                    "runtime_nodes": ", ".join(
                        runtime["node_id"] for runtime in snapshot["runtimes"]
                    ),
                    "phase": coordinator_snapshot["phase"],
                },
            ),
            render_table(
                "ACTIONS",
                columns=["node", "action", "shard", "role", "from_node_id"],
                rows=action_rows,
            ),
            render_section(
                "EVENT LOG",
                (
                    "\n".join(snapshot["event_log"])
                    if snapshot["event_log"]
                    else "(no events yet)"
                ),
            ),
            render_table(
                "SHARD STATES",
                columns=[
                    "node",
                    "shard",
                    "state",
                    "active_owner",
                    "pending_unload",
                    "inflight_requests",
                    "role",
                ],
                rows=rows,
            ),
            render_table(
                "READY / COMMIT STATUS",
                columns=["field", "value"],
                rows=[
                    ("phase", coordinator_snapshot["phase"]),
                    ("ready_nodes", coordinator_snapshot["ready_nodes"]),
                    (
                        "reconfig_ready_nodes",
                        coordinator_snapshot["reconfig_ready_nodes"],
                    ),
                    ("prepared_nodes", snapshot["prepared_nodes"]),
                    (
                        "reconfiguration_committed",
                        coordinator_snapshot["reconfiguration_committed"],
                    ),
                    ("reconfiguration_id", coordinator_snapshot["reconfiguration_id"]),
                ],
            ),
        ]
    )


def build_demo_state() -> dict:
    transport = DemoTransport()
    prepared_nodes: list[str] = []

    coordinator = ClusterCoordinator(
        transport=transport, model_id=MODEL_ID, total_layers=TOTAL_LAYERS
    )

    def _mark_prepared(node_id: str):
        prepared_nodes.append(node_id)
        transport.events.append(f"runtime_ready: node={node_id}")

    old_runtime = NodeRuntime(
        node_id="node-a",
        family=ModelFamily.QWEN,
        total_layers=TOTAL_LAYERS,
        device="cpu",
        loader=DemoLoader("node-a"),
        on_ready=_mark_prepared,
    )
    new_runtime = NodeRuntime(
        node_id="node-c",
        family=ModelFamily.QWEN,
        total_layers=TOTAL_LAYERS,
        device="cpu",
        loader=DemoLoader("node-c"),
        on_ready=_mark_prepared,
    )
    old_runtime.attach_transport(transport)
    new_runtime.attach_transport(transport)

    initial_assignment = ShardAssignment(
        node_id="node-a",
        start_layer=0,
        end_layer=TOTAL_LAYERS,
        role="first",
    )
    old_runtime.load_assignment(MODEL_ID, initial_assignment)
    old_runtime.registry.put(
        ShardRecord(
            shard_key=(0, TOTAL_LAYERS),
            role="first",
            state="serving",
            shard_obj=old_runtime.local_shard,
            active_owner=True,
        )
    )
    old_runtime.begin_request((0, TOTAL_LAYERS))
    coordinator.current_plan = type(
        "CurrentPlan",
        (),
        {
            "model_id": MODEL_ID,
            "coordinator_id": "node-a",
            "assignments": [initial_assignment],
        },
    )()

    reconfiguration = ReconfigurationPlan(
        model_id=MODEL_ID,
        coordinator_id="node-a",
        actions_by_node={
            "node-a": [
                NodeReconfigurationAction(
                    node_id="node-a",
                    action="unload",
                    start_layer=0,
                    end_layer=TOTAL_LAYERS,
                    role="first",
                )
            ],
            "node-c": [
                NodeReconfigurationAction(
                    node_id="node-c",
                    action="move_in",
                    start_layer=0,
                    end_layer=TOTAL_LAYERS,
                    role="first",
                    from_node_id="node-a",
                )
            ],
        },
    )

    runtimes = [old_runtime, new_runtime]
    before = _snapshot_state(
        "BEFORE RECONFIGURATION",
        coordinator,
        runtimes,
        transport,
        reconfiguration,
        prepared_nodes,
    )
    coordinator.begin_reconfiguration(reconfiguration)
    after_prepare = _snapshot_state(
        "AFTER PREPARE",
        coordinator,
        runtimes,
        transport,
        reconfiguration,
        prepared_nodes,
    )
    for node_id in list(prepared_nodes):
        coordinator.mark_reconfig_ready(node_id)
    after_commit = _snapshot_state(
        "AFTER COMMIT",
        coordinator,
        runtimes,
        transport,
        reconfiguration,
        prepared_nodes,
    )
    return {
        "parameter_guide": render_parameter_guide(),
        "snapshots": [before, after_prepare, after_commit],
        "final_snapshot": after_commit,
    }


def run_demo() -> str:
    state = build_demo_state()
    rendered_snapshots = [
        render_runtime_snapshot(snapshot) for snapshot in state["snapshots"]
    ]
    return "\n\n".join([state["parameter_guide"], *rendered_snapshots])


def run_demo_json() -> str:
    return json.dumps(build_demo_state(), ensure_ascii=False, indent=2)


def main(argv: Sequence[str] | None = None) -> int:
    _ = argv
    print(run_demo())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
