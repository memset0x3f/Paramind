from __future__ import annotations

import time
from typing import Any, Callable, Optional

from scheduler.ClusterTypes import NodeReconfigurationAction, ShardAssignment
from .ShardConfig import ModelFamily, ShardConfig
from .ShardRegistry import ShardRecord, ShardRegistry
from .ShardLoader import ShardLoader


class NodeRuntime:
    def __init__(
        self,
        node_id: str,
        family: ModelFamily,
        total_layers: int,
        device: str = "cpu",
        loader=None,
        on_ready: Optional[Callable[[str], Any]] = None,
    ):
        self.node_id = node_id
        self.family = family
        self.total_layers = total_layers
        self.device = device
        self.loader = loader
        self.on_ready = on_ready
        self.transport = None
        self.assignment: ShardAssignment | None = None
        self.local_shard = None
        self.registry = ShardRegistry()

    def attach_transport(
        self, transport, on_ready: Optional[Callable[[str], Any]] = None
    ):
        self.transport = transport
        if on_ready is not None:
            self.on_ready = on_ready

        if hasattr(transport, "register_handler"):
            transport.register_handler("cluster_plan", self.handle_cluster_plan)
            transport.register_handler(
                "cluster_reconfigure", self.handle_reconfiguration_plan
            )
            transport.register_handler(
                "cluster_reconfigure_prepare", self.handle_reconfiguration_prepare
            )
            transport.register_handler(
                "cluster_reconfigure_commit", self.handle_reconfiguration_commit
            )
        return transport

    def _select_assignment(self, payload: dict) -> ShardAssignment | None:
        for item in payload.get("assignments", []):
            if item.get("node_id") == self.node_id:
                return ShardAssignment(
                    node_id=item["node_id"],
                    start_layer=item["start_layer"],
                    end_layer=item["end_layer"],
                    role=item.get("role", "middle"),
                    source_node_id=item.get("source_node_id"),
                )
        return None

    def handle_cluster_plan(self, payload: dict):
        assignment = self._select_assignment(payload)
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
        self.signal_ready()
        return result

    def handle_reconfiguration_commit(self, payload: dict):
        actions = self.handle_reconfiguration_plan(payload)
        if not actions:
            return []
        self.commit_reconfiguration(actions)
        return actions

    def signal_ready(self):
        if self.on_ready is not None:
            return self.on_ready(self.node_id)
        return None

    def apply_assignment(self, model_id: str, assignment: ShardAssignment):
        if assignment.node_id != self.node_id:
            raise ValueError(
                f"Assignment node_id {assignment.node_id!r} does not match runtime node_id {self.node_id!r}"
            )
        self.assignment = assignment
        if self.loader is not None:
            if hasattr(self.loader, "load_assignment"):
                self.loader.load_assignment(assignment)
                self.local_shard = getattr(self.loader, "loaded", assignment)
            elif hasattr(self.loader, "load"):
                self.local_shard = self.loader.load()
            return

        config = ShardConfig(
            model_id=model_id,
            family=self.family,
            start_layer=assignment.start_layer,
            end_layer=assignment.end_layer,
            total_layers=self.total_layers,
        )
        self.local_shard = ShardLoader(config, device=self.device).load()

    def load_assignment(self, model_id: str, assignment: ShardAssignment):
        self.apply_assignment(model_id, assignment)

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
                        last_prepare_ts=time.time(),
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
                        last_prepare_ts=time.time(),
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
                        last_prepare_ts=record.last_prepare_ts,
                        last_commit_ts=time.time(),
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
                            last_prepare_ts=record.last_prepare_ts,
                            last_commit_ts=time.time(),
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
                last_prepare_ts=record.last_prepare_ts,
                last_commit_ts=record.last_commit_ts,
            )
        )

    def finish_request(self, shard_key: tuple[int, int]):
        record = self.registry.get(shard_key)
        if record is None:
            raise RuntimeError(f"Missing shard for request completion: {shard_key}")
        new_inflight = max(record.inflight_requests - 1, 0)
        updated = ShardRecord(
            shard_key=record.shard_key,
            role=record.role,
            state=record.state,
            shard_obj=record.shard_obj,
            active_owner=record.active_owner,
            pending_unload=record.pending_unload,
            inflight_requests=new_inflight,
            last_prepare_ts=record.last_prepare_ts,
            last_commit_ts=record.last_commit_ts,
        )
        self.registry.put(updated)
        self._release_if_drained(shard_key)

    def _release_if_drained(self, shard_key: tuple[int, int]):
        record = self.registry.get(shard_key)
        if record is None:
            return
        if (
            record.state == "draining"
            and record.pending_unload
            and record.inflight_requests == 0
        ):
            self.registry.remove(shard_key)

    def debug_snapshot(self) -> dict:
        assignment = None
        if self.assignment is not None:
            assignment = {
                "node_id": self.assignment.node_id,
                "start_layer": self.assignment.start_layer,
                "end_layer": self.assignment.end_layer,
                "role": self.assignment.role,
                "source_node_id": self.assignment.source_node_id,
            }
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
            "assignment": assignment,
            "shards": shards,
            "has_local_shard": self.local_shard is not None,
        }
