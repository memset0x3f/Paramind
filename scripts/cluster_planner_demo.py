from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scheduler.ClusterPlanner import (
    _load_model_profile,
    plan_static_distribution,
    range_bytes,
    replan_distribution,
)
from scheduler.ClusterTypes import NodeProfile, PlacementPlan
from scheduler.NodeInventory import normalize_node_state
from scripts.demo_rendering import render_table

MODEL_ID = "demo/TenLayerToy"
TOTAL_LAYERS = 10
NODE_ORDER = ["a", "b", "c", "d"]

# Worked example: quality-first cold start 3+4+3; join peels tail from weakest stage;
# drop repairs the gap locally without a global reshuffle.
DEMO_SCENARIO_HINT = "Example: start a,b,c → [0,3)|[3,7)|[7,10) | join d → d:[7,9), c:[9,10) | drop d → c:[7,10) again"


def _budget(start: int, end: int) -> int:
    return range_bytes(MODEL_ID, start, end, TOTAL_LAYERS)


def _format_shard_half_open(start_layer: int, end_layer: int) -> str:
    """Half-open layer index span [start, end), matching planner storage."""
    return f"[{start_layer}, {end_layer})"


SHARD_SPAN_LEGEND = (
    "Shards: layer span [start, end) — left-closed, right-open (start <= i < end)."
)


def _model_header_sizes_mib() -> tuple[float, float, float]:
    """Head/tail are full stage blocks from the profile; middle is one layer only."""
    profile = _load_model_profile(MODEL_ID, TOTAL_LAYERS)
    sections = profile["sections"]
    to_mib = lambda n: float(n) / (1024**2)
    head_mib = to_mib(int(sections["first"]["total_bytes"]))
    mid_layer_keys = sorted(sections["middle"]["layer_bytes"], key=lambda k: int(k))
    one_middle_mib = to_mib(int(sections["middle"]["layer_bytes"][mid_layer_keys[0]]))
    tail_mib = to_mib(int(sections["last"]["total_bytes"]))
    return head_mib, one_middle_mib, tail_mib


BASE_NODE_PROFILES = {
    "a": NodeProfile(
        node_id="a",
        host="10.0.0.1",
        device="cpu",
        total_memory_gb=1.0,
        compute_score=50.0,
        loaded_shards=[],
        max_usable_bytes=_budget(0, 3),
    ),
    "b": NodeProfile(
        node_id="b",
        host="10.0.0.2",
        device="cpu",
        total_memory_gb=1.0,
        compute_score=28.0,
        loaded_shards=[],
        max_usable_bytes=_budget(3, 7),
    ),
    "c": NodeProfile(
        node_id="c",
        host="10.0.0.3",
        device="cpu",
        total_memory_gb=1.0,
        compute_score=12.0,
        loaded_shards=[],
        max_usable_bytes=_budget(7, 10),
    ),
    "d": NodeProfile(
        node_id="d",
        host="10.0.0.4",
        device="cuda",
        total_memory_gb=1.0,
        compute_score=40.0,
        loaded_shards=[],
        max_usable_bytes=_budget(7, 9),
    ),
}


def format_parameter_guide() -> str:
    return "\n".join(
        [
            "=== HELP ===",
            DEMO_SCENARIO_HINT,
            "Columns: on=y/n online | spd=quality (speed) | cap_mb=byte budget | "
            "shard=[lo, hi) layer span | src=migration source",
            "Commands: start a,b,c | join d | drop d | show | help | quit",
        ]
    )


class ClusterDemoSession:
    def __init__(self):
        self.node_profiles = {}
        self.active_node_ids: set[str] = set()
        self.current_plan: PlacementPlan | None = None
        self.planning_error: str | None = None
        self.event_log: list[str] = []
        self._reset_to_base_state()
        self.last_action = "initialized"

    def _reset_to_base_state(self) -> None:
        self.node_profiles = {
            node_id: NodeProfile.from_dict(profile.to_dict())
            for node_id, profile in BASE_NODE_PROFILES.items()
        }
        self.active_node_ids = set()
        self.current_plan = None
        self.planning_error = None
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

    def _model_header(self) -> str:
        head_mib, one_middle_mib, tail_mib = _model_header_sizes_mib()
        header = (
            f"model: {MODEL_ID} | layers: {TOTAL_LAYERS} | "
            f"head: {head_mib:.1f} MiB | middle (1 layer): {one_middle_mib:.1f} MiB | "
            f"tail: {tail_mib:.1f} MiB\n"
            f"last: {self.last_action}"
        )
        if self.planning_error:
            header += f"\nplan_status: pending ({self.planning_error})"
        return header

    def _clear_all_loaded_shards(self) -> None:
        for node_id in self.node_profiles:
            self._set_loaded_shards(node_id, [])

    def _format_cluster_table(self) -> str:
        plan_by_node: dict[str, tuple[str, str]] = {}
        if self.current_plan is not None:
            for a in self.current_plan.assignments:
                shard = _format_shard_half_open(a.start_layer, a.end_layer)
                src = a.source_node_id if a.source_node_id else "-"
                plan_by_node[a.node_id] = (shard, src)

        rows: list[tuple[object, ...]] = []
        for node_id in NODE_ORDER:
            profile = self.node_profiles[node_id]
            state = normalize_node_state(profile)
            on = "y" if node_id in self.active_node_ids else "n"
            cap_mb = state.effective_usable_bytes() / (1024**2)
            if node_id in plan_by_node:
                shard, src = plan_by_node[node_id]
            else:
                shard, src = "-", "-"
            rows.append(
                (
                    node_id,
                    on,
                    f"{state.effective_speed():.0f}",
                    f"{cap_mb:.1f}",
                    shard,
                    src,
                )
            )

        return (
            SHARD_SPAN_LEGEND
            + "\n"
            + render_table(
                "CLUSTER",
                columns=["node", "on", "spd", "cap_mb", "shard", "src"],
                rows=rows,
            )
        )

    def render_current(
        self, include_guide: bool = False, title: str | None = None
    ) -> str:
        sections: list[str] = []
        if include_guide:
            sections.extend([format_parameter_guide(), ""])
        if title:
            sections.append(title)
        sections.append(self._model_header())
        sections.append(self._format_cluster_table())
        return "\n".join(sections)

    def start(self, node_ids: list[str]) -> str:
        if not node_ids:
            raise ValueError("start requires at least one node id")
        unknown = [node_id for node_id in node_ids if node_id not in self.node_profiles]
        if unknown:
            raise ValueError(f"unknown nodes: {', '.join(unknown)}")
        self._reset_to_base_state()
        self.active_node_ids = set(node_ids)
        try:
            self.current_plan = plan_static_distribution(
                model_id=MODEL_ID,
                total_layers=TOTAL_LAYERS,
                nodes=self._active_profiles(),
            )
            self.planning_error = None
            self._refresh_loaded_state_from_plan(self.current_plan)
            self.last_action = f"start {','.join(node_ids)}"
        except ValueError as exc:
            self.current_plan = None
            self.planning_error = str(exc)
            self._clear_all_loaded_shards()
            self.last_action = f"start {','.join(node_ids)} (pending)"
        self.event_log.append(
            f"start: active_nodes={','.join(sorted(self.active_node_ids))}"
        )
        return self.render_current(title="=== AFTER START ===")

    def join(self, node_id: str) -> str:
        if not self.active_node_ids:
            raise ValueError("start the cluster before joining nodes")
        if node_id not in self.node_profiles:
            raise ValueError(f"unknown node: {node_id}")
        if node_id in self.active_node_ids:
            raise ValueError(f"node already active: {node_id}")
        previous_node_ids = set(self.active_node_ids)
        next_active_node_ids = set(self.active_node_ids)
        next_active_node_ids.add(node_id)
        next_nodes = [
            self.node_profiles[candidate]
            for candidate in NODE_ORDER
            if candidate in next_active_node_ids
        ]
        try:
            if self.current_plan is None:
                self.current_plan = plan_static_distribution(
                    model_id=MODEL_ID,
                    total_layers=TOTAL_LAYERS,
                    nodes=next_nodes,
                )
            else:
                self.current_plan = replan_distribution(
                    current=self.current_plan,
                    nodes=next_nodes,
                    total_layers=TOTAL_LAYERS,
                    previous_node_ids=previous_node_ids,
                )
            self.active_node_ids = next_active_node_ids
            self.planning_error = None
            self._refresh_loaded_state_from_plan(self.current_plan)
            self.last_action = f"join {node_id}"
        except ValueError as exc:
            if self.current_plan is None:
                # No serving plan yet: keep onboarding nodes and wait for enough capacity.
                self.active_node_ids = next_active_node_ids
                self.planning_error = str(exc)
                self._clear_all_loaded_shards()
                self.last_action = f"join {node_id} (pending)"
            else:
                raise
        self.event_log.append(f"join: node={node_id}")
        return self.render_current(title=f"=== AFTER JOIN {node_id} ===")

    def drop(self, node_id: str) -> str:
        if node_id not in self.active_node_ids:
            raise ValueError(f"node not active: {node_id}")
        previous_node_ids = set(self.active_node_ids)
        next_active_node_ids = set(self.active_node_ids)
        next_active_node_ids.remove(node_id)
        if not next_active_node_ids:
            self.current_plan = None
            self.planning_error = None
            self._clear_all_loaded_shards()
            self.active_node_ids = next_active_node_ids
            self.last_action = f"drop {node_id}"
            self.event_log.append(f"drop: node={node_id}")
            return self.render_current(title=f"=== AFTER DROP {node_id} ===")
        if self.current_plan is None:
            self.active_node_ids = next_active_node_ids
            self.planning_error = "No serving plan yet; waiting for enough capacity."
            self._clear_all_loaded_shards()
            self.last_action = f"drop {node_id} (pending)"
            self.event_log.append(f"drop: node={node_id}")
            return self.render_current(title=f"=== AFTER DROP {node_id} ===")
        self.current_plan = replan_distribution(
            current=self.current_plan,
            nodes=[
                self.node_profiles[candidate]
                for candidate in NODE_ORDER
                if candidate in next_active_node_ids
            ],
            total_layers=TOTAL_LAYERS,
            previous_node_ids=previous_node_ids,
        )
        self.active_node_ids = next_active_node_ids
        self.planning_error = None
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
