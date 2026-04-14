from dataclasses import dataclass, field
from typing import List, Optional


@dataclass(frozen=True)
class NodeProfile:
    node_id: str
    host: str
    device: str
    total_memory_gb: float
    compute_score: float
    loaded_shards: List[tuple[int, int]] = field(default_factory=list)
    max_usable_bytes: Optional[int] = None

    def to_dict(self) -> dict:
        return {
            "node_id": self.node_id,
            "host": self.host,
            "device": self.device,
            "total_memory_gb": self.total_memory_gb,
            "compute_score": self.compute_score,
            "loaded_shards": [list(shard) for shard in self.loaded_shards],
            "max_usable_bytes": self.max_usable_bytes,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "NodeProfile":
        return cls(
            node_id=data["node_id"],
            host=data["host"],
            device=data["device"],
            total_memory_gb=data["total_memory_gb"],
            compute_score=data["compute_score"],
            loaded_shards=[tuple(shard) for shard in data.get("loaded_shards", [])],
            max_usable_bytes=data.get("max_usable_bytes"),
        )


@dataclass(frozen=True)
class ShardAssignment:
    node_id: str
    start_layer: int
    end_layer: int
    role: str = "middle"
    source_node_id: Optional[str] = None

    @property
    def num_layers(self) -> int:
        return self.end_layer - self.start_layer

    @property
    def is_first(self) -> bool:
        return self.role == "first"

    @property
    def is_last(self) -> bool:
        return self.role == "last"


@dataclass(frozen=True)
class PlacementPlan:
    model_id: str
    assignments: List[ShardAssignment]
    coordinator_id: Optional[str] = None


@dataclass(frozen=True)
class NodeReconfigurationAction:
    node_id: str
    action: str
    start_layer: int
    end_layer: int
    role: str = "middle"
    from_node_id: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "node_id": self.node_id,
            "action": self.action,
            "start_layer": self.start_layer,
            "end_layer": self.end_layer,
            "role": self.role,
            "from_node_id": self.from_node_id,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "NodeReconfigurationAction":
        return cls(
            node_id=data["node_id"],
            action=data["action"],
            start_layer=data["start_layer"],
            end_layer=data["end_layer"],
            role=data.get("role", "middle"),
            from_node_id=data.get("from_node_id"),
        )


@dataclass(frozen=True)
class ReconfigurationPlan:
    model_id: str
    coordinator_id: Optional[str]
    actions_by_node: dict[str, list[NodeReconfigurationAction]]
