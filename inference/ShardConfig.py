from dataclasses import dataclass
from enum import Enum
from typing import List, Optional


class ModelFamily(Enum):
    QWEN = "qwen"
    LLAMA = "llama"


SUPPORTED_DTYPES = frozenset({"float16", "bfloat16", "float32"})


@dataclass(frozen=True)
class ShardConfig:
    model_id: str
    family: ModelFamily
    start_layer: int
    end_layer: int
    total_layers: int
    dtype: Optional[str] = None

    def __post_init__(self):
        if self.end_layer <= self.start_layer:
            raise ValueError(
                f"end_layer ({self.end_layer}) must be > start_layer ({self.start_layer})"
            )
        if self.end_layer > self.total_layers:
            raise ValueError(
                f"end_layer ({self.end_layer}) must be <= total_layers ({self.total_layers})"
            )
        if self.dtype is not None and self.dtype not in SUPPORTED_DTYPES:
            raise ValueError(
                f"dtype ({self.dtype}) must be one of {sorted(SUPPORTED_DTYPES)}"
            )

    @property
    def num_layers(self) -> int:
        return self.end_layer - self.start_layer

    @property
    def is_first_shard(self) -> bool:
        return self.start_layer == 0

    @property
    def is_last_shard(self) -> bool:
        return self.end_layer == self.total_layers

    @staticmethod
    def plan_split(
        model_id: str,
        family: ModelFamily,
        total_layers: int,
        num_nodes: int,
        dtype: Optional[str] = None,
    ) -> List["ShardConfig"]:
        base = total_layers // num_nodes
        remainder = total_layers % num_nodes
        plans = []
        cursor = 0
        for i in range(num_nodes):
            size = base + (1 if i < remainder else 0)
            plans.append(
                ShardConfig(
                    model_id=model_id,
                    family=family,
                    start_layer=cursor,
                    end_layer=cursor + size,
                    total_layers=total_layers,
                    dtype=dtype,
                )
            )
            cursor += size
        return plans
