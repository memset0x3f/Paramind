# ParaMind 15-Day Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build a working distributed LLM inference prototype with chat UI, supporting 2 model families x 3 sizes, with lazy-loading layer splitting and streaming output.

**Architecture:** Replace the current `QwenSlice` (loads full model then trims) with a generic `ShardLoader` that reads only needed layers from safetensors files without ever materializing the full model. The Flask backend exposes an SSE streaming endpoint that bridges the Electron chat frontend to the P2P inference pipeline. A `ShardRegistry` tracks which node holds which layers, enabling dynamic route assembly.

**Tech Stack:** Python 3.12, PyTorch (MPS/CPU), HuggingFace transformers + safetensors, Flask + SSE, Electron, WebSocket (P2P signaling)

**Scope:** User owns frontend + backend + model. P2P layer (hole-punching, signaling server) is teammate's responsibility and treated as a given interface.

**Hardware reference:** M4 Max 36GB unified memory. "Large" models (32B/70B FP16) exceed single-node capacity, proving distributed inference necessity.

**Model matrix:**

| | Qwen2.5 | Llama 3.x |
|---|---|---|
| Small | 0.5B | 3.2-1B (1.2B params, ~2.3GB) |
| Medium | 14B | 3.1-8B (8B params, ~16GB) |
| Large | 32B / 72B | 3.1-70B (70B params, ~140GB) |

> Qwen: fully open, no auth needed. Llama: requires HF_TOKEN (set `export HF_TOKEN=...`).
> DeepSeek was removed from the matrix — V2-Lite is 16B MoE total (32GB FP16), not the 2.9B "Small" category as originally planned. It is too large to serve as a quick-validation peer model. Replaced with Llama 3.2-1B (~2GB).

---

## Timeline Overview

| Days | Phase | What |
|---|---|---|
| 1-4 | Model Layer | Generic lazy-loading shard system for Qwen2.5 + Llama3 |
| 5-7 | Backend + Inference Pipeline | Flask SSE API, InferenceCoordinator, streaming token loop |
| 8-10 | Frontend | Real API integration, streaming display, diagnostic panel |
| 11-13 | Integration + Experiments | End-to-end tests, run 6 models, collect metrics |
| 14-15 | Polish + Demo | Churn simulation, demo prep, report data |

---

## Phase 1: Model Layer (Day 1-4)

### Task 1: Shard Metadata and Configuration (Day 1 morning)

Define a standard way to describe a model shard: which model, which layers, what format.

**Files:**
- Create: `inference/ShardConfig.py`
- Create: `test/inference/test_shard_config.py`

**Step 1: Write the failing test**

```python
# test/inference/test_shard_config.py
import pytest
from inference.ShardConfig import ShardConfig, ModelFamily


def test_shard_config_creation():
    cfg = ShardConfig(
        model_id="Qwen/Qwen2.5-1.5B-Instruct",
        family=ModelFamily.QWEN,
        start_layer=0,
        end_layer=14,
        total_layers=28,
        dtype="float16",
    )
    assert cfg.start_layer == 0
    assert cfg.end_layer == 14
    assert cfg.num_layers == 14
    assert cfg.is_first_shard is True
    assert cfg.is_last_shard is False


def test_shard_config_last_shard():
    cfg = ShardConfig(
        model_id="Qwen/Qwen2.5-1.5B-Instruct",
        family=ModelFamily.QWEN,
        start_layer=14,
        end_layer=28,
        total_layers=28,
        dtype="float16",
    )
    assert cfg.is_first_shard is False
    assert cfg.is_last_shard is True


def test_shard_config_validation():
    with pytest.raises(ValueError):
        ShardConfig(
            model_id="Qwen/Qwen2.5-1.5B-Instruct",
            family=ModelFamily.QWEN,
            start_layer=14,
            end_layer=10,  # end < start
            total_layers=28,
            dtype="float16",
        )


def test_split_plan_even():
    """Split a 28-layer model across 2 nodes."""
    plans = ShardConfig.plan_split(
        model_id="Qwen/Qwen2.5-1.5B-Instruct",
        family=ModelFamily.QWEN,
        total_layers=28,
        num_nodes=2,
        dtype="float16",
    )
    assert len(plans) == 2
    assert plans[0].start_layer == 0
    assert plans[0].end_layer == 14
    assert plans[1].start_layer == 14
    assert plans[1].end_layer == 28


def test_split_plan_uneven():
    """Split a 24-layer model across 5 nodes."""
    plans = ShardConfig.plan_split(
        model_id="test",
        family=ModelFamily.QWEN,
        total_layers=24,
        num_nodes=5,
        dtype="float16",
    )
    assert len(plans) == 5
    assert plans[0].start_layer == 0
    assert plans[-1].end_layer == 24
    # all layers covered, no gaps
    for i in range(len(plans) - 1):
        assert plans[i].end_layer == plans[i + 1].start_layer
```

**Step 2: Run test to verify it fails**

Run: `pytest test/inference/test_shard_config.py -v`
Expected: FAIL with "ModuleNotFoundError: No module named 'inference.ShardConfig'"

**Step 3: Write minimal implementation**

```python
# inference/ShardConfig.py
from dataclasses import dataclass
from enum import Enum
from typing import List


class ModelFamily(Enum):
    QWEN = "qwen"
    LLAMA = "llama"


@dataclass(frozen=True)
class ShardConfig:
    model_id: str
    family: ModelFamily
    start_layer: int
    end_layer: int
    total_layers: int
    dtype: str

    def __post_init__(self):
        if self.end_layer <= self.start_layer:
            raise ValueError(
                f"end_layer ({self.end_layer}) must be > start_layer ({self.start_layer})"
            )
        if self.end_layer > self.total_layers:
            raise ValueError(
                f"end_layer ({self.end_layer}) must be <= total_layers ({self.total_layers})"
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
        dtype: str,
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
```

**Step 4: Run test to verify it passes**

Run: `pytest test/inference/test_shard_config.py -v`
Expected: All 5 tests PASS

**Step 5: Commit**

```bash
git add inference/ShardConfig.py test/inference/test_shard_config.py
git commit -m "feat: add ShardConfig with split planning and validation"
```

---

### Task 2: Model Family Registry (Day 1 afternoon)

Map each model family to its internal layer structure names, so the shard loader knows which safetensors keys to load.

**Files:**
- Create: `inference/ModelRegistry.py`
- Create: `test/inference/test_model_registry.py`

**Step 1: Write the failing test**

```python
# test/inference/test_model_registry.py
from inference.ModelRegistry import ModelRegistry, ModelFamily


def test_qwen_registry():
    info = ModelRegistry.get(ModelFamily.QWEN, "Qwen/Qwen2.5-1.5B-Instruct")
    assert info.total_layers == 28
    assert info.embed_key == "model.embed_tokens"
    assert info.norm_key == "model.norm"
    assert info.lm_head_key == "lm_head"
    assert info.layer_prefix == "model.layers"
    assert info.rotary_key == "model.rotary_emb"


def test_llama_registry():
    info = ModelRegistry.get(ModelFamily.LLAMA, "meta-llama/Llama-3.2-1B-Instruct")
    assert info.total_layers == 16
    assert info.embed_key == "model.embed_tokens"
    assert info.norm_key == "model.norm"
    assert info.lm_head_key == "lm_head"
    assert info.layer_prefix == "model.layers"
    assert info.rotary_key is None  # Llama uses inline RoPE (no rotary_emb module)


def test_layer_key_pattern():
    info = ModelRegistry.get(ModelFamily.QWEN, "Qwen/Qwen2.5-1.5B-Instruct")
    keys = info.layer_weight_prefixes(start=0, end=3)
    assert "model.layers.0." in keys[0]
    assert "model.layers.2." in keys[-1]
    assert len(keys) == 3
```

**Step 2: Run test to verify it fails**

Run: `pytest test/inference/test_model_registry.py -v`

**Step 3: Write minimal implementation**

```python
# inference/ModelRegistry.py
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
    "meta-llama/Llama-3.2-1B-Instruct": {"layers": 16, "hidden": 2048},
    "meta-llama/Llama-3.2-3B-Instruct": {"layers": 28, "hidden": 3072},
    "meta-llama/Llama-3.1-8B-Instruct": {"layers": 32, "hidden": 4096},
    "meta-llama/Llama-3.1-70B-Instruct": {"layers": 80, "hidden": 8192},
}

_FAMILY_DEFAULTS = {
    ModelFamily.QWEN: {
        "embed_key": "model.embed_tokens",
        "norm_key": "model.norm",
        "lm_head_key": "lm_head",
        "layer_prefix": "model.layers",
        "rotary_key": "model.rotary_emb",  # Qwen has separate rotary_emb module
    },
    ModelFamily.LLAMA: {
        "embed_key": "model.embed_tokens",
        "norm_key": "model.norm",
        "lm_head_key": "lm_head",
        "layer_prefix": "model.layers",
        "rotary_key": None,  # Llama uses inline RoPE (no rotary_emb module)
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
```

**Step 4: Run, verify, commit**

```bash
pytest test/inference/test_model_registry.py -v
git add inference/ModelRegistry.py test/inference/test_model_registry.py
git commit -m "feat: add ModelRegistry mapping model families to arch info"
```

---

### Task 3: SafetensorsShardLoader - Lazy Layer Loading (Day 2)

**This is the critical component.** Load only the layers [start, end) from safetensors files on disk, never materializing the full model.

> **Status update (2026-03-24):**
> - **Task 3 (Qwen path): functionally complete**
> - Verified locally with Python 3.11:
>   - `test/inference/test_shard_config.py` ✅
>   - `test/inference/test_model_registry.py` ✅
>   - `test/inference/test_shard_loader.py` ✅
> - **Task 4 still pending** for Llama verification (gated download not yet fully exercised in this session).

> **Implementation note vs original plan:**
> - The original plan assumes every component can be extracted from the meta shell and moved with `.to(device)`.
> - In practice, Qwen's model-level `rotary_emb` is a **computed module without safetensors weights**. Moving it out of the meta shell with `.to(device)` triggers a meta-tensor error.
> - Final implementation keeps the lazy-loading design for all weight-bearing modules, but treats `rotary_emb` as a special case:
>   - layers / embed / norm / lm_head → still assembled from `meta` + `load_state_dict(assign=True)`
>   - `rotary_emb` → rebuilt directly on the target device from `model_config`
> - This preserves the core invariant: **slice first, then only load the owned shard onto the node**.

**Files:**
- Create: `inference/ShardLoader.py`
- Create: `test/inference/test_shard_loader.py`

**Step 1: Write the failing test**

```python
# test/inference/test_shard_loader.py
import pytest
import torch
from inference.ShardLoader import ShardLoader
from inference.ShardConfig import ShardConfig, ModelFamily


@pytest.fixture
def small_qwen_first_shard():
    """First half of Qwen2.5-0.5B for fast testing."""
    return ShardConfig(
        model_id="Qwen/Qwen2.5-0.5B-Instruct",
        family=ModelFamily.QWEN,
        start_layer=0,
        end_layer=12,
        total_layers=24,
        dtype="float16",
    )


@pytest.fixture
def small_qwen_last_shard():
    """Second half of Qwen2.5-0.5B for fast testing."""
    return ShardConfig(
        model_id="Qwen/Qwen2.5-0.5B-Instruct",
        family=ModelFamily.QWEN,
        start_layer=12,
        end_layer=24,
        total_layers=24,
        dtype="float16",
    )


@pytest.mark.slow
def test_load_first_shard(small_qwen_first_shard):
    """First shard should have embed_tokens, layers 0-11, NO lm_head."""
    loader = ShardLoader(small_qwen_first_shard, device="cpu")
    shard = loader.load()

    assert shard.embed_tokens is not None
    assert len(shard.layers) == 12
    assert shard.lm_head is None
    assert shard.norm is None  # norm only on last shard


@pytest.mark.slow
def test_load_last_shard(small_qwen_last_shard):
    """Last shard should have layers 12-23, norm, lm_head, NO embed_tokens."""
    loader = ShardLoader(small_qwen_last_shard, device="cpu")
    shard = loader.load()

    assert shard.embed_tokens is None
    assert len(shard.layers) == 12
    assert shard.lm_head is not None
    assert shard.norm is not None


@pytest.mark.slow
def test_forward_first_shard(small_qwen_first_shard):
    """First shard takes input_ids and returns hidden_states."""
    loader = ShardLoader(small_qwen_first_shard, device="cpu")
    shard = loader.load()

    input_ids = torch.tensor([[1, 2, 3]], dtype=torch.long)
    hidden, kv = shard.forward(input_ids)

    # hidden_states shape: [batch, seq_len, hidden_size]
    assert hidden.shape[0] == 1
    assert hidden.shape[1] == 3
    assert hidden.dtype == torch.float16


@pytest.mark.slow
def test_two_shard_pipeline(small_qwen_first_shard, small_qwen_last_shard):
    """Two shards chained produce logits."""
    shard1 = ShardLoader(small_qwen_first_shard, device="cpu").load()
    shard2 = ShardLoader(small_qwen_last_shard, device="cpu").load()

    input_ids = torch.tensor([[1, 2, 3]], dtype=torch.long)
    hidden, kv1 = shard1.forward(input_ids)
    logits, kv2 = shard2.forward(hidden)

    # logits shape: [batch, seq_len, vocab_size]
    assert logits.shape[0] == 1
    assert logits.shape[1] == 3
    assert logits.shape[2] > 100  # vocab size
```

**Step 2: Run test to verify it fails**

Run: `pytest test/inference/test_shard_loader.py -v -m slow`

**Step 3: Write implementation**

Key insight: use `safetensors.torch.load_file()` for each relevant shard file,
filtered by layer prefix. Parse `model.safetensors.index.json` to know which
weights are in which file.

```python
# inference/ShardLoader.py
import json
import os
from pathlib import Path
from typing import Optional, Tuple

import torch
import torch.nn as nn
from safetensors.torch import load_file
from transformers import AutoConfig

from inference.ShardConfig import ShardConfig, ModelFamily
from inference.ModelRegistry import ModelRegistry


class ModelShard(nn.Module):
    """A loaded model shard ready for forward pass."""

    def __init__(
        self,
        config: ShardConfig,
        layers: nn.ModuleList,
        embed_tokens: Optional[nn.Module],
        norm: Optional[nn.Module],
        lm_head: Optional[nn.Module],
        rotary_emb: Optional[nn.Module],
        model_config,
    ):
        super().__init__()
        self.shard_config = config
        self.layers = layers
        self.embed_tokens = embed_tokens
        self.norm = norm
        self.lm_head = lm_head
        self.rotary_emb = rotary_emb
        self.model_config = model_config

    def forward(
        self, x: torch.Tensor, past_key_values=None
    ) -> Tuple[torch.Tensor, Optional[object]]:
        from transformers.models.qwen2.modeling_qwen2 import Qwen2DecoderLayer
        from transformers.cache_utils import DynamicCache

        if past_key_values is None:
            past_key_values = DynamicCache()

        if self.shard_config.is_first_shard:
            hidden_states = self.embed_tokens(x)
        else:
            hidden_states = x

        # Compute position ids
        seq_len = hidden_states.shape[1]
        past_len = past_key_values.get_seq_length() if past_key_values else 0
        position_ids = torch.arange(
            past_len, past_len + seq_len, device=hidden_states.device
        ).unsqueeze(0)

        # Forward through layers
        for layer in self.layers:
            outputs = layer(
                hidden_states,
                position_ids=position_ids,
                past_key_value=past_key_values,
                use_cache=True,
            )
            hidden_states = outputs[0]

        if self.shard_config.is_last_shard and self.norm is not None:
            hidden_states = self.norm(hidden_states)

        if self.lm_head is not None:
            logits = self.lm_head(hidden_states)
            return logits, past_key_values

        return hidden_states, past_key_values


class ShardLoader:
    """Loads only the needed layers from safetensors files."""

    def __init__(self, config: ShardConfig, device: str = "cpu"):
        self.config = config
        self.device = device
        self.arch_info = ModelRegistry.get(config.family, config.model_id)

    def load(self) -> ModelShard:
        model_config = AutoConfig.from_pretrained(
            self.config.model_id, trust_remote_code=True
        )
        model_path = self._resolve_model_path()
        weight_map = self._parse_weight_index(model_path)

        # Determine which weight keys we need
        needed_prefixes = []
        for i in range(self.config.start_layer, self.config.end_layer):
            needed_prefixes.append(f"{self.arch_info.layer_prefix}.{i}.")

        if self.config.is_first_shard:
            needed_prefixes.append(f"{self.arch_info.embed_key}.")
            if self.arch_info.rotary_key:
                needed_prefixes.append(f"{self.arch_info.rotary_key}.")

        if self.config.is_last_shard:
            needed_prefixes.append(f"{self.arch_info.norm_key}.")
            needed_prefixes.append(f"{self.arch_info.lm_head_key}.")

        # Group needed keys by shard file
        files_to_keys = {}
        for key, shard_file in weight_map.items():
            if any(key.startswith(p) for p in needed_prefixes):
                files_to_keys.setdefault(shard_file, []).append(key)

        # Load only needed tensors
        state_dict = {}
        for shard_file, keys in files_to_keys.items():
            full_path = os.path.join(model_path, shard_file)
            shard_weights = load_file(full_path, device=self.device)
            for k in keys:
                if k in shard_weights:
                    state_dict[k] = shard_weights[k]

        # Build the shard modules
        return self._assemble_shard(model_config, state_dict)

    def _resolve_model_path(self) -> str:
        """Find model on disk (HuggingFace cache or modelscope)."""
        from huggingface_hub import snapshot_download

        return snapshot_download(
            self.config.model_id,
            allow_patterns=["*.safetensors", "*.json", "*.txt"],
        )

    def _parse_weight_index(self, model_path: str) -> dict:
        """Parse model.safetensors.index.json to get key -> file mapping."""
        index_path = os.path.join(model_path, "model.safetensors.index.json")

        if os.path.exists(index_path):
            with open(index_path) as f:
                index = json.load(f)
            return index["weight_map"]

        # Single-file model
        for fname in os.listdir(model_path):
            if fname.endswith(".safetensors"):
                all_keys = load_file(
                    os.path.join(model_path, fname), device="meta"
                ).keys()
                return {k: fname for k in all_keys}

        raise FileNotFoundError(f"No safetensors files found in {model_path}")

    def _assemble_shard(self, model_config, state_dict: dict) -> ModelShard:
        """Build nn.Modules from loaded weights."""
        from transformers import AutoModelForCausalLM

        # Create empty model shell to get layer architecture
        with torch.device("meta"):
            shell = AutoModelForCausalLM.from_config(
                model_config, torch_dtype=torch.float16
            )

        # Extract and load layers
        layers = nn.ModuleList()
        for i in range(self.config.start_layer, self.config.end_layer):
            layer = shell.model.layers[i]
            layer_prefix = f"{self.arch_info.layer_prefix}.{i}."
            layer_state = {
                k.removeprefix(layer_prefix): v
                for k, v in state_dict.items()
                if k.startswith(layer_prefix)
            }
            layer.load_state_dict(layer_state, assign=True)
            layer = layer.to(self.device)
            layers.append(layer)

        # Embed tokens (first shard only)
        embed_tokens = None
        if self.config.is_first_shard:
            embed_tokens = shell.model.embed_tokens
            embed_state = {
                k.removeprefix(f"{self.arch_info.embed_key}."): v
                for k, v in state_dict.items()
                if k.startswith(f"{self.arch_info.embed_key}.")
            }
            embed_tokens.load_state_dict(embed_state, assign=True)
            embed_tokens = embed_tokens.to(self.device)

        # Rotary embedding
        rotary_emb = None
        if self.arch_info.rotary_key:
            rotary_prefix = f"{self.arch_info.rotary_key}."
            rotary_state = {
                k.removeprefix(rotary_prefix): v
                for k, v in state_dict.items()
                if k.startswith(rotary_prefix)
            }
            if rotary_state:
                rotary_emb = shell.model.rotary_emb
                rotary_emb.load_state_dict(rotary_state, assign=True)
                rotary_emb = rotary_emb.to(self.device)

        # Norm + LM head (last shard only)
        norm = None
        lm_head = None
        if self.config.is_last_shard:
            norm_prefix = f"{self.arch_info.norm_key}."
            norm_state = {
                k.removeprefix(norm_prefix): v
                for k, v in state_dict.items()
                if k.startswith(norm_prefix)
            }
            norm = shell.model.norm
            norm.load_state_dict(norm_state, assign=True)
            norm = norm.to(self.device)

            head_prefix = f"{self.arch_info.lm_head_key}."
            head_state = {
                k.removeprefix(head_prefix): v
                for k, v in state_dict.items()
                if k.startswith(head_prefix)
            }
            lm_head = shell.lm_head
            lm_head.load_state_dict(head_state, assign=True)
            lm_head = lm_head.to(self.device)

        del shell
        return ModelShard(
            config=self.config,
            layers=layers,
            embed_tokens=embed_tokens,
            norm=norm,
            lm_head=lm_head,
            rotary_emb=rotary_emb,
            model_config=model_config,
        )
```

**Step 4: Run tests, commit**

```bash
pytest test/inference/test_shard_loader.py -v -m slow
git add inference/ShardLoader.py test/inference/test_shard_loader.py
git commit -m "feat: add ShardLoader with safetensors lazy layer loading"
```

**Important notes for implementer:**
- `torch.device("meta")` creates modules without allocating memory - critical for 70B models
- `load_state_dict(..., assign=True)` replaces meta tensors with real data in-place (PyTorch 2.1+)
- The `_resolve_model_path` uses `huggingface_hub.snapshot_download` which only downloads needed files
- For Llama3 specifically: RoPE is computed inside each attention layer, not as a separate `rotary_emb` module. The registry already handles this by setting `rotary_key=None`
- Update `requirements.txt`: add `safetensors>=0.4.0`, `huggingface_hub>=0.20.0`

---

### Task 4: Verify Llama + Update Old Code (Day 3)

**Files:**
- Create: `test/inference/test_llama_shard.py`
- Modify: `inference/__init__.py`
- Modify: `requirements.txt`

**Step 1: Write Llama integration test**

```python
# test/inference/test_llama_shard.py
import pytest
import torch
from inference.ShardLoader import ShardLoader
from inference.ShardConfig import ShardConfig, ModelFamily

def _hf_token_available():
    import os
    return bool(os.environ.get("HF_TOKEN"))

requires_hf_token = pytest.mark.skipif(
    not _hf_token_available(),
    reason="Llama requires HF_TOKEN for gated repo access"
)

@requires_hf_token
@pytest.mark.slow
def test_llama_two_shard_pipeline():
    """Llama 3.2-1B (16 layers) split into 2 shards produces valid logits."""
    shard1_cfg = ShardConfig(
        model_id="meta-llama/Llama-3.2-1B-Instruct",
        family=ModelFamily.LLAMA,
        start_layer=0,
        end_layer=8,
        total_layers=16,
        dtype="float16",
    )
    shard2_cfg = ShardConfig(
        model_id="meta-llama/Llama-3.2-1B-Instruct",
        family=ModelFamily.LLAMA,
        start_layer=8,
        end_layer=16,
        total_layers=16,
        dtype="float16",
    )

    shard1 = ShardLoader(shard1_cfg, device="cpu").load()
    shard2 = ShardLoader(shard2_cfg, device="cpu").load()

    input_ids = torch.tensor([[1, 2, 3]], dtype=torch.long)
    hidden, kv1 = shard1.forward(input_ids)
    logits, kv2 = shard2.forward(hidden)

    assert logits.ndim == 3
    assert logits.shape[2] > 100  # vocab size
```

**Step 2: Write same correctness test for Qwen**

```python
# Add to test/inference/test_shard_loader.py

@pytest.mark.slow
def test_qwen_output_matches_reference():
    """Distributed output matches single-model output for Qwen2.5-0.5B."""
    from transformers import AutoModelForCausalLM

    model_id = "Qwen/Qwen2.5-0.5B-Instruct"

    ref_model = AutoModelForCausalLM.from_pretrained(model_id).to("cpu")
    input_ids = torch.tensor([[1, 2, 3]], dtype=torch.long)
    with torch.no_grad():
        ref_logits = ref_model(input_ids).logits

    s1 = ShardLoader(
        ShardConfig(model_id, ModelFamily.QWEN, 0, 12, 24, "bfloat16"), "cpu"
    ).load()
    s2 = ShardLoader(
        ShardConfig(model_id, ModelFamily.QWEN, 12, 24, 24, "bfloat16"), "cpu"
    ).load()

    with torch.no_grad():
        h, _ = s1.forward(input_ids)
        dist_logits, _ = s2.forward(h)

    assert torch.allclose(ref_logits, dist_logits, atol=1e-2)
```

**Step 3: Update inference/__init__.py**

```python
# inference/__init__.py
from .ShardConfig import ShardConfig, ModelFamily
from .ShardLoader import ShardLoader, ModelShard
from .ModelRegistry import ModelRegistry
```

**Step 4: Update requirements.txt**

```
torch>=2.2.0
transformers>=4.38.0
safetensors>=0.4.0
huggingface_hub>=0.20.0
modelscope==1.33.0
torchinfo==1.8.0
pystun3==2.0.0
toml==0.10.2
websocket_client==1.8.0
flask>=2.3.0
flask-cors>=4.0.0
```

**Step 5: Run all tests, commit**

```bash
HF_TOKEN=your_token pytest test/inference/ -v -m slow
git add inference/ test/inference/ requirements.txt
git commit -m "feat: verify Llama3 + Qwen shard loading, update requirements"
```

---

### Task 5: Streaming Inference Engine (Day 4)

Single-node or multi-node streaming token generation. This is what the backend calls.

**Files:**
- Create: `inference/InferenceEngine.py`
- Create: `test/inference/test_inference_engine.py`

**Step 1: Write the failing test**

```python
# test/inference/test_inference_engine.py
import pytest
from inference.InferenceEngine import LocalInferenceEngine
from inference.ShardConfig import ShardConfig, ModelFamily


@pytest.mark.slow
def test_local_inference_streaming():
    """Single-node inference produces streaming tokens."""
    engine = LocalInferenceEngine(
        model_id="Qwen/Qwen2.5-0.5B-Instruct",
        family=ModelFamily.QWEN,
        device="cpu",
    )

    tokens = list(engine.generate_stream("Hello", max_tokens=5))

    assert len(tokens) >= 1
    assert all(isinstance(t, str) for t in tokens)


@pytest.mark.slow
def test_local_inference_full_response():
    """Can collect full response."""
    engine = LocalInferenceEngine(
        model_id="Qwen/Qwen2.5-0.5B-Instruct",
        family=ModelFamily.QWEN,
        device="cpu",
    )

    response = engine.generate("Why is sky blue?", max_tokens=20)

    assert isinstance(response, str)
    assert len(response) > 0
```

**Step 3: Write implementation**

```python
# inference/InferenceEngine.py
from typing import Generator, List, Optional

import torch
from transformers import AutoTokenizer

from inference.ShardConfig import ShardConfig, ModelFamily
from inference.ShardLoader import ShardLoader, ModelShard
from inference.ModelRegistry import ModelRegistry


class LocalInferenceEngine:
    """Full model on single node, with streaming token generation."""

    def __init__(
        self,
        model_id: str,
        family: ModelFamily,
        device: str = "cpu",
    ):
        self.model_id = model_id
        self.family = family
        self.device = device

        arch_info = ModelRegistry.get(family, model_id)

        # Load full model as single shard
        cfg = ShardConfig(
            model_id=model_id,
            family=family,
            start_layer=0,
            end_layer=arch_info.total_layers,
            total_layers=arch_info.total_layers,
            dtype="float16",
        )
        self.shard = ShardLoader(cfg, device=device).load()
        self.tokenizer = AutoTokenizer.from_pretrained(
            model_id, trust_remote_code=True
        )

    def generate_stream(
        self,
        prompt: str,
        max_tokens: int = 256,
        temperature: float = 0.0,
        system_prompt: str = "You are a helpful assistant.",
    ) -> Generator[str, None, None]:
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ]
        text = self.tokenizer.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True
        )
        input_ids = self.tokenizer(text, return_tensors="pt").input_ids
        input_ids = input_ids.to(self.device)

        kv_cache = None
        eos_ids = set(self.tokenizer.all_special_ids)
        # Add common EOS tokens
        if self.tokenizer.eos_token_id is not None:
            eos_ids.add(self.tokenizer.eos_token_id)

        with torch.no_grad():
            for _ in range(max_tokens):
                output, kv_cache = self.shard.forward(
                    input_ids, past_key_values=kv_cache
                )

                next_logits = output[:, -1, :]

                if temperature <= 0:
                    next_id = torch.argmax(next_logits, dim=-1).unsqueeze(0)
                else:
                    probs = torch.softmax(next_logits / temperature, dim=-1)
                    next_id = torch.multinomial(probs, num_samples=1)

                token_id = next_id.item()
                if token_id in eos_ids:
                    break

                word = self.tokenizer.decode([token_id])
                yield word

                input_ids = next_id

    def generate(
        self,
        prompt: str,
        max_tokens: int = 256,
        temperature: float = 0.0,
    ) -> str:
        return "".join(self.generate_stream(prompt, max_tokens, temperature))
```

**Step 4: Run, commit**

```bash
pytest test/inference/test_inference_engine.py -v -m slow
git add inference/InferenceEngine.py test/inference/test_inference_engine.py
git commit -m "feat: add LocalInferenceEngine with streaming token generation"
```

---

## Phase 2: Backend API + Bridge (Day 5-7)

### Task 6: Flask SSE Streaming Endpoint (Day 5)

Replace the placeholder `backend.py` with a real inference-backed API.

**Files:**
- Rewrite: `paramind/apps/desktop/python/backend.py`
- Create: `test/backend/test_api.py`

**Step 1: Write the failing test**

```python
# test/backend/test_api.py
import json
import pytest
from paramind.apps.desktop.python.backend import create_app


@pytest.fixture
def client():
    app = create_app(test_mode=True)
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def test_health(client):
    resp = client.get("/api/health")
    data = resp.get_json()
    assert resp.status_code == 200
    assert data["status"] == "healthy"


def test_infer_missing_prompt(client):
    resp = client.post("/api/infer", json={})
    assert resp.status_code == 400


def test_infer_returns_sse(client):
    resp = client.post(
        "/api/infer",
        json={"prompt": "Hello"},
        headers={"Accept": "text/event-stream"},
    )
    assert resp.status_code == 200
    assert "text/event-stream" in resp.content_type


def test_status_endpoint(client):
    resp = client.get("/api/status")
    data = resp.get_json()
    assert resp.status_code == 200
    assert "model_id" in data
    assert "mode" in data
```

**Step 3: Write implementation**

```python
# paramind/apps/desktop/python/backend.py
import json
import time
import os
import sys
from datetime import datetime
from flask import Flask, Response, jsonify, request, stream_with_context
from flask_cors import CORS

# Add project root to path
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../.."))
if project_root not in sys.path:
    sys.path.insert(0, project_root)


def create_app(test_mode: bool = False):
    app = Flask(__name__)
    CORS(app)

    # Lazy-init engine to avoid loading model during import
    engine_holder = {"engine": None, "model_id": None, "family": None}

    def get_engine():
        if engine_holder["engine"] is None:
            from inference.InferenceEngine import LocalInferenceEngine
            from inference.ShardConfig import ModelFamily

            model_id = os.environ.get(
                "PARAMIND_MODEL", "Qwen/Qwen2.5-0.5B-Instruct"
            )
            family_str = os.environ.get("PARAMIND_FAMILY", "qwen")
            family = ModelFamily(family_str)
            device = os.environ.get("PARAMIND_DEVICE", "cpu")

            if not test_mode:
                engine_holder["engine"] = LocalInferenceEngine(
                    model_id=model_id, family=family, device=device
                )
            engine_holder["model_id"] = model_id
            engine_holder["family"] = family_str

        return engine_holder["engine"]

    @app.route("/api/health", methods=["GET"])
    def health():
        return jsonify(
            {
                "status": "healthy",
                "timestamp": datetime.now().isoformat(),
            }
        )

    @app.route("/api/status", methods=["GET"])
    def status():
        return jsonify(
            {
                "model_id": engine_holder.get("model_id")
                or os.environ.get("PARAMIND_MODEL", "Qwen/Qwen2.5-0.5B-Instruct"),
                "mode": "distributed" if os.environ.get("PARAMIND_PEERS") else "local",
                "device": os.environ.get("PARAMIND_DEVICE", "cpu"),
                "family": engine_holder.get("family")
                or os.environ.get("PARAMIND_FAMILY", "qwen"),
            }
        )

    @app.route("/api/infer", methods=["POST"])
    def infer():
        data = request.get_json()
        if not data or "prompt" not in data:
            return jsonify({"error": "prompt is required"}), 400

        prompt = data["prompt"]
        max_tokens = data.get("max_tokens", 256)

        engine = get_engine()
        if engine is None and test_mode:
            # In test mode, return mock SSE stream
            def mock_stream():
                for word in ["Hello", " ", "world", "!"]:
                    yield f"data: {json.dumps({'token': word})}\n\n"
                yield f"data: {json.dumps({'done': True})}\n\n"

            return Response(
                mock_stream(), mimetype="text/event-stream"
            )

        def token_stream():
            start_time = time.time()
            token_count = 0
            for token in engine.generate_stream(prompt, max_tokens=max_tokens):
                token_count += 1
                ttft = time.time() - start_time if token_count == 1 else None
                event = {"token": token}
                if ttft is not None:
                    event["ttft"] = round(ttft, 3)
                yield f"data: {json.dumps(event)}\n\n"

            elapsed = time.time() - start_time
            yield f"data: {json.dumps({'done': True, 'tokens': token_count, 'elapsed': round(elapsed, 3), 'tps': round(token_count / max(elapsed, 0.001), 2)})}\n\n"

        return Response(
            stream_with_context(token_stream()),
            mimetype="text/event-stream",
        )

    return app


if __name__ == "__main__":
    app = create_app()
    app.run(host="0.0.0.0", port=5001, debug=False, threaded=True)
```

**Step 4: Run, commit**

```bash
pytest test/backend/test_api.py -v
git add paramind/apps/desktop/python/backend.py test/backend/test_api.py
git commit -m "feat: Flask backend with SSE streaming inference endpoint"
```

---

### Task 7: Distributed Inference Coordinator (Day 6-7)

Bridge between the inference API and P2PClient for multi-node inference.
When `PARAMIND_PEERS` is set, the coordinator routes hidden states through
the P2P network instead of running all layers locally.

**Files:**
- Create: `inference/DistributedEngine.py`
- Create: `test/inference/test_distributed_engine.py`

**Step 1: Define the interface (test first)**

```python
# test/inference/test_distributed_engine.py
import pytest
from inference.DistributedEngine import DistributedInferenceEngine
from inference.ShardConfig import ShardConfig, ModelFamily


@pytest.mark.slow
def test_distributed_two_process_simulation():
    """Simulate 2-node inference in same process."""
    engine = DistributedInferenceEngine(
        model_id="Qwen/Qwen2.5-0.5B-Instruct",
        family=ModelFamily.QWEN,
        shard_configs=[
            ShardConfig("Qwen/Qwen2.5-0.5B-Instruct", ModelFamily.QWEN, 0, 12, 24, "float16"),
            ShardConfig("Qwen/Qwen2.5-0.5B-Instruct", ModelFamily.QWEN, 12, 24, 24, "float16"),
        ],
        device="cpu",
    )

    tokens = list(engine.generate_stream("Hello", max_tokens=5))
    assert len(tokens) >= 1
```

**Step 3: Write implementation**

```python
# inference/DistributedEngine.py
from typing import Generator, List, Optional, Set
import torch
from transformers import AutoTokenizer

from inference.ShardConfig import ShardConfig, ModelFamily
from inference.ShardLoader import ShardLoader, ModelShard
from inference.ModelRegistry import ModelRegistry


class DistributedInferenceEngine:
    """
    Multi-shard inference engine.
    In simulation mode: all shards loaded locally in sequence.
    In P2P mode: only local shard loaded; remote shards use P2PClient.
    """

    def __init__(
        self,
        model_id: str,
        family: ModelFamily,
        shard_configs: List[ShardConfig],
        device: str = "cpu",
        local_shard_index: Optional[int] = None,
        p2p_client=None,
    ):
        self.model_id = model_id
        self.family = family
        self.device = device
        self.shard_configs = sorted(shard_configs, key=lambda s: s.start_layer)
        self.p2p_client = p2p_client

        # In simulation mode, load all shards locally
        if local_shard_index is None:
            self.shards = [
                ShardLoader(cfg, device=device).load() for cfg in self.shard_configs
            ]
            self.local_index = 0  # "we" are the first shard
        else:
            self.local_index = local_shard_index
            self.shards = [None] * len(self.shard_configs)
            self.shards[local_shard_index] = ShardLoader(
                self.shard_configs[local_shard_index], device=device
            ).load()

        self.tokenizer = AutoTokenizer.from_pretrained(
            model_id, trust_remote_code=True
        )

    def generate_stream(
        self,
        prompt: str,
        max_tokens: int = 256,
        temperature: float = 0.0,
        system_prompt: str = "You are a helpful assistant.",
    ) -> Generator[str, None, None]:
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ]
        text = self.tokenizer.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True
        )
        input_ids = self.tokenizer(text, return_tensors="pt").input_ids
        input_ids = input_ids.to(self.device)

        kv_caches = [None] * len(self.shard_configs)
        eos_ids: Set[int] = set()
        if self.tokenizer.eos_token_id is not None:
            eos_ids.add(self.tokenizer.eos_token_id)

        with torch.no_grad():
            for _ in range(max_tokens):
                x = input_ids
                for i, shard in enumerate(self.shards):
                    if shard is not None:
                        x, kv_caches[i] = shard.forward(x, past_key_values=kv_caches[i])
                    else:
                        # Remote shard: send via P2P, receive result
                        x, kv_caches[i] = self._remote_forward(i, x, kv_caches[i])

                # Last shard output is logits
                if temperature <= 0:
                    next_id = torch.argmax(x[:, -1, :], dim=-1).unsqueeze(0)
                else:
                    probs = torch.softmax(x[:, -1, :] / temperature, dim=-1)
                    next_id = torch.multinomial(probs, num_samples=1)

                token_id = next_id.item()
                if token_id in eos_ids:
                    break

                yield self.tokenizer.decode([token_id])
                input_ids = next_id

    def _remote_forward(self, shard_index, x, kv_cache):
        """Forward to remote peer via P2PClient."""
        if self.p2p_client is None:
            raise RuntimeError(
                f"Shard {shard_index} is remote but no P2PClient configured"
            )
        # Interface to P2PClient: send tensor, receive tensor
        # This integrates with the teammate's P2P code
        from common.Utils import sendTorchData, deserializeTorchData
        # Implementation depends on P2P teammate's interface
        raise NotImplementedError(
            "P2P remote forward - integrate with teammate's P2PClient"
        )
```

**Step 4: Run, commit**

```bash
pytest test/inference/test_distributed_engine.py -v -m slow
git add inference/DistributedEngine.py test/inference/test_distributed_engine.py
git commit -m "feat: add DistributedInferenceEngine with multi-shard pipeline"
```

---

## Phase 3: Frontend Integration (Day 8-10)

### Task 8: Connect Frontend to Real Backend (Day 8)

Replace `simulateAIReply` with real SSE API calls.

**Files:**
- Modify: `paramind/apps/desktop/renderer/chat.js`
- Modify: `paramind/apps/desktop/electron/preload.js`
- Modify: `paramind/apps/desktop/main.js`

**Step 1: Update preload to expose API base URL**

```javascript
// paramind/apps/desktop/electron/preload.js
const { contextBridge, ipcRenderer } = require('electron')

contextBridge.exposeInMainWorld('electronAPI', {
  ping: () => 'pong',
  getBackendUrl: () => 'http://localhost:5001',
})
```

**Step 2: Replace simulateAIReply in chat.js**

Find the `simulateAIReply` and `createAiDraftForConv` functions (lines 343-383)
and replace with real SSE call:

```javascript
// Replace simulateAIReply (line 343) and createAiDraftForConv (line 348) with:

function simulateAIReply(userText) {
  const prompt = userText.replace(/^\s*[@＠](?:AI助手|AI)(?:\s+|$)/i, '').trim()
  createAiDraftForConv(state.activeConvId, prompt)
}

function createAiDraftForConv(convId, prompt) {
  const safePrompt = (prompt || '').trim()
  state.aiBusy = true
  state.aiDraftByConvId[convId] = {
    prompt: safePrompt,
    text: '',
    createdAt: Date.now(),
    generating: true,
    metrics: null,
  }
  renderAll()

  const backendUrl = window.electronAPI?.getBackendUrl?.() || 'http://localhost:5001'

  fetch(`${backendUrl}/api/infer`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'Accept': 'text/event-stream' },
    body: JSON.stringify({ prompt: safePrompt, max_tokens: 256 }),
  })
    .then((resp) => {
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
      const reader = resp.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''

      function read() {
        reader.read().then(({ done, value }) => {
          if (done) {
            finishGeneration(convId)
            return
          }
          buffer += decoder.decode(value, { stream: true })
          const lines = buffer.split('\n')
          buffer = lines.pop() || ''

          for (const line of lines) {
            if (!line.startsWith('data: ')) continue
            try {
              const event = JSON.parse(line.slice(6))
              if (event.token) {
                const draft = state.aiDraftByConvId[convId]
                if (draft) {
                  draft.text += event.token
                  if (event.ttft) draft.metrics = { ttft: event.ttft }
                  renderAiCard()
                }
              }
              if (event.done) {
                const draft = state.aiDraftByConvId[convId]
                if (draft && event.tps) {
                  draft.metrics = { ...draft.metrics, tps: event.tps, tokens: event.tokens }
                }
                finishGeneration(convId)
                return
              }
            } catch {}
          }
          read()
        })
      }
      read()
    })
    .catch((err) => {
      // Fallback to mock if backend unavailable
      console.warn('Backend unavailable, using mock:', err.message)
      fallbackMockReply(convId, safePrompt)
    })
}

function finishGeneration(convId) {
  const draft = state.aiDraftByConvId[convId]
  if (draft) {
    draft.generating = false
  }
  state.aiBusy = false
  renderAll()
}

function fallbackMockReply(convId, prompt) {
  state.aiDraftByConvId[convId] = {
    prompt,
    text: `[Backend offline] Mock reply for: ${prompt}`,
    createdAt: Date.now(),
    generating: false,
  }
  state.aiBusy = false
  renderAll()
}
```

**Step 3: Update main.js to auto-start Flask backend**

```javascript
// Add to main.js, after imports:
const { spawn } = require('child_process')
const path = require('path')

let pythonProcess = null

function startBackend() {
  const pythonScript = path.join(__dirname, 'python', 'backend.py')
  // Use the venv python if available
  const venvPython = path.join(__dirname, '.venv', 'bin', 'python3')
  const pythonCmd = require('fs').existsSync(venvPython) ? venvPython : 'python3'

  pythonProcess = spawn(pythonCmd, [pythonScript], {
    env: {
      ...process.env,
      PARAMIND_MODEL: process.env.PARAMIND_MODEL || 'Qwen/Qwen2.5-0.5B-Instruct',
      PARAMIND_FAMILY: process.env.PARAMIND_FAMILY || 'qwen',
      PARAMIND_DEVICE: process.env.PARAMIND_DEVICE || 'mps',
    },
    stdio: ['pipe', 'pipe', 'pipe'],
  })

  pythonProcess.stdout.on('data', (data) => console.log(`[backend] ${data}`))
  pythonProcess.stderr.on('data', (data) => console.error(`[backend] ${data}`))
}

// In app.whenReady().then(), add before createWindow():
// startBackend()

// In app.on('window-all-closed'), add:
// if (pythonProcess) pythonProcess.kill()
```

**Step 4: Manual test**

1. Start backend: `cd paramind/apps/desktop && python3 python/backend.py`
2. Start electron: `npm start`
3. Type `@AI助手 Hello` - should see streaming response in AI card

**Step 5: Commit**

```bash
git add paramind/apps/desktop/renderer/chat.js paramind/apps/desktop/electron/preload.js paramind/apps/desktop/main.js
git commit -m "feat: connect frontend to Flask backend with SSE streaming"
```

---

### Task 9: Diagnostic Status Panel (Day 9)

Add a panel showing inference metrics (TTFT, tokens/s, active model).

**Files:**
- Modify: `paramind/apps/desktop/renderer/index.html` (add CSS for status bar)
- Modify: `paramind/apps/desktop/renderer/chat.js` (add status polling + display)

**Step 1: Add status bar HTML**

In `index.html`, add after the `<div class="topbar">...</div>` block (after line 562):

```html
<div class="status-bar" id="statusBar">
  <span id="statusModel">Model: --</span>
  <span id="statusMode">Mode: --</span>
  <span id="statusDevice">Device: --</span>
  <span id="statusTTFT">TTFT: --</span>
  <span id="statusTPS">TPS: --</span>
</div>
```

Add CSS:

```css
.status-bar {
  display: flex;
  gap: 16px;
  padding: 6px 18px;
  font-size: 11px;
  color: rgba(255,255,255,.5);
  border-bottom: 1px solid var(--line);
  background: rgba(0,0,0,.12);
}
.status-bar span { white-space: nowrap; }
```

**Step 2: Add status polling in chat.js**

```javascript
// Add at end of chat.js, inside second DOMContentLoaded:

function pollStatus() {
  const backendUrl = window.electronAPI?.getBackendUrl?.() || 'http://localhost:5001'
  fetch(`${backendUrl}/api/status`)
    .then(r => r.json())
    .then(data => {
      const el = (id) => document.getElementById(id)
      el('statusModel').textContent = `Model: ${data.model_id || '--'}`
      el('statusMode').textContent = `Mode: ${data.mode || '--'}`
      el('statusDevice').textContent = `Device: ${data.device || '--'}`
    })
    .catch(() => {
      document.getElementById('statusModel').textContent = 'Model: offline'
    })
}

// Poll every 5 seconds
pollStatus()
setInterval(pollStatus, 5000)
```

**Step 3: Show TTFT/TPS from streaming metrics**

In the `createAiDraftForConv` function, when `event.ttft` or `event.tps` arrives,
update the status bar:

```javascript
// Inside the SSE reader, after processing event.ttft:
if (event.ttft) {
  document.getElementById('statusTTFT').textContent = `TTFT: ${event.ttft}s`
}
// After event.done:
if (event.tps) {
  document.getElementById('statusTPS').textContent = `TPS: ${event.tps}`
}
```

**Step 4: Commit**

```bash
git add paramind/apps/desktop/renderer/index.html paramind/apps/desktop/renderer/chat.js
git commit -m "feat: add diagnostic status bar showing model/TTFT/TPS"
```

---

### Task 10: Route Visualization (Day 10)

Show which nodes participated in inference when in distributed mode.

**Files:**
- Modify: `paramind/apps/desktop/python/backend.py` (add route info to SSE)
- Modify: `paramind/apps/desktop/renderer/chat.js` (display route in AI card)

**Step 1: Extend SSE events with route info**

In `backend.py`, modify the `token_stream` generator to emit a `route` event
at the start:

```python
def token_stream():
    # Emit route info first
    route = {
        "type": "route",
        "nodes": [
            {"id": "local", "layers": "0-24", "device": device}
            # In distributed mode, list all peers
        ]
    }
    yield f"data: {json.dumps(route)}\n\n"

    # Then token events...
```

**Step 2: Display route in AI card title**

In `renderAiCard()` in chat.js, if `draft.route` exists, show node info:

```javascript
// After receiving a route event in the SSE reader:
if (event.type === 'route') {
  const draft = state.aiDraftByConvId[convId]
  if (draft) draft.route = event.nodes
  renderAiCard()
}

// In renderAiCard(), add route display:
const routeHtml = draft.route
  ? draft.route.map(n => `<span style="background:rgba(255,255,255,.06);padding:2px 6px;border-radius:6px;font-size:11px;">${n.id}: layers ${n.layers}</span>`).join(' → ')
  : ''
```

**Step 3: Commit**

```bash
git add paramind/apps/desktop/python/backend.py paramind/apps/desktop/renderer/chat.js
git commit -m "feat: add inference route visualization to AI card"
```

---

## Phase 4: Integration & Experiments (Day 11-13)

### Task 11: End-to-End Integration Test Script (Day 11)

**Files:**
- Create: `scripts/run_experiment.py`

```python
# scripts/run_experiment.py
"""
Run inference experiments across model configurations.

Usage:
  python scripts/run_experiment.py --model Qwen/Qwen2.5-0.5B-Instruct --family qwen --device mps
  python scripts/run_experiment.py --model Qwen/Qwen2.5-1.5B-Instruct --family qwen --num-nodes 2
"""
import argparse
import json
import time
import torch
from inference.ShardConfig import ShardConfig, ModelFamily
from inference.ShardLoader import ShardLoader
from inference.InferenceEngine import LocalInferenceEngine
from inference.DistributedEngine import DistributedInferenceEngine
from inference.ModelRegistry import ModelRegistry


EVAL_PROMPTS = [
    "Explain quantum computing in simple terms.",
    "Write a Python function to sort a list.",
    "What are the benefits of distributed systems?",
]


def run_single_node(model_id, family, device, prompts, max_tokens):
    engine = LocalInferenceEngine(model_id, family, device)
    results = []

    for prompt in prompts:
        start = time.time()
        tokens = []
        ttft = None
        for token in engine.generate_stream(prompt, max_tokens=max_tokens):
            if ttft is None:
                ttft = time.time() - start
            tokens.append(token)

        elapsed = time.time() - start
        results.append({
            "prompt": prompt[:50],
            "ttft": round(ttft, 4) if ttft else None,
            "tokens": len(tokens),
            "elapsed": round(elapsed, 3),
            "tps": round(len(tokens) / max(elapsed, 0.001), 2),
            "response_preview": "".join(tokens)[:100],
        })

    return results


def run_distributed_simulation(model_id, family, device, num_nodes, prompts, max_tokens):
    arch = ModelRegistry.get(family, model_id)
    configs = ShardConfig.plan_split(model_id, family, arch.total_layers, num_nodes, "float16")
    engine = DistributedInferenceEngine(model_id, family, configs, device)
    results = []

    for prompt in prompts:
        start = time.time()
        tokens = []
        ttft = None
        for token in engine.generate_stream(prompt, max_tokens=max_tokens):
            if ttft is None:
                ttft = time.time() - start
            tokens.append(token)

        elapsed = time.time() - start
        results.append({
            "prompt": prompt[:50],
            "num_nodes": num_nodes,
            "layers_per_node": [f"{c.start_layer}-{c.end_layer}" for c in configs],
            "ttft": round(ttft, 4) if ttft else None,
            "tokens": len(tokens),
            "elapsed": round(elapsed, 3),
            "tps": round(len(tokens) / max(elapsed, 0.001), 2),
        })

    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True)
    parser.add_argument("--family", required=True, choices=["qwen", "llama"])
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--num-nodes", type=int, default=1)
    parser.add_argument("--max-tokens", type=int, default=50)
    parser.add_argument("--output", default="experiment_results.json")
    args = parser.parse_args()

    family = ModelFamily(args.family)

    if args.num_nodes == 1:
        results = run_single_node(args.model, family, args.device, EVAL_PROMPTS, args.max_tokens)
    else:
        results = run_distributed_simulation(
            args.model, family, args.device, args.num_nodes, EVAL_PROMPTS, args.max_tokens
        )

    output = {
        "model": args.model,
        "family": args.family,
        "device": args.device,
        "num_nodes": args.num_nodes,
        "results": results,
    }

    with open(args.output, "w") as f:
        json.dump(output, f, indent=2)

    print(json.dumps(output, indent=2))
```

**Commit:**

```bash
git add scripts/run_experiment.py
git commit -m "feat: add experiment runner script for benchmarking"
```

---

### Task 12: Run Experiments (Day 12-13)

Execute experiments across the 6 model configurations. Run each combination:

```bash
# Small models (tech validation, quick)
HF_TOKEN=xxx python scripts/run_experiment.py --model Qwen/Qwen2.5-0.5B-Instruct --family qwen --device mps --num-nodes 1 --output results/qwen_0.5b_1node.json
HF_TOKEN=xxx python scripts/run_experiment.py --model Qwen/Qwen2.5-0.5B-Instruct --family qwen --device mps --num-nodes 2 --output results/qwen_0.5b_2node.json
HF_TOKEN=xxx python scripts/run_experiment.py --model meta-llama/Llama-3.2-1B-Instruct --family llama --device mps --num-nodes 1 --output results/llama_1b_1node.json
HF_TOKEN=xxx python scripts/run_experiment.py --model meta-llama/Llama-3.2-1B-Instruct --family llama --device mps --num-nodes 2 --output results/llama_1b_2node.json

# Medium models (single node vs distributed)
HF_TOKEN=xxx python scripts/run_experiment.py --model Qwen/Qwen2.5-14B-Instruct --family qwen --device mps --num-nodes 1 --output results/qwen_14b_1node.json
HF_TOKEN=xxx python scripts/run_experiment.py --model Qwen/Qwen2.5-14B-Instruct --family qwen --device mps --num-nodes 2 --output results/qwen_14b_2node.json
HF_TOKEN=xxx python scripts/run_experiment.py --model meta-llama/Llama-3.1-8B-Instruct --family llama --device mps --num-nodes 1 --output results/llama_8b_1node.json
HF_TOKEN=xxx python scripts/run_experiment.py --model meta-llama/Llama-3.1-8B-Instruct --family llama --device mps --num-nodes 2 --output results/llama_8b_2node.json

# Large models (distributed only - single node impossible)
HF_TOKEN=xxx python scripts/run_experiment.py --model Qwen/Qwen2.5-32B-Instruct --family qwen --device mps --num-nodes 2 --output results/qwen_32b_2node.json
HF_TOKEN=xxx python scripts/run_experiment.py --model meta-llama/Llama-3.1-70B-Instruct --family llama --device mps --num-nodes 2 --output results/llama_70b_2node.json
HF_TOKEN=xxx python scripts/run_experiment.py --model Qwen/Qwen2.5-32B-Instruct --family qwen --device mps --num-nodes 3 --output results/qwen_32b_3node.json
```

**Key metrics to record per experiment:**
- TTFT (time to first token) in seconds
- TPS (tokens per second) steady-state
- Peak memory usage per node (via `torch.mps.current_allocated_memory()`)
- Total wall-clock time

**Create results aggregation:**

```bash
mkdir -p results
git add results/
git commit -m "feat: run experiments across 6 model configurations"
```

---

## Phase 5: Polish & Demo (Day 14-15)

### Task 13: Node Churn Simulation (Day 14)

Create a test that kills a shard mid-inference and verifies recovery.

**Files:**
- Create: `test/integration/test_churn.py`

This test demonstrates the system's behavior when a node goes offline.
For the demo: log the event clearly and show the route re-planning.

---

### Task 14: Demo Preparation (Day 15)

**Checklist:**
- [ ] Record screen capture of chat app with real inference
- [ ] Show status bar updating with TTFT/TPS
- [ ] Demonstrate distributed mode: 2+ nodes visible in route
- [ ] Show large model (32B) running across 2 nodes where single node fails
- [ ] Prepare slides with metrics tables from experiment results
- [ ] Clean up README with setup instructions

**Files:**
- Modify: `paramind/apps/desktop/README.md`

---

## Dependency Graph

```
Task 1 (ShardConfig) ──┐
Task 2 (ModelRegistry) ─┼─→ Task 3 (ShardLoader) ─→ Task 4 (Verify families)
                        │                                    │
                        │                           Task 5 (InferenceEngine) ─→ Task 7 (DistributedEngine)
                        │                                    │                          │
                        │                           Task 6 (Flask API) ─────────────────┘
                        │                                    │
                        │                           Task 8 (Frontend SSE) ─→ Task 9 (Status Panel) ─→ Task 10 (Route viz)
                        │                                                                                      │
                        └──────────────────────────────────────── Task 11 (Experiment script) ─→ Task 12 (Run experiments)
                                                                                                        │
                                                                                               Task 13 (Churn test) ─→ Task 14 (Demo)
```

## Notes for Implementer

1. **Apple Silicon MPS:** Use `device="mps"` for M4 Max. PyTorch MPS support is mature for inference. Falls back to CPU for unsupported ops.

2. **Memory management for large models:** When loading 32B across 2 nodes (simulation), each shard holds ~16B params = ~32GB FP16. On a 36GB M4 Max, only ONE shard fits at a time. For simulation, load shard1, run forward, offload to CPU, load shard2, run forward. For real distributed mode, each physical machine holds only its shard.

3. **Llama3 access:** Llama models are gated - requires `export HF_TOKEN=hf_xxx` before downloading.

4. **transformers version:** Ensure `>=4.38.0` for proper Qwen2.5 + Llama3 support with DynamicCache.

5. **DeepSeek removed from model matrix:** DeepSeek-V2-Lite was originally listed as "2.9B" in the Small category, but its actual total parameter count is 16B (32GB FP16) — the 2.9B figure was likely the activated MoE parameters, not total model size. This makes it unsuitable as a quick-validation target. The plan now treats Llama 3.2-1B (~2GB, gated via HF_TOKEN) as the peer model alongside Qwen.

5. **The old `QwenSlice` in `inference/ModelSplitter.py` is superseded** by `ShardLoader`. Keep it until all P2PClient references are migrated, then delete.
