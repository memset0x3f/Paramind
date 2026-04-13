from __future__ import annotations

import time
from typing import Any, Callable, Optional

from scheduler.ClusterTypes import (
    NodeReconfigurationAction,
    ShardAssignment,
    NodeProfile,
)
from scheduler.ClusterCoordinator import ClusterCoordinator
from .ShardConfig import ModelFamily, ShardConfig
from .ShardRegistry import ShardRecord, ShardRegistry
from .ShardLoader import ShardLoader, ModelShard
from scheduler.DeviceProfile import build_node_profile

from p2p import P2PClient
from common.Utils import getSignalServerAddr
import uuid
import json
import torch
import queue
import threading
import logging
from transformers import AutoTokenizer

logger = logging.getLogger("paramind.NodeRuntime")


class NodeRuntime:
    def __init__(
        self,
        node_id: uuid.UUID,
        node_group: str,
        family: ModelFamily,
        total_layers: int,
        model_id: str,
        shard_assignment: ShardAssignment | None = None,
        device: str = "cpu",
        loader=None,
        on_ready: Optional[Callable[[str], Any]] = None,
    ):
        self.node_id = str(node_id)
        self.node_group = node_group
        self.family = family
        self.total_layers = total_layers
        self.model_id = model_id
        self.device = device
        self.loader = loader
        self.on_ready = on_ready
        self.p2p_client = P2PClient(getSignalServerAddr(), client_uuid=node_id)
        self.assignment = shard_assignment
        self.local_shard: Optional[ModelShard] = None
        self.kvCache: dict[str, Any] = {}
        self.tokenizer = AutoTokenizer.from_pretrained(model_id, trust_remote_code=True)
        if shard_assignment is not None:
            self.apply_assignment(model_id=model_id, assignment=shard_assignment)
        self.registry = ShardRegistry()
        self.global_assignments: list[ShardAssignment] = []
        self.route: list[str] = []
        self.shard_owner_index: dict[str, str] = {}

        self.token_queue = queue.Queue()
        self.torch_input_worker_thread = threading.Thread(
            target=self.local_inference_worker, daemon=True
        )
        self.torch_input_worker_thread.start()

        self.inference_results = {}
        self.eos_ids = set(self.tokenizer.all_special_ids)
        if self.tokenizer.eos_token_id is not None:
            self.eos_ids.add(self.tokenizer.eos_token_id)

        self.coordinator = None
        self.is_coordinator = False
        self.profile = build_node_profile(self.node_id)
        self.peer_profiles: dict[str, NodeProfile] = {self.node_id: self.profile}

    def _register_handlers(self):
        self.p2p_client.register_handler("allPeers", self.handle_allPeers)
        self.p2p_client.register_handler("newPeer", self.handle_newPeer)
        self.p2p_client.register_handler("cluster_plan", self.handle_cluster_plan)
        self.p2p_client.register_handler(
            "reconfiguration_prepare", self.handle_reconfiguration_prepare
        )
        self.p2p_client.register_handler(
            "reconfiguration_commit", self.handle_reconfiguration_commit
        )
        self.p2p_client.register_handler("torchInput", self.handle_torch_input)
        self.p2p_client.register_handler("torchOutput", self.handle_torch_output)

    def run(self):
        self._register_handlers()
        self.p2p_client.connect()
        self.p2p_client.registerToGroup(self.node_group, self.profile)
        self.p2p_client.waitForAllPeerConnection()

    def distributedInfer(self, prompt: str):
        if self.local_shard is None:
            raise RuntimeError("Local shard is not loaded for inference")

        messages = [
            {"role": "user", "content": prompt},
        ]
        text = self.tokenizer.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True
        )
        input_ids = self.tokenizer(text, return_tensors="pt").input_ids.to(self.device)
        stard_node_id = self.node_id
        inference_id = self.node_id + "-" + str(int(time.time() * 1000))
        inference_path = self.route
        self.p2p_client.sendTensorToPeer(
            tensor=input_ids,
            peer_uuid=inference_path[0],
            inference_path=inference_path,
            inference_id=inference_id,
            start_node_id=stard_node_id,
            input=True,
        )

    def handle_newPeer(self, payload: dict):
        peer_id = payload.get("uuid")
        if peer_id and peer_id != self.node_id:
            profile_dict = payload.get("profile", {})
            self.peer_profiles[peer_id] = NodeProfile.from_dict(profile_dict)
            logger.info(f"New peer connected: {peer_id} with profile {profile_dict}")

    def handle_allPeers(self, payload: dict):
        peers = payload.get("peers", [])
        for peer in peers:
            peer_id = peer.get("uuid")
            if peer_id and peer_id != self.node_id:
                profile_dict = peer.get("profile", {})
                self.peer_profiles[peer_id] = NodeProfile.from_dict(profile_dict)
        if len(peers) == 0 or (len(peers) == 1 and str(self.node_id) in peers):
            assert (
                self.coordinator is None
            ), "Coordinator should not exist before allPeers message"
            self.coordinator = ClusterCoordinator(
                transport=self.p2p_client,
                model_id=self.model_id,
                total_layers=self.total_layers,
            )
            self.is_coordinator = True
            logger.info("Node %s initialized as coordinator", self.node_id)

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

    def _extract_global_assignments(self, payload: dict) -> list[ShardAssignment]:
        assignments: list[ShardAssignment] = []
        for item in payload.get("assignments", []):
            assignments.append(
                ShardAssignment(
                    node_id=item["node_id"],
                    start_layer=item["start_layer"],
                    end_layer=item["end_layer"],
                    role=item.get("role", "middle"),
                    source_node_id=item.get("source_node_id"),
                )
            )
        return assignments

    def _derive_route_and_owner_index(self) -> tuple[list[str], dict[str, str]]:
        ordered = sorted(
            self.global_assignments,
            key=lambda assignment: assignment.start_layer,
        )
        route = [assignment.node_id for assignment in ordered]
        owner_index = {
            f"{assignment.start_layer}-{assignment.end_layer}": assignment.node_id
            for assignment in ordered
        }
        return route, owner_index

    def handle_torch_input(self, data: dict):
        data["input"] = True
        self.token_queue.put(data)

    def handle_torch_output(self, data: dict):
        data["input"] = False
        self.token_queue.put(data)

    @torch.no_grad()
    def local_inference_worker(self):
        while True:
            data = self.token_queue.get()
            assert (
                self.local_shard is not None
            ), "Local shard is not loaded to handle input"

            inference_path = data["inference_path"]
            inference_id = data["inference_id"]
            start_node_id = data["start_node_id"]
            tensorData = data["tensor"].to(self.device)
            is_input = data["input"]
            # TODO: Check route and inference path consistency

            logger.debug(
                f"Handling tensor for inference_id={inference_id}, input={is_input}, tensor_shape={tensorData.shape}, inference_path={inference_path}"
            )
            if is_input:
                # Get inference-specific KV cache
                past_kv = self.kvCache.get(inference_id, None)
                logits, new_kv = self.local_shard.forward(
                    tensorData, past_key_values=past_kv
                )
                # Update inference-specific cache
                self.kvCache[inference_id] = new_kv

                cur_hop = inference_path.index(self.node_id)
                if cur_hop < len(inference_path) - 1:
                    next_node_id = inference_path[cur_hop + 1]
                    self.p2p_client.sendTensorToPeer(
                        tensor=logits.cpu(),
                        peer_uuid=next_node_id,
                        inference_path=inference_path,
                        inference_id=inference_id,
                        start_node_id=start_node_id,
                        input=True,
                    )
                    continue
                else:
                    # Output to start node
                    self.p2p_client.sendTensorToPeer(
                        tensor=logits.cpu(),
                        peer_uuid=start_node_id,
                        inference_path=inference_path,
                        inference_id=inference_id,
                        start_node_id=start_node_id,
                        input=False,
                    )
            else:
                logits = tensorData[:, -1, :]
                next_id = torch.argmax(logits, dim=-1).reshape(1, 1)
                token_id = next_id.item()
                if inference_id not in self.inference_results:
                    self.inference_results[inference_id] = ([], False)
                self.inference_results[inference_id][0].append(token_id)
                if token_id in self.eos_ids:
                    self.inference_results[inference_id] = (
                        self.tokenizer.decode(
                            self.inference_results[inference_id][0],
                            skip_special_tokens=True,
                            clean_up_tokenization_spaces=False,
                        ),
                        True,
                    )
                    # Clean up inference-specific KV cache
                    self.kvCache.pop(inference_id, None)
                    continue
                self.p2p_client.sendTensorToPeer(
                    tensor=next_id.cpu(),
                    peer_uuid=inference_path[0],
                    inference_path=inference_path,
                    inference_id=inference_id,
                    start_node_id=start_node_id,
                    input=True,
                )

    def handle_cluster_plan(self, payload: dict):
        self.global_assignments = self._extract_global_assignments(payload)
        fallback_route, fallback_owner_index = self._derive_route_and_owner_index()
        self.route = list(payload.get("route") or fallback_route)
        raw_owner_index = payload.get("shard_owner_index")
        self.shard_owner_index = (
            {str(key): str(value) for key, value in raw_owner_index.items()}
            if isinstance(raw_owner_index, dict)
            else fallback_owner_index
        )

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
