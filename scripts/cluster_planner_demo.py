from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scheduler.ClusterPlanner import (
    choose_coordinator,
    coordinator_score,
    estimate_reference_stage_width,
    estimate_stage_time_for_node,
    plan_static_distribution,
    replan_distribution,
)
from scheduler.ClusterTypes import NodeProfile, PlacementPlan
from scheduler.NodeInventory import normalize_node_state
from scripts.demo_rendering import render_key_value_block, render_section, render_table

MODEL_ID = "Qwen/Qwen2.5-0.5B-Instruct"
TOTAL_LAYERS = 24
NODE_ORDER = ["node-a", "node-b", "node-c", "node-d"]
BASE_NODE_PROFILES = {
    "node-a": NodeProfile(
        node_id="node-a",
        host="10.0.0.1",
        device="cpu",
        total_memory_gb=32.0,
        free_memory_gb=28.0,
        compute_score=30.0,
        loaded_shards=[],
    ),
    "node-b": NodeProfile(
        node_id="node-b",
        host="10.0.0.2",
        device="cpu",
        total_memory_gb=32.0,
        free_memory_gb=27.0,
        compute_score=20.0,
        loaded_shards=[],
    ),
    "node-c": NodeProfile(
        node_id="node-c",
        host="10.0.0.3",
        device="cpu",
        total_memory_gb=32.0,
        free_memory_gb=26.0,
        compute_score=10.0,
        loaded_shards=[],
    ),
    "node-d": NodeProfile(
        node_id="node-d",
        host="10.0.0.4",
        device="cuda",
        total_memory_gb=64.0,
        free_memory_gb=40.0,
        compute_score=80.0,
        loaded_shards=[],
    ),
}


def format_parameter_guide() -> str:
    return "\n".join(
        [
            "=== PARAMETER GUIDE ===",
            "node_id: 节点唯一标识，用来表示 shard 当前属于谁。",
            "device: 节点设备类型；当前 demo 里主要区分 cpu / cuda。",
            "free_memory_gb: 当前可用内存，影响节点还能承载多少 blocks。",
            "compute_score: 旧兼容字段；会被转换为 NodeState 的速度输入。",
            "loaded_shards: 当前仍驻留在该节点上的层区间（仅在线；drop/leave 视为 unload，离线为空）。",
            "effective_speed: planner 内部使用的近似速度；当前 demo 里主要来自 compute_score。",
            "effective_capacity_blocks: 节点最多还能承载多少层块；当前 demo 里近似由 free_memory_gb 决定。",
            "reference_stage_width: 按 total_layers / active_nodes 估出来的参考 shard 宽度，用来统一比较节点。",
            "stage_time: 当前节点在 reference_stage_width 下的估计 stage 时间；越小越优。",
            "coordinator_score: coordinator 选择使用的共享 planner 分数；当前也是越小越优。",
            "placement role: first / middle / last，分别表示首段、中段、尾段 shard。",
            "source_node_id: replan 后该 shard 主要是从哪个旧 owner 迁移而来。",
            "commands: start node-a,node-b | join node-d | drop node-b | show | help | quit",
        ]
    )


def format_plan(plan: PlacementPlan) -> str:
    lines = [f"model={plan.model_id}", f"coordinator={plan.coordinator_id}"]
    for assignment in plan.assignments:
        suffix = ""
        if assignment.source_node_id is not None:
            suffix = f" <- {assignment.source_node_id}"
        lines.append(
            f"{assignment.node_id}: {assignment.start_layer}-{assignment.end_layer} ({assignment.role}){suffix}"
        )
    return "\n".join(lines)


class ClusterDemoSession:
    def __init__(self):
        self.node_profiles = {}
        self.active_node_ids: set[str] = set()
        self.current_plan: PlacementPlan | None = None
        self.event_log: list[str] = []
        self.last_action = "initialized"
        self._reset_to_base_state()

    def _reset_to_base_state(self) -> None:
        self.node_profiles = {
            node_id: NodeProfile.from_dict(profile.to_dict())
            for node_id, profile in BASE_NODE_PROFILES.items()
        }
        self.active_node_ids = set()
        self.current_plan = None
        self.event_log = []
        self.last_action = "reset"

    def _active_profiles(self) -> list[NodeProfile]:
        return [
            self.node_profiles[node_id]
            for node_id in NODE_ORDER
            if node_id in self.active_node_ids
        ]

    def _set_loaded_shards(
        self, node_id: str, new_ranges: list[tuple[int, int]]
    ) -> None:
        current = self.node_profiles[node_id]
        self.node_profiles[node_id] = replace(current, loaded_shards=sorted(new_ranges))

    def _refresh_loaded_state_from_plan(self, plan: PlacementPlan) -> None:
        # Online nodes: resident weights match the new plan. Offline: unload (no snapshot).
        ranges_by_node: dict[str, list[tuple[int, int]]] = {}
        for assignment in plan.assignments:
            ranges_by_node.setdefault(assignment.node_id, []).append(
                (assignment.start_layer, assignment.end_layer)
            )
        for node_id in self.node_profiles:
            if node_id in self.active_node_ids:
                self._set_loaded_shards(node_id, ranges_by_node.get(node_id, []))
            else:
                self._set_loaded_shards(node_id, [])

    def _format_node_states(self) -> str:
        if not self.active_node_ids:
            return "[NODE STATES]\n(no cluster yet)"
        lines = ["[NODE STATES]"]
        for node_id in NODE_ORDER:
            profile = self.node_profiles[node_id]
            state = normalize_node_state(profile)
            active = "yes" if node_id in self.active_node_ids else "no"
            lines.append(
                f"{state.node_id} | active={active} | device={state.device_type} | "
                f"free={state.free_memory_gb:.1f}GB | capacity={state.effective_capacity_blocks()} | "
                f"loaded={state.loaded_ranges}"
            )
        return "\n".join(lines)

    def _format_shard_states(self) -> str:
        if self.current_plan is None:
            return "[SHARD STATES]\n(no shard state yet)"
        rows = []
        for node_id in NODE_ORDER:
            profile = self.node_profiles[node_id]
            if not profile.loaded_shards:
                rows.append(
                    (
                        node_id,
                        "inactive" if node_id not in self.active_node_ids else "active",
                        "-",
                        "-",
                    )
                )
                continue
            for start_layer, end_layer in profile.loaded_shards:
                rows.append(
                    (
                        node_id,
                        "active" if node_id in self.active_node_ids else "inactive",
                        f"{start_layer}-{end_layer}",
                        "resident",
                    )
                )
        return render_table(
            "SHARD STATES",
            columns=["node", "cluster_state", "shard", "status"],
            rows=rows,
        )

    def _format_scoring(self) -> str:
        if not self.active_node_ids:
            return "[SCORING]\n(no active nodes yet)"
        active_profiles = self._active_profiles()
        reference_width = estimate_reference_stage_width(
            TOTAL_LAYERS, len(active_profiles)
        )
        lines = ["[SCORING]"]
        for profile in active_profiles:
            state = normalize_node_state(profile)
            speed = state.effective_speed()
            capacity = state.effective_capacity_blocks()
            stage_time = estimate_stage_time_for_node(
                state,
                width=reference_width,
                total_layers=TOTAL_LAYERS,
                role="middle",
            )
            score = coordinator_score(
                state,
                total_layers=TOTAL_LAYERS,
                active_nodes=len(active_profiles),
                current_plan=self.current_plan,
            )
            lines.append(
                f"{state.node_id} | effective_speed={speed:.1f} | "
                f"effective_capacity_blocks={capacity} | reference_stage_width={reference_width} | "
                f"stage_time={stage_time:.3f} | coordinator_score={score:.3f}"
            )
        lines.append(
            f"coordinator = min(coordinator_score) = "
            f"{choose_coordinator(active_profiles, total_layers=TOTAL_LAYERS, current_plan=self.current_plan)}"
        )
        return "\n".join(lines)

    def _format_placement(self) -> str:
        if self.current_plan is None:
            return "[PLACEMENT]\n(no placement yet)"
        return "\n".join(["[PLACEMENT]", format_plan(self.current_plan)])

    def _format_current_cluster(self) -> str:
        return render_key_value_block(
            "CURRENT CLUSTER",
            {
                "model": MODEL_ID,
                "active_nodes": (
                    ", ".join(sorted(self.active_node_ids))
                    if self.active_node_ids
                    else "(none)"
                ),
                "coordinator": (
                    self.current_plan.coordinator_id
                    if self.current_plan is not None
                    else "(none)"
                ),
                "last_action": self.last_action,
            },
        )

    def _format_actions(self) -> str:
        if self.current_plan is None:
            return "[ACTIONS]\n(no actions yet)"
        rows = []
        for assignment in self.current_plan.assignments:
            rows.append(
                (
                    assignment.node_id,
                    f"{assignment.start_layer}-{assignment.end_layer}",
                    assignment.role,
                    assignment.source_node_id or "-",
                )
            )
        return render_table(
            "ACTIONS",
            columns=["node", "shard", "role", "source_node_id"],
            rows=rows,
        )

    def _format_event_log(self) -> str:
        body = "\n".join(self.event_log[-6:]) if self.event_log else "(no events yet)"
        return render_section("EVENT LOG", body)

    def render_current(
        self, include_guide: bool = False, title: str | None = None
    ) -> str:
        sections = []
        if include_guide:
            sections.append(format_parameter_guide())
            sections.append("")
        if title:
            sections.append(title)
        sections.extend(
            [
                self._format_current_cluster(),
                self._format_node_states(),
                self._format_actions(),
                self._format_event_log(),
                self._format_shard_states(),
                self._format_scoring(),
            ]
        )
        if self.current_plan is not None:
            sections.append(self._format_placement())
        return "\n".join(sections)

    def start(self, node_ids: list[str]) -> str:
        if not node_ids:
            raise ValueError("start requires at least one node id")
        unknown = [node_id for node_id in node_ids if node_id not in self.node_profiles]
        if unknown:
            raise ValueError(f"unknown nodes: {', '.join(unknown)}")
        self._reset_to_base_state()
        self.active_node_ids = set(node_ids)
        self.current_plan = plan_static_distribution(
            model_id=MODEL_ID,
            total_layers=TOTAL_LAYERS,
            nodes=self._active_profiles(),
        )
        self._refresh_loaded_state_from_plan(self.current_plan)
        self.last_action = f"start {','.join(node_ids)}"
        self.event_log.append(
            f"start: active_nodes={','.join(sorted(self.active_node_ids))}"
        )
        return self.render_current(title="=== AFTER START ===")

    def join(self, node_id: str) -> str:
        if self.current_plan is None:
            raise ValueError("start the cluster before joining nodes")
        if node_id not in self.node_profiles:
            raise ValueError(f"unknown node: {node_id}")
        if node_id in self.active_node_ids:
            raise ValueError(f"node already active: {node_id}")
        self.active_node_ids.add(node_id)
        self.current_plan = replan_distribution(
            current=self.current_plan,
            nodes=self._active_profiles(),
            total_layers=TOTAL_LAYERS,
        )
        self._refresh_loaded_state_from_plan(self.current_plan)
        self.last_action = f"join {node_id}"
        self.event_log.append(f"join: node={node_id}")
        return self.render_current(title=f"=== AFTER JOIN {node_id} ===")

    def drop(self, node_id: str) -> str:
        if node_id not in self.active_node_ids:
            raise ValueError(f"node not active: {node_id}")
        self.active_node_ids.remove(node_id)
        if not self.active_node_ids:
            self.current_plan = None
            for nid in self.node_profiles:
                self._set_loaded_shards(nid, [])
            self.last_action = f"drop {node_id}"
            self.event_log.append(f"drop: node={node_id}")
            return self.render_current(title=f"=== AFTER DROP {node_id} ===")
        if self.current_plan is None:
            raise ValueError("no current plan to replan from")
        self.current_plan = replan_distribution(
            current=self.current_plan,
            nodes=self._active_profiles(),
            total_layers=TOTAL_LAYERS,
        )
        self._refresh_loaded_state_from_plan(self.current_plan)
        self.last_action = f"drop {node_id}"
        self.event_log.append(f"drop: node={node_id}")
        return self.render_current(title=f"=== AFTER DROP {node_id} ===")

    def process_command(self, command: str) -> tuple[str, bool]:
        raw = command.strip()
        if not raw:
            return "Empty command. Use: start/join/drop/show/help/quit", True
        lowered = raw.lower()
        try:
            if lowered == "help":
                return format_parameter_guide(), True
            if lowered == "show":
                return self.render_current(title="=== CURRENT STATE ==="), True
            if lowered == "quit":
                return "Bye.", False
            if lowered.startswith("start "):
                node_ids = [item.strip() for item in raw[6:].split(",") if item.strip()]
                return self.start(node_ids), True
            if lowered.startswith("join "):
                return self.join(raw[5:].strip()), True
            if lowered.startswith("drop "):
                return self.drop(raw[5:].strip()), True
            return "Unknown command. Use: start/join/drop/show/help/quit", True
        except ValueError as exc:
            return f"Error: {exc}", True


def main() -> None:
    session = ClusterDemoSession()
    print(session.render_current(include_guide=True))
    while True:
        try:
            command = input("\ncommand> ")
        except EOFError:
            print("\nBye.")
            break
        try:
            output, keep_running = session.process_command(command)
        except Exception as exc:  # pragma: no cover - interactive path
            print(f"Error: {exc}")
            continue
        print(output)
        if not keep_running:
            break


def run_scripted_demo(commands: list[str], include_guide: bool = True) -> str:
    session = ClusterDemoSession()
    outputs = [session.render_current(include_guide=include_guide)]
    for command in commands:
        output, keep_running = session.process_command(command)
        outputs.append(f"command> {command}\n{output}")
        if not keep_running:
            break
    return "\n\n".join(outputs)


if __name__ == "__main__":
    main()
