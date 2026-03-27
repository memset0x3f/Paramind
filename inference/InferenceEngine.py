from typing import Generator

import torch
from transformers import AutoTokenizer

from .ModelRegistry import ModelRegistry
from .ShardConfig import ModelFamily, ShardConfig
from .ShardLoader import ShardLoader


class LocalInferenceEngine:
    """Full model on a single node, exposed as a streaming token generator."""

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
        cfg = ShardConfig(
            model_id=model_id,
            family=family,
            start_layer=0,
            end_layer=arch_info.total_layers,
            total_layers=arch_info.total_layers,
        )
        self.shard = ShardLoader(cfg, device=device).load()
        self.tokenizer = AutoTokenizer.from_pretrained(model_id, trust_remote_code=True)

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
        input_ids = self.tokenizer(text, return_tensors="pt").input_ids.to(self.device)

        kv_cache = None
        eos_ids = set(self.tokenizer.all_special_ids)
        if self.tokenizer.eos_token_id is not None:
            eos_ids.add(self.tokenizer.eos_token_id)

        with torch.no_grad():
            for _ in range(max_tokens):
                output, kv_cache = self.shard.forward(
                    input_ids, past_key_values=kv_cache
                )
                next_logits = output[:, -1, :]

                if temperature <= 0:
                    next_id = torch.argmax(next_logits, dim=-1).reshape(1, 1)
                else:
                    probs = torch.softmax(next_logits / temperature, dim=-1)
                    next_id = torch.multinomial(probs, num_samples=1)

                token_id = next_id.item()
                if token_id in eos_ids:
                    break

                yield self.tokenizer.decode([token_id], skip_special_tokens=False)
                input_ids = next_id

    def generate(
        self,
        prompt: str,
        max_tokens: int = 256,
        temperature: float = 0.0,
        system_prompt: str = "You are a helpful assistant.",
    ) -> str:
        return "".join(
            self.generate_stream(
                prompt,
                max_tokens=max_tokens,
                temperature=temperature,
                system_prompt=system_prompt,
            )
        )
