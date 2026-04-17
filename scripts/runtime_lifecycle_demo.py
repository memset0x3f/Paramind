from __future__ import annotations

from collections import defaultdict
import json
import os
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
from inference.ShardConfig import ModelFamily
from inference.ShardRegistry import ShardRecord, ShardRegistry
from scripts.demo_rendering import render_key_value_block, render_section, render_table

try:
    from inference.NodeRuntime import NodeRuntime as RuntimeNode
    import inference.NodeRuntime as node_runtime_module
except ImportError:
    RuntimeNode = None  # type: ignore[assignment]
    node_runtime_module = None

MODEL_ID = "Qwen/Qwen2.5-0.5B-Instruct"
TOTAL_LAYERS = 12


class DemoTransport:
    def __init__(self):
        self.handlers: dict[str, list[Callable[[dict], object]]] = defaultdict(list)
        self.events: list[str] = []

    def register_handler(self, event: str, handler: Callable[[dict], object]):
        self.handlers[event].append(handler)

    def broadcast(self, message: bytes | str | dict, include_self: bool = False):
        _ = include_self
        if isinstance(message, bytes):
            payload = json.loads(message.decode("utf-8"))
        elif isinstance(message, str):
            payload = json.loads(message)
        elif isinstance(message, dict):
            payload = message
        else:
            raise TypeError(f"Unsupported message payload type: {type(message)!r}")

        event = str(payload.get("type", "unknown"))
        self.events.append(f"{event}: model={payload.get('model_id', '-')}")
        for handler in self.handlers.get(event, []):
            handler(payload)


class DemoP2PStub:
    def sendToPeer(self, message: bytes, peer_uuid: str):
        _ = message
        _ = peer_uuid
        return None


class DemoNodeRuntime:
    """Fallback runtime that mirrors NodeRuntime lifecycle state transitions."""

    def __init__(
        self,
        node_id: str,
        node_group: str,
        family: ModelFamily,
        total_layers: int,
        model_id: str,
        shard_assignment: ShardAssignment | None = None,
        device: str = "cpu",
        loader=None,
        on_ready: Callable[..., object] | None = None,
    ):
        self.node_id = node_id
        self.node_group = node_group
        self.family = family
        self.total_layers = total_layers
        self.model_id = model_id
        self.device = device
        self.loader = loader
        self.on_ready = on_ready
        self.assignment = shard_assignment
        self.local_shard = None
        self.registry = ShardRegistry()
        self.global_assignments: list[ShardAssignment] = []
        self.route: list[str] = []
        self.shard_owner_index: dict[str, str] = {}
        if shard_assignment is not None:
            self.apply_assignment(model_id=model_id, assignment=shard_assignment)

    def attach_transport(self, transport, on_ready: Callable[[str], object] | None = None):
        if on_ready is not None:
            self.on_ready = on_ready
        transport.register_handler("cluster_plan", self.handle_cluster_plan)
        transport.register_handler(
            "cluster_reconfigure_prepare", self.handle_reconfiguration_prepare
        )
        transport.register_handler(
            "cluster_reconfigure_commit", self.handle_reconfiguration_commit
        )
        return transport

    def signal_ready(
        self,
        reconfiguration_id: int | None = None,
        coordinator_id: str | None = None,
    ):
        _ = coordinator_id
        if self.on_ready is None:
            return None
        if reconfiguration_id is None:
            return self.on_ready(self.node_id)
        try:
            return self.on_ready(self.node_id, reconfiguration_id)
        except TypeError:
            return self.on_ready(self.node_id)

    def apply_assignment(self, model_id: str, assignment: ShardAssignment):
        _ = model_id
        if assignment.node_id != self.node_id:
            raise ValueError(
                f"Assignment node_id {assignment.node_id!r} does not match runtime node_id {self.node_id!r}"
            )
        self.assignment = assignment
        if self.loader is not None and hasattr(self.loader, "load_assignment"):
            self.loader.load_assignment(assignment)
            self.local_shard = getattr(self.loader, "loaded", assignment)
        else:
            self.local_shard = assignment

    def handle_cluster_plan(self, payload: dict):
        assignments = payload.get("assignments", [])
        self.global_assignments = [
            ShardAssignment(
                node_id=item["node_id"],
                start_layer=item["start_layer"],
                end_layer=item["end_layer"],
                role=item.get("role", "middle"),
                source_node_id=item.get("source_node_id"),
            )
            for item in assignments
        ]
        ordered = sorted(self.global_assignments, key=lambda item: item.start_layer)
        self.route = [assignment.node_id for assignment in ordered]
        self.shard_owner_index = {
            f"{assignment.start_layer}-{assignment.end_layer}": assignment.node_id
            for assignment in ordered
        }

        assignment = next(
            (item for item in self.global_assignments if item.node_id == self.node_id),
            None,
        )
        if assignment is None:
            return None
        self.apply_assignment(model_id=payload["model_id"], assignment=assignment)
        self.registry.put(
            ShardRecord(
                shard_key=(assignment.start_layer, assignment.end_layer),
                role=assignment.role,
                state="serving",
                shard_obj=self.local_shard,
                active_owner=True,
            )
        )
        self.signal_ready()
        return assignment

    def handle_reconfiguration_plan(self, payload: dict):
        raw_actions = payload.get("actions_by_node", {}).get(self.node_id, [])
        return [NodeReconfigurationAction.from_dict(item) for item in raw_actions]

    def handle_reconfiguration_prepare(self, payload: dict):
        actions = self.handle_reconfiguration_plan(payload)
        if not actions:
            return []
        result = self.prepare_reconfiguration(
            model_id=payload["model_id"], actions=actions
        )
        self.signal_ready(
            reconfiguration_id=payload.get("reconfiguration_id"),
            coordinator_id=payload.get("coordinator_id"),
        )
        return result

    def handle_reconfiguration_commit(self, payload: dict):
        actions = self.handle_reconfiguration_plan(payload)
        if not actions:
            return []
        self.commit_reconfiguration(actions)
        return actions

    def prepare_reconfiguration(
        self, model_id: str, actions: list[NodeReconfigurationAction]
    ):
        results = []
        for action in actions:
            shard_key = (action.start_layer, action.end_layer)
            if action.action == "keep":
                record = self.registry.get(shard_key)
                if record is None:
                    raise RuntimeError(f"Missing shard for keep: {shard_key}")
                results.append(
                    {"shard_key": shard_key, "status": "ready", "action": "keep"}
                )
                continue
            if action.action in {"load", "move_in"}:
                self.registry.put(
                    ShardRecord(
                        shard_key=shard_key,
                        role=action.role,
                        state="loading",
                        active_owner=False,
                    )
                )
                assignment = ShardAssignment(
                    node_id=self.node_id,
                    start_layer=action.start_layer,
                    end_layer=action.end_layer,
                    role=action.role,
                    source_node_id=action.from_node_id,
                )
                self.apply_assignment(model_id=model_id, assignment=assignment)
                self.registry.put(
                    ShardRecord(
                        shard_key=shard_key,
                        role=action.role,
                        state="ready",
                        shard_obj=self.local_shard,
                        active_owner=False,
                    )
                )
                results.append(
                    {"shard_key": shard_key, "status": "ready", "action": action.action}
                )
                continue
            if action.action == "unload":
                record = self.registry.get(shard_key)
                if record is not None:
                    record.pending_unload = True
                results.append(
                    {"shard_key": shard_key, "status": "deferred", "action": "unload"}
                )
        return results

    def commit_reconfiguration(self, actions: list[NodeReconfigurationAction]):
        for action in actions:
            shard_key = (action.start_layer, action.end_layer)
            record = self.registry.get(shard_key)
            if action.action in {"keep", "load", "move_in"}:
                if record is None:
                    raise RuntimeError(f"Missing shard during commit: {shard_key}")
                self.registry.put(
                    ShardRecord(
                        shard_key=record.shard_key,
                        role=record.role,
                        state="serving",
                        shard_obj=record.shard_obj,
                        active_owner=True,
                        pending_unload=False,
                        inflight_requests=record.inflight_requests,
                    )
                )
                continue
            if (
                action.action == "unload"
                and record is not None
                and record.pending_unload
            ):
                if record.inflight_requests > 0:
                    self.registry.put(
                        ShardRecord(
                            shard_key=record.shard_key,
                            role=record.role,
                            state="draining",
                            shard_obj=record.shard_obj,
                            active_owner=False,
                            pending_unload=True,
                            inflight_requests=record.inflight_requests,
                        )
                    )
                else:
                    self.registry.remove(shard_key)

    def begin_request(self, shard_key: tuple[int, int]):
        record = self.registry.get(shard_key)
        if record is None:
            raise RuntimeError(f"Missing shard for request: {shard_key}")
        if record.state != "serving":
            raise RuntimeError(f"Shard is not serving new requests: {shard_key}")
        self.registry.put(
            ShardRecord(
                shard_key=record.shard_key,
                role=record.role,
                state=record.state,
                shard_obj=record.shard_obj,
                active_owner=record.active_owner,
                pending_unload=record.pending_unload,
                inflight_requests=record.inflight_requests + 1,
            )
        )

    def debug_snapshot(self) -> dict:
        shards = []
        for record in sorted(
            self.registry.all_records(), key=lambda item: item.shard_key
        ):
            shards.append(
                {
                    "shard_key": record.shard_key,
                    "role": record.role,
                    "state": record.state,
                    "active_owner": record.active_owner,
                    "pending_unload": record.pending_unload,
                    "inflight_requests": record.inflight_requests,
                    "last_prepare_ts": record.last_prepare_ts,
                    "last_commit_ts": record.last_commit_ts,
                }
            )
        return {
            "node_id": self.node_id,
            "assignment": (
                {
                    "node_id": self.assignment.node_id,
                    "start_layer": self.assignment.start_layer,
                    "end_layer": self.assignment.end_layer,
                    "role": self.assignment.role,
                    "source_node_id": self.assignment.source_node_id,
                }
                if self.assignment is not None
                else None
            ),
            "global_assignments": [
                {
                    "node_id": item.node_id,
                    "start_layer": item.start_layer,
                    "end_layer": item.end_layer,
                    "role": item.role,
                    "source_node_id": item.source_node_id,
                }
                for item in self.global_assignments
            ],
            "route": list(self.route),
            "shard_owner_index": dict(self.shard_owner_index),
            "shards": shards,
            "has_local_shard": self.local_shard is not None,
        }


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


def _render_shard_states(runtimes: list[object]) -> str:
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


def _render_current_cluster(coordinator: ClusterCoordinator, runtimes: list[object]) -> str:
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
    runtimes: list[object],
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
    runtimes: list[object],
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
    runtime_class = DemoNodeRuntime
    use_real_runtime = (
        RuntimeNode is not None
        and node_runtime_module is not None
        and os.environ.get("PARAMIND_LIFECYCLE_USE_REAL_RUNTIME") == "1"
    )
    if use_real_runtime:
        os.environ.setdefault("PARAMIND_SIGSERVER", "ws://127.0.0.1:9999")

        class _DemoTokenizer:
            all_special_ids = []
            eos_token_id = None

            def apply_chat_template(
                self, messages, tokenize: bool = False, add_generation_prompt: bool = True
            ) -> str:
                _ = messages
                _ = tokenize
                _ = add_generation_prompt
                return ""

            def __call__(self, text, return_tensors: str = "pt"):
                raise RuntimeError("Tokenizer call is not expected in lifecycle demo")

            def decode(
                self,
                token_ids,
                skip_special_tokens: bool = True,
                clean_up_tokenization_spaces: bool = False,
            ) -> str:
                _ = token_ids
                _ = skip_special_tokens
                _ = clean_up_tokenization_spaces
                return ""

        node_runtime_module.P2PClient._queryStunInfo = staticmethod(
            lambda port: ("DemoNAT", "127.0.0.1", port)
        )
        node_runtime_module.getSignalServerAddr = lambda: os.environ[
            "PARAMIND_SIGSERVER"
        ]
        node_runtime_module.AutoTokenizer.from_pretrained = staticmethod(
            lambda *args, **kwargs: _DemoTokenizer()
        )
        runtime_class = RuntimeNode

    transport = DemoTransport()
    prepared_nodes: list[str] = []

    coordinator = ClusterCoordinator(
        transport=transport, model_id=MODEL_ID, total_layers=TOTAL_LAYERS
    )

    def _mark_prepared(node_id: str, reconfiguration_id: int | None = None):
        _ = reconfiguration_id
        prepared_nodes.append(node_id)
        transport.events.append(f"runtime_ready: node={node_id}")

    old_runtime = runtime_class(
        node_id="node-a",
        node_group="demo-group",
        family=ModelFamily.QWEN,
        total_layers=TOTAL_LAYERS,
        model_id=MODEL_ID,
        device="cpu",
        loader=DemoLoader("node-a"),
        on_ready=_mark_prepared,
    )
    new_runtime = runtime_class(
        node_id="node-c",
        node_group="demo-group",
        family=ModelFamily.QWEN,
        total_layers=TOTAL_LAYERS,
        model_id=MODEL_ID,
        device="cpu",
        loader=DemoLoader("node-c"),
        on_ready=_mark_prepared,
    )
    if use_real_runtime:
        old_runtime.p2p_client = DemoP2PStub()
        new_runtime.p2p_client = DemoP2PStub()
    old_runtime.attach_transport(transport)
    new_runtime.attach_transport(transport)

    initial_assignment = ShardAssignment(
        node_id="node-a",
        start_layer=0,
        end_layer=TOTAL_LAYERS,
        role="first",
    )
    old_runtime.apply_assignment(MODEL_ID, initial_assignment)
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
