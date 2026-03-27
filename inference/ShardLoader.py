import json
import os
from pathlib import Path
from typing import Optional, Tuple

import torch
import torch.nn as nn
from safetensors.torch import load_file
from transformers import AutoConfig, AutoModelForCausalLM

from .ShardConfig import ShardConfig, ModelFamily
from .ModelRegistry import ModelRegistry


class ModelShard(nn.Module):
    """A loaded model shard ready for forward pass."""

    def __init__(
        self,
        config: ShardConfig,
        layers: nn.ModuleList,
        embed_tokens: Optional[nn.Module],
        norm: Optional[nn.Module],
        lm_head: Optional[nn.Module],
        rotary_emb,
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
        cache_position = torch.arange(
            past_len, past_len + seq_len, device=hidden_states.device
        )
        position_ids = cache_position.unsqueeze(0)

        # Compute position embeddings from rotary_emb
        # Qwen: rotary_emb at model level, pre-computes position_embeddings
        # Llama: rotary_emb lives inside each attention layer and uses position_ids directly
        if self.rotary_emb is not None:
            position_embeddings = self.rotary_emb(hidden_states, position_ids)
        else:
            position_embeddings = None

        # Forward through all layers
        for layer in self.layers:
            if position_embeddings is not None:
                # Qwen path: pass pre-computed position embeddings
                layer_output = layer(
                    hidden_states,
                    attention_mask=None,
                    position_ids=position_ids,
                    past_key_values=past_key_values,
                    use_cache=True,
                    cache_position=cache_position,
                    position_embeddings=position_embeddings,
                )
            else:
                # Llama path: each attention layer computes RoPE internally from position_ids
                layer_output = layer(
                    hidden_states,
                    attention_mask=None,
                    position_ids=position_ids,
                    past_key_values=past_key_values,
                    use_cache=True,
                    cache_position=cache_position,
                )
            # layer_output is either (hidden_states,) or (hidden_states, kv_cache)
            hidden_states = (
                layer_output[0] if isinstance(layer_output, tuple) else layer_output
            )

        if self.shard_config.is_last_shard and self.norm is not None:
            hidden_states = self.norm(hidden_states)

        if self.lm_head is not None:
            logits = self.lm_head(hidden_states)
            return logits, past_key_values

        return hidden_states, past_key_values


class ShardLoader:
    """Loads model shards from safetensors files.

    Uses a full model shell for architecture + selectively loads layer weights.
    For Qwen2.5-0.5B (1GB in bfloat16), loading full model to CPU is acceptable.
    For larger models, this approach can be combined with device_map for GPU offloading.
    """

    def __init__(self, config: ShardConfig, device: str = "cpu"):
        self.config = config
        self.device = device
        self.arch_info = ModelRegistry.get(config.family, config.model_id)

    def _config_torch_dtype(self) -> torch.dtype | None:
        mapping = {
            "float16": torch.float16,
            "bfloat16": torch.bfloat16,
            "float32": torch.float32,
        }
        if self.config.dtype is None:
            return None
        try:
            return mapping[self.config.dtype]
        except KeyError as exc:
            raise ValueError(f"Unsupported dtype: {self.config.dtype}") from exc

    @staticmethod
    def _native_state_dict_dtype(state_dict: dict) -> torch.dtype | None:
        first_tensor = next(iter(state_dict.values()), None)
        return None if first_tensor is None else first_tensor.dtype

    def load(self) -> ModelShard:
        """Load a model shard using selective weight loading.

        Strategy (per Plan):
        1. Create meta model shell (zero memory until weights assigned)
        2. Download config + index (small files)
        3. load_file() only the shard files containing needed layers
        4. For each owned component: extract from shell, load_state_dict(assign=True), .to(device)
        5. del shell, return ModelShard with only owned components
        """
        model_config = AutoConfig.from_pretrained(
            self.config.model_id, trust_remote_code=True
        )
        model_path = self._resolve_model_path()
        weight_map = self._parse_weight_index(model_path)

        # Step 1: Determine which safetensors keys we need
        arch = self.arch_info
        needed_prefixes = []

        # Layer weights
        for i in range(self.config.start_layer, self.config.end_layer):
            needed_prefixes.append(f"{arch.layer_prefix}.{i}.")

        # Embed tokens — first shard always needs it; last shard needs it for weight tying
        if self.config.is_first_shard or self.config.is_last_shard:
            needed_prefixes.append(f"{arch.embed_key}.")

        # Norm + LM head (last shard)
        if self.config.is_last_shard:
            needed_prefixes.append(f"{arch.norm_key}.")
            needed_prefixes.append(f"{arch.lm_head_key}.")

        # Rotary emb (Qwen only) — shared component, load for ALL shards
        if arch.rotary_key is not None:
            needed_prefixes.append(f"{arch.rotary_key}.")

        # Group keys by shard file
        files_to_keys = {}
        for key, shard_file in weight_map.items():
            if any(key.startswith(p) for p in needed_prefixes):
                files_to_keys.setdefault(shard_file, []).append(key)

        # Step 2: Load only needed tensors from safetensors
        state_dict = {}
        for shard_file, keys in files_to_keys.items():
            full_path = os.path.join(model_path, shard_file)
            shard_weights = load_file(full_path, device="cpu")
            for k in keys:
                if k in shard_weights:
                    state_dict[k] = shard_weights[k]

        target_dtype = self._config_torch_dtype() or self._native_state_dict_dtype(
            state_dict
        )

        # Step 3: Create empty model shell (meta device, no memory allocated)
        shell_kwargs = {"trust_remote_code": True}
        if target_dtype is not None:
            shell_kwargs["torch_dtype"] = target_dtype
        with torch.device("meta"):
            shell = AutoModelForCausalLM.from_config(model_config, **shell_kwargs)

        # Step 4: Assemble shard by extracting components and loading individually
        shard = self._assemble_shard(shell, model_config, state_dict, target_dtype)
        shard.eval()
        del shell
        return shard

    def _assemble_shard(
        self,
        shell: AutoModelForCausalLM,
        model_config,
        state_dict: dict,
        target_dtype: torch.dtype | None,
    ) -> ModelShard:
        """Build ModelShard by extracting and loading components from meta shell.

        Each component is extracted from shell, load_state_dict(assign=True)'d
        to populate its meta tensors, then .to(device)'d individually.
        """
        arch = self.arch_info

        # --- Layers ---
        layers = nn.ModuleList()
        for i in range(self.config.start_layer, self.config.end_layer):
            layer = shell.model.layers[i]
            layer_prefix = f"{arch.layer_prefix}.{i}."
            layer_state = {
                k.removeprefix(layer_prefix): v
                for k, v in state_dict.items()
                if k.startswith(layer_prefix)
            }
            layer.load_state_dict(layer_state, assign=True)
            layer = self._move_module(layer, target_dtype)
            layers.append(layer)

        # --- Embed tokens (first shard always; last shard for weight tying only) ---
        embed_tokens = None
        embed_prefix = f"{arch.embed_key}."
        embed_in_state = any(k.startswith(embed_prefix) for k in state_dict)
        if self.config.is_first_shard:
            if embed_in_state:
                embed_tokens = shell.model.embed_tokens
                embed_state = {
                    k.removeprefix(embed_prefix): v
                    for k, v in state_dict.items()
                    if k.startswith(embed_prefix)
                }
                embed_tokens.load_state_dict(embed_state, assign=True)
                embed_tokens = self._move_module(embed_tokens, target_dtype)
        elif self.config.is_last_shard and embed_in_state:
            # Last shard: load embed into shell temporarily for weight tying only
            embed_tmp = shell.model.embed_tokens
            embed_state = {
                k.removeprefix(embed_prefix): v
                for k, v in state_dict.items()
                if k.startswith(embed_prefix)
            }
            embed_tmp.load_state_dict(embed_state, assign=True)
            shell.model.embed_tokens = self._move_module(embed_tmp, target_dtype)

        # --- Rotary emb (Qwen only — computed module, no stored weights in safetensors) ---
        rotary_emb = self._build_rotary_emb(shell, model_config, target_dtype)

        # --- Norm + LM head (last shard only) ---
        norm = None
        lm_head = None
        if self.config.is_last_shard:
            # Norm
            norm = shell.model.norm
            norm_prefix = f"{arch.norm_key}."
            norm_state = {
                k.removeprefix(norm_prefix): v
                for k, v in state_dict.items()
                if k.startswith(norm_prefix)
            }
            norm.load_state_dict(norm_state, assign=True)
            norm = self._move_module(norm, target_dtype)

            # LM head — handle weight tying: Qwen ties lm_head.weight to embed_tokens.weight
            # The weights may be stored under embed_tokens key, not lm_head key
            head_prefix = f"{arch.lm_head_key}."
            head_state = {
                k.removeprefix(head_prefix): v
                for k, v in state_dict.items()
                if k.startswith(head_prefix)
            }
            embed_prefix = f"{arch.embed_key}."
            embed_in_state = any(k.startswith(embed_prefix) for k in state_dict)

            if head_state:
                # lm_head has its own weights in state_dict
                lm_head = shell.lm_head
                lm_head.load_state_dict(head_state, assign=True)
                lm_head = self._move_module(lm_head, target_dtype)
            elif embed_in_state:
                # Weight tying: lm_head shares with embed_tokens (Qwen pattern)
                lm_head = shell.lm_head.to_empty(device=self.device)
                if target_dtype is not None:
                    lm_head = lm_head.to(dtype=target_dtype)
                lm_head.weight = shell.model.embed_tokens.weight

        return ModelShard(
            config=self.config,
            layers=layers,
            embed_tokens=embed_tokens,
            norm=norm,
            lm_head=lm_head,
            rotary_emb=rotary_emb,
            model_config=model_config,
        )

    def _build_rotary_emb(
        self,
        shell: AutoModelForCausalLM,
        model_config,
        target_dtype: torch.dtype | None,
    ):
        """Build a non-meta rotary embedding module on the target device.

        Qwen keeps rotary_emb at model level, but its buffers are computed from
        config rather than loaded from safetensors. Pulling the module out of a
        meta shell and calling `.to(device)` leaves those buffers on meta and
        raises "Cannot copy out of meta tensor".

        To preserve lazy weight loading while avoiding the meta-tensor trap,
        rebuild the rotary module directly on the target device from config.
        """
        if self.arch_info.rotary_key is None:
            return None

        meta_rotary = getattr(shell.model, "rotary_emb", None)
        if meta_rotary is None:
            return None

        rotary_cls = meta_rotary.__class__
        target_device = torch.device(self.device)

        try:
            rotary_emb = rotary_cls(config=model_config, device=target_device)
        except TypeError:
            try:
                rotary_emb = rotary_cls(model_config, device=target_device)
            except TypeError:
                rotary_emb = rotary_cls(config=model_config)
        if target_dtype is None:
            return rotary_emb.to(target_device)
        return rotary_emb.to(device=target_device, dtype=target_dtype)

    def _move_module(self, module: nn.Module, target_dtype: torch.dtype | None):
        if target_dtype is None:
            return module.to(self.device)
        return module.to(device=self.device, dtype=target_dtype)

    def _resolve_model_path(self) -> str:
        """Find model on disk (HuggingFace cache or modelscope)."""
        from huggingface_hub import snapshot_download

        if os.path.isdir(self.config.model_id):
            return self.config.model_id

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
                    os.path.join(model_path, fname), device="cpu"
                ).keys()
                return {k: fname for k in all_keys}

        raise FileNotFoundError(f"No safetensors files found in {model_path}")
