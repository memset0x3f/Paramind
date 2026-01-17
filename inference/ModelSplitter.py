import torch
import torch.nn as nn
from transformers import AutoModelForCausalLM, AutoConfig
import copy
import gc


class QwenSlice(nn.Module):
    def __init__(self, model_path, start_layer, end_layer, device="cuda"):
        super().__init__()
        self.device = device
        self.start_layer = start_layer
        self.end_layer = end_layer

        print(f"[{device}] Loading slice: Layers {start_layer}-{end_layer} ...")

        # 1. 加载完整模型到内存 (0.5B 很小，这就没问题了)
        full_model = AutoModelForCausalLM.from_pretrained(
            model_path,
            torch_dtype=torch.float16,
            device_map="cpu",
            trust_remote_code=True,
        )
        self.config = full_model.config

        # 2. 构建分片模型 (复用 Petals 策略: 删减层)
        self.model_shard = copy.deepcopy(full_model.model)

        # 筛选需要的层
        selected_layers = nn.ModuleList()
        for i in range(start_layer, end_layer):
            selected_layers.append(full_model.model.layers[i].to(device))
        self.model_shard.layers = selected_layers

        # 处理 Embeddings (仅首节点)
        if start_layer == 0:
            self.model_shard.embed_tokens = full_model.model.embed_tokens.to(device)
            self.model_shard.rotary_emb = full_model.model.rotary_emb.to(device)
        else:
            self.model_shard.embed_tokens = None
            self.model_shard.rotary_emb = full_model.model.rotary_emb.to(device)

        # 处理 Head (仅尾节点)
        self.lm_head = None
        if end_layer == self.config.num_hidden_layers:
            self.model_shard.norm = full_model.model.norm.to(device)
            self.lm_head = full_model.lm_head.to(device)
        else:
            self.model_shard.norm = nn.Identity()

        del full_model
        gc.collect()
        torch.cuda.empty_cache()

    def forward(self, x, past_key_values=None):
        """
        x: input_ids (Long) 或 hidden_states (Float)
        past_key_values: 这一分片对应的 KV Cache
        """
        # Qwen2Model 的 forward 接收 past_key_values 参数
        # 我们需要确保传入的 past_key_values 长度与 self.model_shard.layers 长度一致吗？
        # 不，transformers 的 logic 是：如果 layer index 匹配不上，它可能会报错。
        # 这里的 trick 是：我们这个 model_shard 认为自己只有 (end-start) 这么多层。
        # 所以传入的 past_key_values 应该也是只有这么多层的 list。

        if self.start_layer == 0:
            # Node 1: 输入是 input_ids
            outputs = self.model_shard(
                input_ids=x, past_key_values=past_key_values, use_cache=True
            )
        else:
            # Node 2: 输入是 hidden_states (作为 inputs_embeds)
            outputs = self.model_shard(
                inputs_embeds=x, past_key_values=past_key_values, use_cache=True
            )

        hidden_states = outputs.last_hidden_state
        new_kv_cache = outputs.past_key_values

        if self.lm_head is not None:
            # 尾节点计算 Logits
            logits = self.lm_head(hidden_states)
            return logits, new_kv_cache

        return hidden_states, new_kv_cache
