from __future__ import annotations

import argparse
import json
from pathlib import Path
import socket
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from inference.NodeInventory import NodeState, from_local_snapshot


def scan_local_node(node_id: str, host: str | None = None) -> NodeState:
    return from_local_snapshot(node_id=node_id, host=host)


def render_text(node: NodeState) -> str:
    return "\n".join(
        [
            f"node_id={node.node_id}",
            f"host={node.host}",
            f"device_type={node.device_type}",
            f"total_memory_gb={node.total_memory_gb}",
            f"free_memory_gb={node.free_memory_gb}",
            f"effective_speed={node.effective_speed()}",
            f"effective_capacity_blocks={node.effective_capacity_blocks()}",
            f"loaded_ranges={node.loaded_ranges}",
        ]
    )


def render_json(node: NodeState) -> str:
    payload = node.to_dict()
    payload["effective_speed"] = node.effective_speed()
    payload["effective_capacity_blocks"] = node.effective_capacity_blocks()
    return json.dumps(payload, indent=2, sort_keys=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Scan local hardware into a NodeState-style report."
    )
    parser.add_argument(
        "--json", action="store_true", help="Render machine-readable JSON output."
    )
    parser.add_argument(
        "--node-id", default=socket.gethostname(), help="Override node id."
    )
    parser.add_argument("--host", default=None, help="Override host value.")
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    node = scan_local_node(node_id=args.node_id, host=args.host)
    if args.json:
        print(render_json(node))
    else:
        print(render_text(node))


if __name__ == "__main__":
    main()
