from __future__ import annotations

from dataclasses import dataclass
import math

from scheduler.ClusterTypes import (
    NodeReconfigurationAction,
    PlacementPlan,
    ShardAssignment,
)
from scheduler.NodeInventory import NodeState, normalize_node_state

FIRST_LAST_ROLE_PENALTY = 1.0
MEMORY_PRESSURE_WEIGHT = 0.2
MIGRATION_FIXED_PENALTY = 2.0
MIGRATION_SIZE_PENALTY = 0.25
PRELOAD_MISS_PENALTY = 1.0
BOUNDARY_SHIFT_PENALTY = 0.1
REPLAN_SUM_WEIGHT = 0.15


@dataclass(frozen=True)
class _PlanScore:
    objective: float
    bottleneck: float
    total: float


def _layer_work(
    total_layers: int, layer_work: list[float] | None = None
) -> list[float]:
    if layer_work is None:
        return [1.0] * total_layers
    if len(layer_work) != total_layers:
        raise ValueError("layer_work length must equal total_layers")
    return layer_work


def _sum_work(prefix: list[float], start: int, end: int) -> float:
    return prefix[end] - prefix[start]


def estimate_reference_stage_width(total_layers: int, active_nodes: int) -> int:
    if total_layers <= 0:
        raise ValueError("total_layers must be positive")
    if active_nodes <= 0:
        raise ValueError("active_nodes must be positive")
    return int(math.ceil(total_layers / active_nodes))


def estimate_stage_time_for_node(
    node,
    width: int,
    total_layers: int,
    role: str = "middle",
) -> float:
    normalized = normalize_node_state(node)
    layer_work = _layer_work(total_layers)
    prefix = [0.0]
    for value in layer_work:
        prefix.append(prefix[-1] + value)
    return _estimate_stage_cost(
        0,
        width,
        normalized,
        total_layers,
        prefix,
        is_first_stage=(role == "first"),
        is_last_stage=(role == "last"),
    )


def coordinator_score(
    node,
    total_layers: int,
    active_nodes: int,
    role: str = "first",
    current_plan: PlacementPlan | None = None,
) -> float:
    normalized = normalize_node_state(node)
    if current_plan is not None:
        for assignment in current_plan.assignments:
            if assignment.node_id == normalized.node_id:
                return estimate_stage_time_for_node(
                    normalized,
                    width=assignment.num_layers,
                    total_layers=total_layers,
                    role=assignment.role,
                )
    width = estimate_reference_stage_width(total_layers, active_nodes)
    return estimate_stage_time_for_node(
        normalized,
        width=width,
        total_layers=total_layers,
        role=role,
    )


def choose_coordinator(
    nodes, total_layers: int | None = None, current_plan: PlacementPlan | None = None
) -> str:
    normalized_nodes = [normalize_node_state(node) for node in nodes]
    if not normalized_nodes:
        raise ValueError("At least one node profile is required")
    candidate_nodes = normalized_nodes
    if current_plan is not None and current_plan.assignments:
        assigned_node_ids = {
            assignment.node_id for assignment in current_plan.assignments
        }
        scoped = [
            node for node in normalized_nodes if node.node_id in assigned_node_ids
        ]
        if scoped:
            candidate_nodes = scoped
    effective_total_layers = total_layers
    if effective_total_layers is None:
        if current_plan is not None and current_plan.assignments:
            effective_total_layers = max(
                assignment.end_layer for assignment in current_plan.assignments
            )
        else:
            effective_total_layers = max(len(normalized_nodes), 1)
    return min(
        candidate_nodes,
        key=lambda node: coordinator_score(
            node,
            total_layers=effective_total_layers,
            active_nodes=len(candidate_nodes),
            current_plan=current_plan,
        ),
    ).node_id


def score_assignment_move_cost(
    current_owner: str | None,
    target_owner: str,
    shard: tuple[int, int],
) -> float:
    if current_owner is None:
        return 1.0
    if current_owner == target_owner:
        return 0.0
    return 10.0 + float(shard[1] - shard[0])


def estimate_assignment_cost(
    node: NodeState,
    assignment: ShardAssignment,
    shard_param_gb: float,
    bandwidth_gbps: float | None = None,
) -> float:
    node = normalize_node_state(node)
    shard_key = (assignment.start_layer, assignment.end_layer)
    load_penalty = 0.0 if shard_key in node.loaded_ranges else shard_param_gb
    device_bonus = -2.0 if node.device_type == "cuda" else 0.0
    role_cost = FIRST_LAST_ROLE_PENALTY if assignment.role in {"first", "last"} else 0.0
    bandwidth_cost = 0.0 if bandwidth_gbps is None else 1.0 / max(bandwidth_gbps, 0.1)
    compute_bias = -node.effective_speed() * 0.01
    return load_penalty + role_cost + bandwidth_cost + compute_bias + device_bonus


def _sort_nodes_by_quality_desc(nodes: list[NodeState]) -> list[NodeState]:
    """Higher compute speed and larger capacity rank first (cold-start greedy order)."""
    return sorted(
        nodes,
        key=lambda node: (
            -node.effective_speed(),
            -node.effective_capacity_blocks(),
            node.node_id,
        ),
    )


def _node_quality_key(node: NodeState) -> tuple[float, int, str]:
    return (
        node.effective_speed(),
        node.effective_capacity_blocks(),
        node.node_id,
    )


def _arrange_nodes_for_pipeline(
    nodes: list[NodeState], total_layers: int
) -> list[NodeState]:
    if not nodes:
        return []
    reference_width = estimate_reference_stage_width(total_layers, len(nodes))
    ranked = sorted(
        nodes,
        key=lambda node: (
            estimate_stage_time_for_node(
                node,
                width=reference_width,
                total_layers=total_layers,
                role="middle",
            ),
            -node.effective_capacity_blocks(),
            node.node_id,
        ),
    )
    front: list[NodeState] = []
    back: list[NodeState] = []
    for index, node in enumerate(ranked):
        if index % 2 == 0:
            front.append(node)
        else:
            back.append(node)
    return front + list(reversed(back))


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

    temp_plan = PlacementPlan(
        model_id=model_id,
        assignments=assignments,
    )
    return PlacementPlan(
        model_id=model_id,
        assignments=assignments,
        coordinator_id=choose_coordinator(
            ordered_nodes, total_layers=boundaries[-1], current_plan=temp_plan
        ),
    )


def _estimate_stage_cost(
    start: int,
    end: int,
    node: NodeState,
    total_layers: int,
    prefix_work: list[float],
    is_first_stage: bool,
    is_last_stage: bool,
) -> float:
    if end <= start:
        return math.inf
    width = end - start
    capacity = node.effective_capacity_blocks()
    if capacity <= 0 or width > capacity:
        return math.inf

    work = _sum_work(prefix_work, start, end)
    speed = max(node.effective_speed(), 0.001)
    cost = work / speed
    if is_first_stage:
        cost += FIRST_LAST_ROLE_PENALTY / speed
    if is_last_stage:
        cost += FIRST_LAST_ROLE_PENALTY / speed
    cost += MEMORY_PRESSURE_WEIGHT * (width / max(capacity, 1))
    return cost


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
    remaining = total_layers
    used_nodes: list[NodeState] = []
    boundaries: list[int] = [0]

    for node in ordered_nodes:
        if remaining <= 0:
            break
        cap = node.effective_capacity_blocks()
        if cap <= 0:
            continue
        take = min(cap, remaining)
        if take <= 0:
            continue
        used_nodes.append(node)
        boundaries.append(boundaries[-1] + take)
        remaining -= take

    if remaining > 0 or not used_nodes:
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
    temp_plan = PlacementPlan(model_id=current.model_id, assignments=assignments)
    return PlacementPlan(
        model_id=current.model_id,
        assignments=assignments,
        coordinator_id=choose_coordinator(
            ordered_nodes,
            total_layers=total_layers,
            current_plan=temp_plan,
        ),
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
        if node.online and node.effective_capacity_blocks() > 0
    ]
    if not active_nodes:
        raise ValueError(
            "At least one online node with positive capacity is required for planning"
        )

    layer_work = _layer_work(total_layers, layer_work)
    total_capacity = sum(node.effective_capacity_blocks() for node in active_nodes)
    if total_capacity < total_layers:
        raise ValueError("Insufficient cluster capacity for total layers")

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
        if node.online and node.effective_capacity_blocks() > 0
    ]
    if not active_nodes:
        raise ValueError(
            "At least one online node with positive capacity is required for planning"
        )
    return active_nodes


def _assignment_can_stay(
    assignment: ShardAssignment,
    node_by_id: dict[str, NodeState],
) -> bool:
    node = node_by_id.get(assignment.node_id)
    if node is None or not node.online:
        return False
    width = assignment.num_layers
    if width > node.effective_capacity_blocks():
        return False
    shard_key = (assignment.start_layer, assignment.end_layer)
    if shard_key in node.loaded_ranges:
        return True
    return width <= node.effective_capacity_blocks()


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


def _range_loaded(node: NodeState, start: int, end: int) -> bool:
    for loaded_start, loaded_end in node.loaded_ranges:
        if loaded_start <= start and end <= loaded_end:
            return True
    return False


def _estimate_migration_cost(
    node: NodeState,
    start: int,
    end: int,
    owner_by_layer: list[str | None],
) -> float:
    dominant_owner = _dominant_owner(owner_by_layer, start, end)
    width = end - start
    changed = dominant_owner is not None and dominant_owner != node.node_id
    migration_cost = MIGRATION_FIXED_PENALTY * float(changed)
    migration_cost += MIGRATION_SIZE_PENALTY * sum(
        1
        for owner in owner_by_layer[start:end]
        if owner is not None and owner != node.node_id
    )
    if not _range_loaded(node, start, end):
        migration_cost += PRELOAD_MISS_PENALTY
    return migration_cost + (0.05 * width)


def _solve_replan_dp(
    model_id: str,
    total_layers: int,
    ordered_nodes: list[NodeState],
    layer_work: list[float],
    current: PlacementPlan,
) -> tuple[_PlanScore, PlacementPlan] | None:
    prefix = [0.0]
    for value in layer_work:
        prefix.append(prefix[-1] + value)

    current_boundaries = [
        assignment.end_layer for assignment in current.assignments[:-1]
    ]
    owner_by_layer = _owner_by_layer(current, total_layers)
    node_count = len(ordered_nodes)
    inf_score = _PlanScore(math.inf, math.inf, math.inf)
    dp: list[list[_PlanScore]] = [
        [inf_score for _ in range(total_layers + 1)] for _ in range(node_count + 1)
    ]
    prev: list[list[int | None]] = [
        [None for _ in range(total_layers + 1)] for _ in range(node_count + 1)
    ]
    dp[0][0] = _PlanScore(0.0, 0.0, 0.0)

    for i in range(1, node_count + 1):
        node = ordered_nodes[i - 1]
        for end in range(i, total_layers - (node_count - i) + 1):
            best = inf_score
            best_start = None
            for start in range(i - 1, end):
                previous = dp[i - 1][start]
                if math.isinf(previous.objective):
                    continue
                stage_cost = _estimate_stage_cost(
                    start,
                    end,
                    node,
                    total_layers,
                    prefix,
                    is_first_stage=(i == 1),
                    is_last_stage=(i == node_count),
                )
                if math.isinf(stage_cost):
                    continue
                migration_cost = _estimate_migration_cost(
                    node, start, end, owner_by_layer
                )
                target_boundary = (
                    current_boundaries[i - 1]
                    if i - 1 < len(current_boundaries)
                    else total_layers
                )
                instability_cost = BOUNDARY_SHIFT_PENALTY * abs(end - target_boundary)
                stage_total = stage_cost + migration_cost + instability_cost
                bottleneck = max(previous.bottleneck, stage_cost)
                total = previous.total + stage_total
                objective = bottleneck + REPLAN_SUM_WEIGHT * total
                score = _PlanScore(objective, bottleneck, total)
                if (score.objective, score.total) < (best.objective, best.total):
                    best = score
                    best_start = start
            dp[i][end] = best
            prev[i][end] = best_start

    best_plan: tuple[_PlanScore, PlacementPlan] | None = None
    for used_nodes in range(1, node_count + 1):
        score = dp[used_nodes][total_layers]
        if math.isinf(score.objective):
            continue
        boundaries = [0] * (used_nodes + 1)
        boundaries[used_nodes] = total_layers
        cursor = total_layers
        for i in range(used_nodes, 0, -1):
            start = prev[i][cursor]
            if start is None:
                break
            boundaries[i - 1] = start
            cursor = start

        base_plan = _build_assignments(model_id, ordered_nodes[:used_nodes], boundaries)
        plan = _annotate_plan_sources_from_current(
            base_plan=base_plan,
            current=current,
            ordered_nodes=ordered_nodes[:used_nodes],
            total_layers=total_layers,
        )
        if best_plan is None or (score.objective, score.total) < (
            best_plan[0].objective,
            best_plan[0].total,
        ):
            best_plan = (score, plan)
    return best_plan


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
            _assignment_can_stay(assignment, node_by_id)
            for assignment in current.assignments
        )
        and current_node_ids.issubset(active_node_ids)
    ):
        return current

    layer_work = _layer_work(total_layers, layer_work)
    total_capacity = sum(node.effective_capacity_blocks() for node in active_nodes)
    if total_capacity < total_layers:
        raise ValueError("Insufficient cluster capacity for total layers")

    ordered_nodes = _arrange_nodes_for_pipeline(active_nodes, total_layers=total_layers)
    solved = _solve_replan_dp(
        current.model_id, total_layers, ordered_nodes, layer_work, current
    )
    if solved is None:
        raise ValueError("No feasible replan found for the current nodes")
    return solved[1]


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

        remaining = standby.effective_capacity_blocks()
        if remaining <= 0:
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
        new_start = center.start_layer
        new_end = center.end_layer

        if remaining >= center.num_layers:
            remaining -= center.num_layers
            left_bound = weakest_index
            right_bound = weakest_index
        else:
            preferred_side = _neighbor_side(weakest_index - 1, weakest_index + 1)
            if preferred_side == "left":
                new_start = center.end_layer - remaining
            else:
                new_end = center.start_layer + remaining
            remaining = 0
            left_bound = weakest_index
            right_bound = weakest_index

        while remaining > 0:
            candidate_side = _neighbor_side(left_bound - 1, right_bound + 1)
            if candidate_side is None:
                break
            if candidate_side == "left":
                candidate = ordered[left_bound - 1]
                width = candidate.num_layers
                if remaining >= width:
                    new_start = candidate.start_layer
                    remaining -= width
                    left_bound -= 1
                else:
                    new_start = candidate.end_layer - remaining
                    remaining = 0
            else:
                candidate = ordered[right_bound + 1]
                width = candidate.num_layers
                if remaining >= width:
                    new_end = candidate.end_layer
                    remaining -= width
                    right_bound += 1
                else:
                    new_end = candidate.start_layer + remaining
                    remaining = 0

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
        _assignment_can_stay(assignment, node_by_id)
        for assignment in current.assignments
    ):
        return current

    layer_work = _layer_work(total_layers, layer_work)
    total_capacity = sum(node.effective_capacity_blocks() for node in active_nodes)
    if total_capacity < total_layers:
        raise ValueError("Insufficient cluster capacity for total layers")

    ordered_current = sorted(current.assignments, key=lambda item: item.start_layer)
    surviving_widths: dict[str, int] = {}
    spare_capacity: dict[str, int] = {}
    standby_nodes = {
        node.node_id: node
        for node in _sort_nodes_by_quality_desc(active_nodes)
        if node.node_id not in current_node_ids
    }
    standby_queue = list(standby_nodes.keys())

    for assignment in ordered_current:
        if assignment.node_id not in active_node_ids:
            continue
        surviving_widths[assignment.node_id] = assignment.num_layers
        spare_capacity[assignment.node_id] = max(
            0,
            node_by_id[assignment.node_id].effective_capacity_blocks()
            - assignment.num_layers,
        )

    inserted_by_gap: dict[int, list[tuple[str, int]]] = {}

    for gap_index, assignment in enumerate(ordered_current):
        if assignment.node_id in active_node_ids:
            continue

        remaining_gap = assignment.num_layers
        distance = 1
        while remaining_gap > 0 and distance < len(ordered_current):
            for candidate_index in (gap_index - distance, gap_index + distance):
                if remaining_gap <= 0:
                    break
                if candidate_index < 0 or candidate_index >= len(ordered_current):
                    continue
                candidate = ordered_current[candidate_index]
                candidate_id = candidate.node_id
                if candidate_id not in surviving_widths:
                    continue
                available = spare_capacity.get(candidate_id, 0)
                if available <= 0:
                    continue
                take = min(available, remaining_gap)
                surviving_widths[candidate_id] += take
                spare_capacity[candidate_id] -= take
                remaining_gap -= take
            distance += 1

        if remaining_gap > 0:
            inserted: list[tuple[str, int]] = []
            while remaining_gap > 0 and standby_queue:
                standby_id = standby_queue.pop(0)
                standby = standby_nodes[standby_id]
                take = min(standby.effective_capacity_blocks(), remaining_gap)
                if take <= 0:
                    continue
                inserted.append((standby_id, take))
                remaining_gap -= take
            if remaining_gap > 0:
                raise ValueError("No feasible drop repair found for the current nodes")
            inserted_by_gap[gap_index] = inserted

    rebuilt_nodes: list[NodeState] = []
    boundaries: list[int] = [0]

    for index, assignment in enumerate(ordered_current):
        if assignment.node_id in surviving_widths:
            width = surviving_widths[assignment.node_id]
            if width > 0:
                rebuilt_nodes.append(node_by_id[assignment.node_id])
                boundaries.append(boundaries[-1] + width)
        else:
            for standby_id, width in inserted_by_gap.get(index, []):
                rebuilt_nodes.append(node_by_id[standby_id])
                boundaries.append(boundaries[-1] + width)

    if boundaries[-1] != total_layers or not rebuilt_nodes:
        raise ValueError("No feasible drop repair found for the current nodes")

    base_plan = _build_assignments(
        current.model_id,
        rebuilt_nodes,
        boundaries,
    )
    return _annotate_plan_sources_from_current(
        base_plan=base_plan,
        current=current,
        ordered_nodes=rebuilt_nodes,
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
