import argparse
from pathlib import Path
import sys
from typing import Iterable, Optional, Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from inference.DistributedEngine import DistributedInferenceEngine
from inference.InferenceEngine import LocalInferenceEngine
from inference.ModelRegistry import ModelRegistry
from inference.ShardConfig import ModelFamily, ShardConfig


def _parse_family(value: str) -> ModelFamily:
    normalized = value.strip().lower()
    if normalized == "qwen":
        return ModelFamily.QWEN
    if normalized == "llama":
        return ModelFamily.LLAMA
    raise argparse.ArgumentTypeError(f"Unsupported family: {value}")


def _stream_and_collect(tokens: Iterable[str]) -> str:
    collected = []
    previous_text = ""
    print("Streaming output: ", end="", flush=True)
    for token in tokens:
        token_text = str(token)
        if token_text.startswith(previous_text):
            delta = token_text[len(previous_text) :]
        else:
            delta = token_text
        if delta:
            print(delta, end="", flush=True)
        collected.append(token_text)
        previous_text = token_text
    print()
    return "".join(collected)


def run_local_demo(
    model_id: str,
    family: ModelFamily,
    device: str,
    prompt: str,
    max_tokens: int,
    temperature: float,
) -> str:
    arch_info = ModelRegistry.get(family, model_id)

    print("=== Demo A: single-node local ===")
    print(f"Model: {model_id}")
    print(f"Family: {family.value}")
    print(f"Device: {device}")
    print(f"Total layers: {arch_info.total_layers}")

    engine = LocalInferenceEngine(model_id=model_id, family=family, device=device)
    shard_cfg = engine.shard.shard_config
    print(f"Loaded layers: {shard_cfg.num_layers}/{arch_info.total_layers}")
    print(f"Layer span: {shard_cfg.start_layer}-{shard_cfg.end_layer}")
    print(f"Prompt: {prompt}")

    final_text = _stream_and_collect(
        engine.generate_stream(
            prompt=prompt,
            max_tokens=max_tokens,
            temperature=temperature,
            system_prompt="",
        )
    )
    print(f"Final output: {final_text}")
    return final_text


def run_distributed_sim_demo(
    model_id: str,
    family: ModelFamily,
    device: str,
    prompt: str,
    max_tokens: int,
    temperature: float,
    num_shards: int,
) -> str:
    arch_info = ModelRegistry.get(family, model_id)
    shard_configs = ShardConfig.plan_split(
        model_id=model_id,
        family=family,
        total_layers=arch_info.total_layers,
        num_nodes=num_shards,
    )

    print(f"=== Demo B: {num_shards}-shard distributed simulation ===")
    print(f"Model: {model_id}")
    print(f"Family: {family.value}")
    print(f"Device: {device}")
    print(f"Total layers: {arch_info.total_layers}")
    for idx, cfg in enumerate(shard_configs):
        print(
            f"Shard {idx}: layers {cfg.start_layer}-{cfg.end_layer} "
            f"({cfg.num_layers} layers)"
        )
    print(f"Prompt: {prompt}")

    engine = DistributedInferenceEngine(
        model_id=model_id,
        family=family,
        shard_configs=shard_configs,
        device=device,
    )
    final_text = _stream_and_collect(
        engine.generate_stream(
            prompt=prompt,
            max_tokens=max_tokens,
            temperature=temperature,
            system_prompt="",
        )
    )
    print(f"Final output: {final_text}")
    return final_text


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run a local ParaMind inference demo with explicit loading output."
    )
    parser.add_argument(
        "--mode",
        choices=["local", "distributed-sim", "both"],
        default="both",
        help="Which demo to run.",
    )
    parser.add_argument(
        "--model-id",
        default="Qwen/Qwen2.5-0.5B-Instruct",
        help="Model identifier or local model path.",
    )
    parser.add_argument(
        "--family",
        type=_parse_family,
        default=ModelFamily.QWEN,
        help="Model family: qwen or llama.",
    )
    parser.add_argument(
        "--device",
        default="cpu",
        help="Torch device to use, e.g. cpu or mps.",
    )
    parser.add_argument(
        "--prompt",
        default="用一句话介绍你自己。",
        help="Prompt used for the real generation pass.",
    )
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=256,
        help="Maximum number of generated tokens.",
    )
    parser.add_argument(
        "--temperature",
        type=float,
        default=0.0,
        help="Sampling temperature. 0 means greedy decoding.",
    )
    parser.add_argument(
        "--num-shards",
        type=int,
        default=2,
        help="How many shard configs to simulate in distributed mode.",
    )
    return parser


def run_selected_demos(
    mode: str,
    model_id: str,
    family: ModelFamily,
    device: str,
    prompt: str,
    max_tokens: int,
    temperature: float,
    num_shards: int,
    show_see_also: bool = True,
) -> int:
    if mode in {"local", "both"}:
        run_local_demo(
            model_id=model_id,
            family=family,
            device=device,
            prompt=prompt,
            max_tokens=max_tokens,
            temperature=temperature,
        )

    if mode == "both":
        print()

    if mode in {"distributed-sim", "both"}:
        run_distributed_sim_demo(
            model_id=model_id,
            family=family,
            device=device,
            prompt=prompt,
            max_tokens=max_tokens,
            temperature=temperature,
            num_shards=num_shards,
        )

    if show_see_also:
        print()
        print("See also: scripts/cluster_planner_demo.py")
        print("See also: scripts/runtime_lifecycle_demo.py")

    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return run_selected_demos(
        mode=args.mode,
        model_id=args.model_id,
        family=args.family,
        device=args.device,
        prompt=args.prompt,
        max_tokens=args.max_tokens,
        temperature=args.temperature,
        num_shards=args.num_shards,
        show_see_also=True,
    )


if __name__ == "__main__":
    raise SystemExit(main())
