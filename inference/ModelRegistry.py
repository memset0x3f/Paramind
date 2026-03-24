from dataclasses import dataclass
from typing import Dict, List, Optional
from transformers import AutoConfig

from inference.ShardConfig import ModelFamily


@dataclass
class ModelArchInfo:
    model_id: str
    family: ModelFamily
    total_layers: int
    hidden_size: int
    embed_key: str
    norm_key: str
    lm_head_key: str
    layer_prefix: str
    rotary_key: Optional[str]

    def layer_weight_prefixes(self, start: int, end: int) -> List[str]:
        return [f"{self.layer_prefix}.{i}." for i in range(start, end)]


# Known configs (avoid downloading config every time during tests)
_KNOWN_CONFIGS: Dict[str, Dict] = {
    "Qwen/Qwen2.5-0.5B-Instruct": {"layers": 24, "hidden": 896},
    "Qwen/Qwen2.5-1.5B-Instruct": {"layers": 28, "hidden": 1536},
    "Qwen/Qwen2.5-14B-Instruct": {"layers": 48, "hidden": 5120},
    "Qwen/Qwen2.5-32B-Instruct": {"layers": 64, "hidden": 5120},
    "Qwen/Qwen2.5-72B-Instruct": {"layers": 80, "hidden": 8192},
    "meta-llama/Llama-3.1-1B-Instruct": {"layers": 16, "hidden": 2048},
    "meta-llama/Llama-3.1-8B-Instruct": {"layers": 32, "hidden": 4096},
    "meta-llama/Llama-3.1-70B-Instruct": {"layers": 80, "hidden": 8192},
    "meta-llama/Llama-3.2-1B-Instruct": {"layers": 16, "hidden": 2048},
    "meta-llama/Llama-3.2-3B-Instruct": {"layers": 28, "hidden": 3072},
}

_FAMILY_DEFAULTS = {
    ModelFamily.QWEN: {
        "embed_key": "model.embed_tokens",
        "norm_key": "model.norm",
        "lm_head_key": "lm_head",
        "layer_prefix": "model.layers",
        "rotary_key": "model.rotary_emb",
    },
    ModelFamily.LLAMA: {
        "embed_key": "model.embed_tokens",
        "norm_key": "model.norm",
        "lm_head_key": "lm_head",
        "layer_prefix": "model.layers",
        "rotary_key": None,  # RoPE is computed inline in Llama attention
    },
}


class ModelRegistry:
    @staticmethod
    def get(family: ModelFamily, model_id: str) -> ModelArchInfo:
        known = _KNOWN_CONFIGS.get(model_id)
        if known:
            total_layers = known["layers"]
            hidden_size = known["hidden"]
        else:
            config = AutoConfig.from_pretrained(model_id, trust_remote_code=True)
            total_layers = config.num_hidden_layers
            hidden_size = config.hidden_size

        defaults = _FAMILY_DEFAULTS[family]
        return ModelArchInfo(
            model_id=model_id,
            family=family,
            total_layers=total_layers,
            hidden_size=hidden_size,
            **defaults,
        )
