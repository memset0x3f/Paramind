from typing import Generator, List, Optional, Set

import torch
from transformers import AutoTokenizer

from .ShardConfig import ModelFamily, ShardConfig
from .ShardLoader import ShardLoader


class DistributedInferenceEngine:
    """
    Multi-shard inference engine.

    In simulation mode: all shards are loaded locally and executed in sequence.
    In P2P mode: only the local shard is loaded and remote forwarding is delegated
    to the configured P2P client.
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
        self._tokenizer = None

        if local_shard_index is None:
            self.shards = [
                ShardLoader(cfg, device=device).load() for cfg in self.shard_configs
            ]
            self.local_index = 0
        else:
            self.local_index = local_shard_index
            self.shards = [None] * len(self.shard_configs)
            self.shards[local_shard_index] = ShardLoader(
                self.shard_configs[local_shard_index], device=device
            ).load()

    def _get_tokenizer(self):
        if self._tokenizer is None:
            self._tokenizer = AutoTokenizer.from_pretrained(
                self.model_id, trust_remote_code=True
            )
        return self._tokenizer

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
        tokenizer = self._get_tokenizer()
        text = tokenizer.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True
        )
        input_ids = tokenizer(text, return_tensors="pt").input_ids.to(self.device)

        eos_ids: Set[int] = set(tokenizer.all_special_ids)
        if tokenizer.eos_token_id is not None:
            eos_ids.add(tokenizer.eos_token_id)

        kv_caches = [None] * len(self.shard_configs)
        generated_ids: list[int] = []
        decoded_so_far = ""

        with torch.no_grad():
            for _ in range(max_tokens):
                x = input_ids
                for i, shard in enumerate(self.shards):
                    if shard is not None:
                        x, kv_caches[i] = shard.forward(x, past_key_values=kv_caches[i])
                    else:
                        x, kv_caches[i] = self._remote_forward(i, x, kv_caches[i])

                next_logits = x[:, -1, :]

                if temperature <= 0:
                    next_id = torch.argmax(next_logits, dim=-1).reshape(1, 1)
                else:
                    probs = torch.softmax(next_logits / temperature, dim=-1)
                    next_id = torch.multinomial(probs, num_samples=1)

                token_id = next_id.item()
                if token_id in eos_ids:
                    break

                generated_ids.append(token_id)
                new_text = tokenizer.decode(
                    generated_ids,
                    skip_special_tokens=True,
                    clean_up_tokenization_spaces=False,
                )
                # Cumulative text only (same contract as LocalInferenceEngine.generate_stream).
                if new_text != decoded_so_far:
                    yield new_text
                    decoded_so_far = new_text

                input_ids = next_id

    def _remote_forward(self, shard_index, x, kv_cache):
        if self.p2p_client is None:
            raise RuntimeError(
                f"Shard {shard_index} is remote but no P2P client is configured"
            )
        if not hasattr(self.p2p_client, "remote_forward"):
            raise AttributeError(
                "Configured P2P client does not implement remote_forward"
            )
        return self.p2p_client.remote_forward(
            shard_index=shard_index,
            tensor_payload=x,
            kv_payload=kv_cache,
        )
