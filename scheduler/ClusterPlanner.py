from __future__ import annotations

import json
import math
from pathlib import Path

from .ClusterTypes import (
    NodeReconfigurationAction,
    PlacementPlan,
    ShardAssignment,
)
from .NodeInventory import NodeState, normalize_node_state

_PROFILE_DIR = Path(__file__).resolve().parent.parent / "models" / "profiles"
DEFAULT_MODEL_ID = "Qwen/Qwen2.5-0.5B-Instruct"


def _profile_filename(model_id: str) -> Path:
    safe_name = model_id.replace("/", "__")
    return _PROFILE_DIR / f"{safe_name}.json"


def _load_model_profile(model_id: str, total_layers: int) -> dict:
    path = _profile_filename(model_id)
    if not path.exists():
        raise FileNotFoundError(
            f"Missing layer-bytes profile for {model_id}. Expected {path}. "
            "Generate it with models.ensure_model_profile(model_id) first."
        )
    payload = json.loads(path.read_text())
    layer_bytes = payload.get("layer_bytes", {})
    if len(layer_bytes) != total_layers:
        raise ValueError(
            f"Layer-bytes profile for {model_id} has {len(layer_bytes)} layers, "
            f"but planner requested {total_layers}."
        )
    return payload


def range_bytes(model_id: str, start: int, end: int, total_layers: int) -> int:
    if start < 0 or end < start or end > total_layers:
        raise ValueError(
            f"Invalid layer range [{start}, {end}) for total_layers={total_layers}"
        )
    profile = _load_model_profile(model_id, total_layers)
    layer_bytes = profile["layer_bytes"]
    special_components = profile.get("special_components", {})
    total = sum(layer_bytes[str(index)] for index in range(start, end))
    if start == 0:
        total += int(special_components.get("embeddings", 0))
    if end == total_layers:
        total += int(special_components.get("final_norm", 0))
        total += int(special_components.get("lm_head", 0))
        total += sum(int(v) for k, v in profile.get("other_components", {}).items())
    return total


def _total_model_bytes(model_id: str, total_layers: int) -> int:
    profile = _load_model_profile(model_id, total_layers)
    return int(profile["total_bytes"])


def _layer_work(
    total_layers: int, layer_work: list[float] | None = None
) -> list[float]:
    if layer_work is None:
        return [1.0] * total_layers
    if len(layer_work) != total_layers:
        raise ValueError("layer_work length must equal total_layers")
    return layer_work


def _sort_nodes_by_quality_desc(nodes: list[NodeState]) -> list[NodeState]:
    """Higher compute speed and larger capacity rank first (cold-start greedy order)."""
    return sorted(
        nodes,
        key=lambda node: (
            -node.effective_speed(),
            -node.effective_usable_bytes(),
            node.node_id,
        ),
    )


def _node_quality_key(node: NodeState) -> tuple[float, int, str]:
    return (
        node.effective_speed(),
        node.effective_usable_bytes(),
        node.node_id,
    )


def _build_assignments(
    model_id: str,
    ordered_nodes: list[NodeState],
    boundaries: list[int],
) -> PlacementPlan:
    assignments: list[ShardAssignment] = []
    non_empty_segments = [
        (node, boundaries[index], boundaries[index + 1])
        for index, node in enumerate(ordered_nodes)
        if boundaries[index + 1] > boundaries[index]
    ]

    for segment_index, (node, start, end) in enumerate(non_empty_segments):
        role = "middle"
        if segment_index == 0:
            role = "first"
        if segment_index == len(non_empty_segments) - 1:
            role = "last" if role == "middle" else role
        assignments.append(
            ShardAssignment(
                node_id=node.node_id,
                start_layer=start,
                end_layer=end,
                role=role,
            )
        )

    return PlacementPlan(
        model_id=model_id,
        assignments=assignments,
        coordinator_id=None,
    )


def _solve_cold_start_greedy_by_quality(
    model_id: str,
    total_layers: int,
    ordered_nodes: list[NodeState],
    layer_work: list[float],
) -> PlacementPlan | None:
    """Fill highest-quality nodes to capacity first, then the next, until all layers are placed.

    Uses only a prefix of ``ordered_nodes`` (quality-descending). Returns ``None`` if
    aggregate capacity is insufficient (caller should raise).
    """
    _ = layer_work  # cold-start greedy uses uniform layer slots; work weights are for replan cost only
    cursor = 0
    used_nodes: list[NodeState] = []
    boundaries: list[int] = [0]

    for node in ordered_nodes:
        if cursor >= total_layers:
            break
        usable_bytes = node.effective_usable_bytes()
        if usable_bytes <= 0:
            continue
        end = cursor
        while (
            end < total_layers
            and range_bytes(model_id, cursor, end + 1, total_layers) <= usable_bytes
        ):
            end += 1
        if end <= cursor:
            continue
        used_nodes.append(node)
        boundaries.append(end)
        cursor = end

    if cursor < total_layers or not used_nodes:
        return None
    if boundaries[-1] != total_layers:
        return None
    return _build_assignments(model_id, used_nodes, boundaries)


def _annotate_plan_sources_from_current(
    base_plan: PlacementPlan,
    current: PlacementPlan,
    ordered_nodes: list[NodeState],
    total_layers: int,
) -> PlacementPlan:
    owner_by_layer = _owner_by_layer(current, total_layers)
    assignments: list[ShardAssignment] = []
    for assignment in base_plan.assignments:
        dominant_owner = _dominant_owner(
            owner_by_layer, assignment.start_layer, assignment.end_layer
        )
        source_node_id = (
            None if dominant_owner == assignment.node_id else dominant_owner
        )
        assignments.append(
            ShardAssignment(
                node_id=assignment.node_id,
                start_layer=assignment.start_layer,
                end_layer=assignment.end_layer,
                role=assignment.role,
                source_node_id=source_node_id,
            )
        )
    return PlacementPlan(
        model_id=current.model_id,
        assignments=assignments,
        coordinator_id=None,
    )


def plan_static_distribution(
    model_id: str,
    total_layers: int,
    nodes,
    layer_work: list[float] | None = None,
) -> PlacementPlan:
    normalized_nodes = [normalize_node_state(node) for node in nodes]
    active_nodes = [
        node
        for node in normalized_nodes
        if node.online and node.effective_usable_bytes() > 0
    ]
    if not active_nodes:
        raise ValueError(
            "At least one online node with positive capacity is required for planning"
        )

    layer_work = _layer_work(total_layers, layer_work)
    total_capacity_bytes = sum(node.effective_usable_bytes() for node in active_nodes)
    total_model_bytes = _total_model_bytes(model_id, total_layers)
    if total_capacity_bytes < total_model_bytes:
        raise ValueError("Insufficient cluster capacity bytes for total model")

    ordered_nodes = _sort_nodes_by_quality_desc(active_nodes)
    plan = _solve_cold_start_greedy_by_quality(
        model_id, total_layers, ordered_nodes, layer_work
    )
    if plan is None:
        raise ValueError(
            "No feasible contiguous placement: model does not fit even using every node"
        )
    return plan


def _normalize_active_nodes(nodes) -> list[NodeState]:
    normalized_nodes = [normalize_node_state(node) for node in nodes]
    active_nodes = [
        node
        for node in normalized_nodes
        if node.online and node.effective_usable_bytes() > 0
    ]
    if not active_nodes:
        raise ValueError(
            "At least one online node with positive capacity is required for planning"
        )
    return active_nodes


def _assignment_can_stay(
    assignment: ShardAssignment,
    node_by_id: dict[str, NodeState],
    model_id: str,
    total_layers: int,
) -> bool:
    node = node_by_id.get(assignment.node_id)
    if node is None or not node.online:
        return False
    shard_bytes = range_bytes(
        model_id, assignment.start_layer, assignment.end_layer, total_layers
    )
    if shard_bytes > node.effective_usable_bytes():
        return False
    return True


def _owner_by_layer(current: PlacementPlan, total_layers: int) -> list[str | None]:
    owners: list[str | None] = [None] * total_layers
    for assignment in current.assignments:
        for layer in range(assignment.start_layer, assignment.end_layer):
            owners[layer] = assignment.node_id
    return owners


def _dominant_owner(
    owner_by_layer: list[str | None], start: int, end: int
) -> str | None:
    counts: dict[str, int] = {}
    for owner in owner_by_layer[start:end]:
        if owner is None:
            continue
        counts[owner] = counts.get(owner, 0) + 1
    if not counts:
        return None
    return max(counts.items(), key=lambda item: item[1])[0]


def _assignment_key(assignment: ShardAssignment) -> tuple[int, int]:
    return (assignment.start_layer, assignment.end_layer)


def diff_assignment_changes(
    current: PlacementPlan,
    new: PlacementPlan,
) -> dict[str, list[NodeReconfigurationAction]]:
    current_by_key = {
        _assignment_key(assignment): assignment for assignment in current.assignments
    }
    new_by_key = {
        _assignment_key(assignment): assignment for assignment in new.assignments
    }
    actions: dict[str, list[NodeReconfigurationAction]] = {}

    for key, assignment in new_by_key.items():
        old = current_by_key.get(key)
        if old is not None and old.node_id == assignment.node_id:
            actions.setdefault(assignment.node_id, []).append(
                NodeReconfigurationAction(
                    node_id=assignment.node_id,
                    action="keep",
                    start_layer=assignment.start_layer,
                    end_layer=assignment.end_layer,
                    role=assignment.role,
                )
            )
            continue

        incoming_action = "move_in" if assignment.source_node_id is not None else "load"
        actions.setdefault(assignment.node_id, []).append(
            NodeReconfigurationAction(
                node_id=assignment.node_id,
                action=incoming_action,
                start_layer=assignment.start_layer,
                end_layer=assignment.end_layer,
                role=assignment.role,
                from_node_id=assignment.source_node_id,
            )
        )

    for key, assignment in current_by_key.items():
        new_assignment = new_by_key.get(key)
        if new_assignment is None or new_assignment.node_id != assignment.node_id:
            actions.setdefault(assignment.node_id, []).append(
                NodeReconfigurationAction(
                    node_id=assignment.node_id,
                    action="unload",
                    start_layer=assignment.start_layer,
                    end_layer=assignment.end_layer,
                    role=assignment.role,
                )
            )

    for node_id, node_actions in actions.items():
        node_actions.sort(
            key=lambda item: (item.start_layer, item.end_layer, item.action)
        )
    return actions


def _assignment_bytes(
    model_id: str, assignment: ShardAssignment, total_layers: int
) -> int:
    return range_bytes(
        model_id, assignment.start_layer, assignment.end_layer, total_layers
    )


def _expandable_right_layers(
    node: NodeState,
    current_start: int,
    current_end: int,
    gap_end: int,
    model_id: str,
    total_layers: int,
) -> int:
    used = range_bytes(model_id, current_start, current_end, total_layers)
    usable = node.effective_usable_bytes()
    end = current_end
    while end < gap_end:
        next_bytes = range_bytes(model_id, current_start, end + 1, total_layers)
        if next_bytes > usable:
            break
        used = next_bytes
        end += 1
    return end - current_end


def _expandable_left_layers(
    node: NodeState,
    gap_start: int,
    current_start: int,
    current_end: int,
    model_id: str,
    total_layers: int,
) -> int:
    used = range_bytes(model_id, current_start, current_end, total_layers)
    usable = node.effective_usable_bytes()
    start = current_start
    while start > gap_start:
        next_bytes = range_bytes(model_id, start - 1, current_end, total_layers)
        if next_bytes > usable:
            break
        used = next_bytes
        start -= 1
    return current_start - start


def _max_range_end_for_node(
    model_id: str,
    node: NodeState,
    start: int,
    total_layers: int,
    max_end: int | None = None,
) -> int:
    usable = node.effective_usable_bytes()
    limit = total_layers if max_end is None else min(max_end, total_layers)
    end = start
    while end < limit and range_bytes(model_id, start, end + 1, total_layers) <= usable:
        end += 1
    return end


def _min_range_start_for_node(
    model_id: str,
    node: NodeState,
    end: int,
    min_start: int,
    total_layers: int,
) -> int:
    usable = node.effective_usable_bytes()
    start = end
    while (
        start > min_start
        and range_bytes(model_id, start - 1, end, total_layers) <= usable
    ):
        start -= 1
    return start


def plan_membership_stable_distribution(
    current: PlacementPlan,
    nodes,
    total_layers: int,
    layer_work: list[float] | None = None,
) -> PlacementPlan:
    active_nodes = _normalize_active_nodes(nodes)
    node_by_id = {node.node_id: node for node in active_nodes}
    current_node_ids = {assignment.node_id for assignment in current.assignments}
    active_node_ids = set(node_by_id.keys())
    if (
        current.assignments
        and all(
            _assignment_can_stay(assignment, node_by_id, current.model_id, total_layers)
            for assignment in current.assignments
        )
        and current_node_ids.issubset(active_node_ids)
    ):
        return current

    total_capacity_bytes = sum(node.effective_usable_bytes() for node in active_nodes)
    total_model_bytes = _total_model_bytes(current.model_id, total_layers)
    if total_capacity_bytes < total_model_bytes:
        raise ValueError("Insufficient cluster capacity bytes for total model")
    ordered_nodes = _sort_nodes_by_quality_desc(active_nodes)
    base_plan = _solve_cold_start_greedy_by_quality(
        current.model_id,
        total_layers,
        ordered_nodes,
        _layer_work(total_layers, layer_work),
    )
    if base_plan is None:
        raise ValueError("No feasible replan found for the current nodes")
    return _annotate_plan_sources_from_current(
        base_plan=base_plan,
        current=current,
        ordered_nodes=ordered_nodes,
        total_layers=total_layers,
    )


def plan_join_distribution(
    current: PlacementPlan,
    nodes,
    total_layers: int,
    layer_work: list[float] | None = None,
) -> PlacementPlan:
    """Insert stronger joiners by absorbing the weakest local cluster contiguously."""
    active_nodes = _normalize_active_nodes(nodes)
    node_by_id = {node.node_id: node for node in active_nodes}
    active_node_ids = set(node_by_id.keys())
    current_node_ids = {assignment.node_id for assignment in current.assignments}

    if not current_node_ids.issubset(active_node_ids):
        return plan_membership_stable_distribution(
            current=current,
            nodes=nodes,
            total_layers=total_layers,
            layer_work=layer_work,
        )

    standby_nodes = [
        node
        for node in _sort_nodes_by_quality_desc(active_nodes)
        if node.node_id not in current_node_ids
    ]
    if not standby_nodes:
        return current

    working = current

    for standby in standby_nodes:
        ordered = sorted(working.assignments, key=lambda item: item.start_layer)
        weakest_index = min(
            range(len(ordered)),
            key=lambda index: _node_quality_key(node_by_id[ordered[index].node_id]),
        )
        weakest_node = node_by_id[ordered[weakest_index].node_id]
        if _node_quality_key(standby) <= _node_quality_key(weakest_node):
            continue

        usable_bytes = standby.effective_usable_bytes()
        if usable_bytes <= 0:
            continue

        def _neighbor_side(left_index: int, right_index: int) -> str | None:
            left_quality = (
                _node_quality_key(node_by_id[ordered[left_index].node_id])
                if left_index >= 0
                else None
            )
            right_quality = (
                _node_quality_key(node_by_id[ordered[right_index].node_id])
                if right_index < len(ordered)
                else None
            )
            if left_quality is None and right_quality is None:
                return None
            if left_quality is None:
                return "right"
            if right_quality is None:
                return "left"
            return "left" if left_quality <= right_quality else "right"

        center = ordered[weakest_index]
        center_bytes = _assignment_bytes(current.model_id, center, total_layers)
        preferred_side = _neighbor_side(weakest_index - 1, weakest_index + 1)

        if center_bytes <= usable_bytes:
            new_start = center.start_layer
            new_end = center.end_layer
        elif preferred_side == "left":
            new_start = center.start_layer
            new_end = _max_range_end_for_node(
                current.model_id,
                standby,
                center.start_layer,
                total_layers,
                max_end=center.end_layer,
            )
        else:
            new_end = center.end_layer
            new_start = _min_range_start_for_node(
                current.model_id,
                standby,
                center.end_layer,
                center.start_layer,
                total_layers,
            )

        if new_end <= new_start:
            continue

        left_bound = weakest_index
        right_bound = weakest_index

        while True:
            candidate_side = _neighbor_side(left_bound - 1, right_bound + 1)
            if candidate_side is None:
                break
            if candidate_side == "left":
                candidate = ordered[left_bound - 1]
                extended_start = _min_range_start_for_node(
                    current.model_id,
                    standby,
                    new_end,
                    candidate.start_layer,
                    total_layers,
                )
                if extended_start == new_start:
                    break
                if extended_start <= candidate.start_layer:
                    new_start = candidate.start_layer
                    left_bound -= 1
                else:
                    new_start = extended_start
                    break
            else:
                candidate = ordered[right_bound + 1]
                extended_end = _max_range_end_for_node(
                    current.model_id,
                    standby,
                    new_start,
                    total_layers,
                    max_end=candidate.end_layer,
                )
                if extended_end == new_end:
                    break
                if extended_end >= candidate.end_layer:
                    new_end = candidate.end_layer
                    right_bound += 1
                else:
                    new_end = extended_end
                    break

        segments: list[tuple[str, int, int]] = []
        inserted = False
        for assignment in ordered:
            if assignment.end_layer <= new_start:
                segments.append(
                    (assignment.node_id, assignment.start_layer, assignment.end_layer)
                )
                continue
            if assignment.start_layer >= new_end:
                if not inserted:
                    segments.append((standby.node_id, new_start, new_end))
                    inserted = True
                segments.append(
                    (assignment.node_id, assignment.start_layer, assignment.end_layer)
                )
                continue

            if assignment.start_layer < new_start:
                segments.append((assignment.node_id, assignment.start_layer, new_start))
            if not inserted:
                segments.append((standby.node_id, new_start, new_end))
                inserted = True
            if assignment.end_layer > new_end:
                segments.append((assignment.node_id, new_end, assignment.end_layer))

        if not inserted:
            segments.append((standby.node_id, new_start, new_end))

        assignments: list[ShardAssignment] = []
        ordered_participants: list[NodeState] = []
        seen_nodes: set[str] = set()
        for index, (node_id, start, end) in enumerate(segments):
            if end <= start:
                continue
            role = "middle"
            if index == 0:
                role = "first"
            if index == len(segments) - 1:
                role = "last" if role == "middle" else role
            assignments.append(
                ShardAssignment(
                    node_id=node_id,
                    start_layer=start,
                    end_layer=end,
                    role=role,
                )
            )
            if node_id not in seen_nodes:
                ordered_participants.append(node_by_id[node_id])
                seen_nodes.add(node_id)

        base_plan = PlacementPlan(model_id=current.model_id, assignments=assignments)
        working = _annotate_plan_sources_from_current(
            base_plan=base_plan,
            current=working,
            ordered_nodes=ordered_participants,
            total_layers=total_layers,
        )

    return working


def plan_drop_distribution(
    current: PlacementPlan,
    nodes,
    total_layers: int,
    layer_work: list[float] | None = None,
) -> PlacementPlan:
    """Repair drop events by filling local gaps first, then activating standby capacity.

    The current plan remains authoritative for route order. When an assigned node
    disappears, the planner first lets nearby surviving nodes absorb as much of the
    missing contiguous range as their spare capacity allows. Only the remaining gap
    is handed to standby nodes, which are inserted at the gap location rather than
    appended to the route tail. This preserves monotonic routing, avoids crossing
    assignments, and minimizes new network jumps.
    """
    active_nodes = _normalize_active_nodes(nodes)
    node_by_id = {node.node_id: node for node in active_nodes}
    active_node_ids = set(node_by_id.keys())
    current_node_ids = {assignment.node_id for assignment in current.assignments}

    if current_node_ids.issubset(active_node_ids) and all(
        _assignment_can_stay(assignment, node_by_id, current.model_id, total_layers)
        for assignment in current.assignments
    ):
        return current

    layer_work = _layer_work(total_layers, layer_work)
    total_capacity_bytes = sum(node.effective_usable_bytes() for node in active_nodes)
    total_model_bytes = _total_model_bytes(current.model_id, total_layers)
    if total_capacity_bytes < total_model_bytes:
        raise ValueError("Insufficient cluster capacity bytes for total model")

    ordered_current = sorted(current.assignments, key=lambda item: item.start_layer)
    surviving_segments: dict[str, list[int]] = {}
    standby_nodes = {
        node.node_id: node
        for node in _sort_nodes_by_quality_desc(active_nodes)
        if node.node_id not in current_node_ids
    }
    standby_queue = list(standby_nodes.keys())

    for assignment in ordered_current:
        if assignment.node_id not in active_node_ids:
            continue
        surviving_segments[assignment.node_id] = [
            assignment.start_layer,
            assignment.end_layer,
        ]

    inserted_by_gap: dict[int, list[tuple[str, int, int]]] = {}

    for gap_index, assignment in enumerate(ordered_current):
        if assignment.node_id in active_node_ids:
            continue

        remaining_start = assignment.start_layer
        remaining_end = assignment.end_layer
        distance = 1
        while remaining_start < remaining_end and distance < len(ordered_current):
            for candidate_index, side in (
                (gap_index - distance, "left"),
                (gap_index + distance, "right"),
            ):
                if remaining_start >= remaining_end:
                    break
                if candidate_index < 0 or candidate_index >= len(ordered_current):
                    continue
                candidate = ordered_current[candidate_index]
                candidate_id = candidate.node_id
                if candidate_id not in surviving_segments:
                    continue
                current_start, current_end = surviving_segments[candidate_id]
                node = node_by_id[candidate_id]
                if side == "left":
                    extended_end = _max_range_end_for_node(
                        current.model_id,
                        node,
                        current_start,
                        total_layers,
                        max_end=remaining_end,
                    )
                    if extended_end > current_end:
                        surviving_segments[candidate_id][1] = extended_end
                        remaining_start = max(remaining_start, extended_end)
                else:
                    extended_start = _min_range_start_for_node(
                        current.model_id,
                        node,
                        current_end,
                        remaining_start,
                        total_layers,
                    )
                    if extended_start < current_start:
                        surviving_segments[candidate_id][0] = extended_start
                        remaining_end = min(remaining_end, extended_start)
            distance += 1

        if remaining_start < remaining_end:
            inserted: list[tuple[str, int, int]] = []
            cursor = remaining_start
            while cursor < remaining_end and standby_queue:
                standby_id = standby_queue.pop(0)
                standby = standby_nodes[standby_id]
                take_end = _max_range_end_for_node(
                    current.model_id,
                    standby,
                    cursor,
                    total_layers,
                    max_end=remaining_end,
                )
                if take_end <= cursor:
                    continue
                inserted.append((standby_id, cursor, take_end))
                cursor = take_end
            if cursor < remaining_end:
                raise ValueError("No feasible drop repair found for the current nodes")
            inserted_by_gap[gap_index] = inserted

    segments: list[tuple[str, int, int]] = []

    for index, assignment in enumerate(ordered_current):
        if assignment.node_id in surviving_segments:
            start, end = surviving_segments[assignment.node_id]
            if end > start:
                segments.append((assignment.node_id, start, end))
        else:
            segments.extend(inserted_by_gap.get(index, []))

    if not segments:
        raise ValueError("No feasible drop repair found for the current nodes")
    if segments[0][1] != 0 or segments[-1][2] != total_layers:
        raise ValueError("No feasible drop repair found for the current nodes")
    for previous, current_segment in zip(segments, segments[1:]):
        if previous[2] != current_segment[1]:
            raise ValueError("No feasible drop repair found for the current nodes")

    assignments: list[ShardAssignment] = []
    ordered_participants: list[NodeState] = []
    seen_nodes: set[str] = set()
    for index, (node_id, start, end) in enumerate(segments):
        role = "middle"
        if index == 0:
            role = "first"
        if index == len(segments) - 1:
            role = "last" if role == "middle" else role
        assignments.append(
            ShardAssignment(
                node_id=node_id,
                start_layer=start,
                end_layer=end,
                role=role,
            )
        )
        if node_id not in seen_nodes:
            ordered_participants.append(node_by_id[node_id])
            seen_nodes.add(node_id)

    base_plan = PlacementPlan(model_id=current.model_id, assignments=assignments)
    return _annotate_plan_sources_from_current(
        base_plan=base_plan,
        current=current,
        ordered_nodes=ordered_participants,
        total_layers=total_layers,
    )


def compute_next_plan(
    current: PlacementPlan | None,
    nodes,
    model_id: str,
    total_layers: int,
    layer_work: list[float] | None = None,
    previous_node_ids: set[str] | None = None,
) -> PlacementPlan:
    active_nodes = _normalize_active_nodes(nodes)
    if current is None or not current.assignments:
        return plan_static_distribution(
            model_id=model_id,
            total_layers=total_layers,
            nodes=active_nodes,
            layer_work=layer_work,
        )

    current_node_ids = (
        set(previous_node_ids)
        if previous_node_ids is not None
        else {assignment.node_id for assignment in current.assignments}
    )
    active_node_ids = {node.node_id for node in active_nodes}

    if active_node_ids == current_node_ids:
        return plan_membership_stable_distribution(
            current=current,
            nodes=active_nodes,
            total_layers=total_layers,
            layer_work=layer_work,
        )
    if active_node_ids.issuperset(current_node_ids):
        return plan_join_distribution(
            current=current,
            nodes=active_nodes,
            total_layers=total_layers,
            layer_work=layer_work,
        )
    if active_node_ids.issubset(current_node_ids):
        return plan_drop_distribution(
            current=current,
            nodes=active_nodes,
            total_layers=total_layers,
            layer_work=layer_work,
        )
    return plan_membership_stable_distribution(
        current=current,
        nodes=active_nodes,
        total_layers=total_layers,
        layer_work=layer_work,
    )


def replan_distribution(
    current: PlacementPlan,
    nodes,
    total_layers: int,
    layer_work: list[float] | None = None,
    previous_node_ids: set[str] | None = None,
) -> PlacementPlan:
    return compute_next_plan(
        current=current,
        nodes=nodes,
        model_id=current.model_id,
        total_layers=total_layers,
        layer_work=layer_work,
        previous_node_ids=previous_node_ids,
    )
