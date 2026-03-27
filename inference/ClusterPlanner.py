from __future__ import annotations

from dataclasses import dataclass
import math

from inference.ClusterTypes import NodeReconfigurationAction, PlacementPlan, ShardAssignment
from inference.NodeInventory import NodeState, normalize_node_state

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


def _layer_work(total_layers: int, layer_work: list[float] | None = None) -> list[float]:
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


def choose_coordinator(nodes, total_layers: int | None = None, current_plan: PlacementPlan | None = None) -> str:
    normalized_nodes = [normalize_node_state(node) for node in nodes]
    if not normalized_nodes:
        raise ValueError("At least one node profile is required")
    effective_total_layers = total_layers
    if effective_total_layers is None:
        if current_plan is not None and current_plan.assignments:
            effective_total_layers = max(assignment.end_layer for assignment in current_plan.assignments)
        else:
            effective_total_layers = max(len(normalized_nodes), 1)
    return min(
        normalized_nodes,
        key=lambda node: coordinator_score(
            node,
            total_layers=effective_total_layers,
            active_nodes=len(normalized_nodes),
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


def _arrange_nodes_for_pipeline(nodes: list[NodeState], total_layers: int) -> list[NodeState]:
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
        coordinator_id=choose_coordinator(ordered_nodes, total_layers=boundaries[-1], current_plan=temp_plan),
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


def _solve_cold_start_dp(
    model_id: str,
    total_layers: int,
    ordered_nodes: list[NodeState],
    layer_work: list[float],
) -> tuple[_PlanScore, PlacementPlan] | None:
    prefix = [0.0]
    for value in layer_work:
        prefix.append(prefix[-1] + value)

    node_count = len(ordered_nodes)
    inf_score = _PlanScore(math.inf, math.inf, math.inf)
    dp: list[list[_PlanScore]] = [[inf_score for _ in range(total_layers + 1)] for _ in range(node_count + 1)]
    prev: list[list[int | None]] = [[None for _ in range(total_layers + 1)] for _ in range(node_count + 1)]
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
                bottleneck = max(previous.bottleneck, stage_cost)
                total = previous.total + stage_cost
                score = _PlanScore(bottleneck, bottleneck, total)
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
        plan = _build_assignments(model_id, ordered_nodes[:used_nodes], boundaries)
        if best_plan is None or (score.objective, score.total) < (
            best_plan[0].objective,
            best_plan[0].total,
        ):
            best_plan = (score, plan)
    return best_plan


def plan_static_distribution(
    model_id: str,
    total_layers: int,
    nodes,
    layer_work: list[float] | None = None,
) -> PlacementPlan:
    normalized_nodes = [normalize_node_state(node) for node in nodes]
    active_nodes = [node for node in normalized_nodes if node.online and node.effective_capacity_blocks() > 0]
    if not active_nodes:
        raise ValueError("At least one online node with positive capacity is required for planning")

    layer_work = _layer_work(total_layers, layer_work)
    total_capacity = sum(node.effective_capacity_blocks() for node in active_nodes)
    if total_capacity < total_layers:
        raise ValueError("Insufficient cluster capacity for total layers")

    ordered_nodes = _arrange_nodes_for_pipeline(active_nodes, total_layers=total_layers)
    solved = _solve_cold_start_dp(model_id, total_layers, ordered_nodes, layer_work)
    if solved is None:
        raise ValueError("No feasible contiguous placement found for the current nodes")
    return solved[1]


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


def _dominant_owner(owner_by_layer: list[str | None], start: int, end: int) -> str | None:
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
    current_by_key = {_assignment_key(assignment): assignment for assignment in current.assignments}
    new_by_key = {_assignment_key(assignment): assignment for assignment in new.assignments}
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
        node_actions.sort(key=lambda item: (item.start_layer, item.end_layer, item.action))
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
        1 for owner in owner_by_layer[start:end] if owner is not None and owner != node.node_id
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

    current_boundaries = [assignment.end_layer for assignment in current.assignments[:-1]]
    owner_by_layer = _owner_by_layer(current, total_layers)
    node_count = len(ordered_nodes)
    inf_score = _PlanScore(math.inf, math.inf, math.inf)
    dp: list[list[_PlanScore]] = [[inf_score for _ in range(total_layers + 1)] for _ in range(node_count + 1)]
    prev: list[list[int | None]] = [[None for _ in range(total_layers + 1)] for _ in range(node_count + 1)]
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
                migration_cost = _estimate_migration_cost(node, start, end, owner_by_layer)
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
        owner_by_layer = _owner_by_layer(current, total_layers)
        assignments: list[ShardAssignment] = []
        for assignment in base_plan.assignments:
            dominant_owner = _dominant_owner(owner_by_layer, assignment.start_layer, assignment.end_layer)
            source_node_id = None if dominant_owner == assignment.node_id else dominant_owner
            assignments.append(
                ShardAssignment(
                    node_id=assignment.node_id,
                    start_layer=assignment.start_layer,
                    end_layer=assignment.end_layer,
                    role=assignment.role,
                    source_node_id=source_node_id,
                )
            )
        temp_plan = PlacementPlan(
            model_id=current.model_id,
            assignments=assignments,
        )
        plan = PlacementPlan(
            model_id=current.model_id,
            assignments=assignments,
            coordinator_id=choose_coordinator(
                ordered_nodes[:used_nodes],
                total_layers=total_layers,
                current_plan=temp_plan,
            ),
        )
        if best_plan is None or (score.objective, score.total) < (
            best_plan[0].objective,
            best_plan[0].total,
        ):
            best_plan = (score, plan)
    return best_plan


def replan_distribution(
    current: PlacementPlan,
    nodes,
    total_layers: int,
    layer_work: list[float] | None = None,
) -> PlacementPlan:
    normalized_nodes = [normalize_node_state(node) for node in nodes]
    active_nodes = [node for node in normalized_nodes if node.online and node.effective_capacity_blocks() > 0]
    if not active_nodes:
        raise ValueError("At least one online node with positive capacity is required for replanning")

    node_by_id = {node.node_id: node for node in active_nodes}
    current_node_ids = {assignment.node_id for assignment in current.assignments}
    active_node_ids = set(node_by_id.keys())
    if current.assignments and all(
        _assignment_can_stay(assignment, node_by_id) for assignment in current.assignments
    ) and current_node_ids == active_node_ids:
        return current

    layer_work = _layer_work(total_layers, layer_work)
    total_capacity = sum(node.effective_capacity_blocks() for node in active_nodes)
    if total_capacity < total_layers:
        raise ValueError("Insufficient cluster capacity for total layers")

    ordered_nodes = _arrange_nodes_for_pipeline(active_nodes, total_layers=total_layers)
    solved = _solve_replan_dp(current.model_id, total_layers, ordered_nodes, layer_work, current)
    if solved is None:
        raise ValueError("No feasible replan found for the current nodes")
    return solved[1]
