from __future__ import annotations

from dataclasses import asdict
import itertools
import time

from scheduler.ClusterPlanner import (
    diff_assignment_changes,
    plan_static_distribution,
    replan_distribution,
)
from scheduler.ClusterTypes import (
    NodeProfile,
    NodeReconfigurationAction,
    PlacementPlan,
    ReconfigurationPlan,
)
from scheduler.NodeInventory import NodeState


class ClusterCoordinator:
    _reconfig_id_counter = itertools.count(1)

    def __init__(self, transport, model_id: str, total_layers: int):
        self.transport = transport
        self.model_id = model_id
        self.total_layers = total_layers
        self.ready_nodes: set[str] = set()
        self.current_plan: PlacementPlan | None = None
        self.last_reconfiguration: ReconfigurationPlan | None = None
        self.pending_reconfiguration: ReconfigurationPlan | None = None
        self.reconfig_ready_nodes: set[str] = set()
        self.reconfiguration_committed = False
        self.reconfiguration_id: int | None = None
        self.phase = "idle"
        self.prepare_started_at: float | None = None
        self.commit_started_at: float | None = None
        self.completed_at: float | None = None

    def build_plan(self, profiles) -> PlacementPlan:
        normalized_profiles = [
            (
                profile
                if isinstance(profile, (NodeProfile, NodeState))
                else (
                    NodeState.from_dict(profile)
                    if "device_type" in profile
                    else NodeProfile.from_dict(profile)
                )
            )
            for profile in profiles
        ]
        plan = plan_static_distribution(
            self.model_id,
            self.total_layers,
            normalized_profiles,
        )
        self.current_plan = plan
        return plan

    def replan(self, profiles) -> PlacementPlan:
        normalized_profiles = [
            (
                profile
                if isinstance(profile, (NodeProfile, NodeState))
                else (
                    NodeState.from_dict(profile)
                    if "device_type" in profile
                    else NodeProfile.from_dict(profile)
                )
            )
            for profile in profiles
        ]
        if self.current_plan is None:
            self.current_plan = plan_static_distribution(
                self.model_id,
                self.total_layers,
                normalized_profiles,
            )
            return self.current_plan

        self.current_plan = replan_distribution(
            current=self.current_plan,
            nodes=normalized_profiles,
            total_layers=self.total_layers,
        )
        return self.current_plan

    def build_reconfiguration(self, profiles) -> ReconfigurationPlan:
        previous = self.current_plan
        if previous is None:
            new_plan = self.build_plan(profiles)
            actions_by_node: dict[str, list[NodeReconfigurationAction]] = {}
            for assignment in new_plan.assignments:
                actions_by_node.setdefault(assignment.node_id, []).append(
                    NodeReconfigurationAction(
                        node_id=assignment.node_id,
                        action="load",
                        start_layer=assignment.start_layer,
                        end_layer=assignment.end_layer,
                        role=assignment.role,
                    )
                )
            self.last_reconfiguration = ReconfigurationPlan(
                model_id=new_plan.model_id,
                coordinator_id=new_plan.coordinator_id,
                actions_by_node=actions_by_node,
            )
            return self.last_reconfiguration

        new_plan = self.replan(profiles)
        actions = diff_assignment_changes(previous, new_plan)
        self.last_reconfiguration = ReconfigurationPlan(
            model_id=new_plan.model_id,
            coordinator_id=new_plan.coordinator_id,
            actions_by_node=actions,
        )
        return self.last_reconfiguration

    def broadcast_plan(self, plan: PlacementPlan):
        if self.transport is None:
            raise RuntimeError("No transport configured for cluster broadcasts")
        self.transport.broadcast(
            "cluster_plan",
            {
                "model_id": plan.model_id,
                "coordinator_id": plan.coordinator_id,
                "assignments": [asdict(a) for a in plan.assignments],
            },
        )

    def broadcast_reconfiguration(self, reconfiguration: ReconfigurationPlan):
        if self.transport is None:
            raise RuntimeError("No transport configured for cluster broadcasts")
        self.transport.broadcast(
            "cluster_reconfigure",
            {
                "model_id": reconfiguration.model_id,
                "coordinator_id": reconfiguration.coordinator_id,
                "actions_by_node": {
                    node_id: [action.to_dict() for action in actions]
                    for node_id, actions in reconfiguration.actions_by_node.items()
                },
            },
        )

    def begin_reconfiguration(self, reconfiguration: ReconfigurationPlan):
        if self.transport is None:
            raise RuntimeError("No transport configured for cluster broadcasts")
        self.pending_reconfiguration = reconfiguration
        self.reconfig_ready_nodes = set()
        self.reconfiguration_committed = False
        self.reconfiguration_id = next(self._reconfig_id_counter)
        self.phase = "prepare"
        self.prepare_started_at = time.time()
        self.commit_started_at = None
        self.completed_at = None
        self.transport.broadcast(
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

    def mark_reconfig_ready(self, node_id: str):
        self.reconfig_ready_nodes.add(node_id)
        if self.pending_reconfiguration is None:
            return
        expected = set(self.pending_reconfiguration.actions_by_node.keys())
        if expected.issubset(self.reconfig_ready_nodes):
            self.phase = "commit"
            self.commit_started_at = time.time()
            self.transport.broadcast(
                "cluster_reconfigure_commit",
                {
                    "model_id": self.pending_reconfiguration.model_id,
                    "coordinator_id": self.pending_reconfiguration.coordinator_id,
                    "actions_by_node": {
                        peer_node_id: [action.to_dict() for action in actions]
                        for peer_node_id, actions in self.pending_reconfiguration.actions_by_node.items()
                    },
                },
            )
            self.reconfiguration_committed = True
            self.completed_at = time.time()
            self.phase = "complete"

    def mark_ready(self, node_id: str):
        self.ready_nodes.add(node_id)

    def all_ready(self, plan: PlacementPlan) -> bool:
        expected = {assignment.node_id for assignment in plan.assignments}
        return expected.issubset(self.ready_nodes)

    def wait_for_ready(
        self,
        plan: PlacementPlan,
        timeout_seconds: float = 5.0,
        poll_interval_seconds: float = 0.01,
    ) -> bool:
        deadline = (
            None if timeout_seconds is None else time.monotonic() + timeout_seconds
        )
        while not self.all_ready(plan):
            if deadline is not None and time.monotonic() >= deadline:
                raise TimeoutError("Timed out waiting for cluster readiness")
            time.sleep(poll_interval_seconds)
        return True

    def debug_snapshot(self) -> dict:
        return {
            "model_id": self.model_id,
            "total_layers": self.total_layers,
            "current_coordinator_id": (
                self.current_plan.coordinator_id
                if self.current_plan is not None
                else None
            ),
            "ready_nodes": sorted(self.ready_nodes),
            "pending_reconfiguration": self.pending_reconfiguration is not None,
            "reconfig_ready_nodes": sorted(self.reconfig_ready_nodes),
            "reconfiguration_committed": self.reconfiguration_committed,
            "reconfiguration_id": self.reconfiguration_id,
            "phase": self.phase,
            "prepare_started_at": self.prepare_started_at,
            "commit_started_at": self.commit_started_at,
            "completed_at": self.completed_at,
        }
