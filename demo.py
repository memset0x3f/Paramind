import argparse
import logging
import time
import uuid
import torch

from inference.ModelRegistry import ModelRegistry
from inference.NodeRuntime import NodeRuntime
from inference.ShardConfig import ModelFamily
from scheduler.ClusterTypes import ShardAssignment


LOGGER = logging.getLogger("paramind.demo")


def build_node_id(group: str, node_index: int) -> uuid.UUID:
    return uuid.uuid5(uuid.NAMESPACE_DNS, f"paramind-demo:{group}:{node_index}")


def wait_for_result(runtime: NodeRuntime):
    while True:
        for inference_id, value in list(runtime.inference_results.items()):
            if isinstance(value, tuple) and len(value) == 2 and value[1]:
                return inference_id, value[0]
        time.sleep(0.2)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Minimal real-shard demo: two local NodeRuntime processes over real P2P transport."
    )
    parser.add_argument("--node-index", type=int, choices=[1, 2], required=True)
    parser.add_argument("--group", default="demo-group")
    parser.add_argument("--model-id", default="Qwen/Qwen2.5-0.5B-Instruct")
    parser.add_argument("--prompt", default="hello")
    parser.add_argument(
        "--device", default="cuda" if torch.cuda.is_available() else "cpu"
    )
    parser.add_argument("--family", choices=["qwen", "llama"], default="qwen")
    args = parser.parse_args()

    log_file = f"{args.node_index}.log"

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        filename=log_file,
        filemode="a",
        force=True,
    )
    LOGGER.info("Logging to %s", log_file)

    family = ModelFamily.QWEN if args.family == "qwen" else ModelFamily.LLAMA
    total_layers = ModelRegistry.get(family, args.model_id).total_layers
    split = total_layers // 2

    node_id = build_node_id(args.group, args.node_index)
    if args.node_index == 1:
        assignment = ShardAssignment(
            node_id=str(node_id),
            start_layer=0,
            end_layer=split,
            role="first",
        )
    else:
        assignment = ShardAssignment(
            node_id=str(node_id),
            start_layer=split,
            end_layer=total_layers,
            role="last",
        )

    runtime = NodeRuntime(
        node_id=node_id,
        node_group=args.group,
        family=family,
        total_layers=total_layers,
        model_id=args.model_id,
        shard_assignment=assignment,
        device=args.device,
    )

    runtime.run()
    LOGGER.info(
        "Node %s ready with assignment %s-%s",
        runtime.node_id,
        assignment.start_layer,
        assignment.end_layer,
    )

    if args.node_index == 1:
        # Wait for the second node to join
        while len(runtime.p2p_client.peerInfo) < 2:
            time.sleep(1)
        peer_ids = sorted(runtime.p2p_client.peerInfo.keys())

        runtime.route = [runtime.node_id, peer_ids[0]]

        # Current NodeRuntime loop can continue generation for many rounds;
        # broaden EOS ids so cooperation test converges quickly.
        # runtime.eos_ids = set(range(500000))

        runtime.distributedInfer(args.prompt)
        inference_id, text = wait_for_result(runtime)
        print(f"[leader] inference_id={inference_id}")
        print(f"[leader] result={text!r}")

    print(f"Node {runtime.node_id} running, Ctrl+C to exit")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
