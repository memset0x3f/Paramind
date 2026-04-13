from __future__ import annotations

import argparse
import json
from pathlib import Path
import socket
import sys
import time

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scheduler.NodeInventory import NodeState, from_local_snapshot


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


def render_json(node: NodeState, scan_wall_time_ms: float | None = None) -> str:
    payload: dict = {}
    for key, value in sorted(node.to_dict().items()):
        payload[key] = value
    payload["effective_speed"] = node.effective_speed()
    payload["effective_capacity_blocks"] = node.effective_capacity_blocks()
    if scan_wall_time_ms is not None:
        payload["scan_wall_time_ms"] = round(scan_wall_time_ms, 3)
    return json.dumps(payload, indent=2)


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
    t0 = time.perf_counter()
    node = scan_local_node(node_id=args.node_id, host=args.host)
    scan_wall_time_ms = (time.perf_counter() - t0) * 1000.0
    if args.json:
        print(render_json(node, scan_wall_time_ms=scan_wall_time_ms))
    else:
        print(render_text(node))
        print(f"scan_wall_time_ms={scan_wall_time_ms:.1f}")


if __name__ == "__main__":
    main()
