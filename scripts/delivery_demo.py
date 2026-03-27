from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts import cluster_planner_demo, local_demo, runtime_lifecycle_demo
from scripts.demo_rendering import render_key_value_block, render_table


SCRIPTED_PLANNER_COMMANDS = ["start node-a,node-b,node-c", "join node-d", "show", "quit"]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Unified delivery-facing CLI for ParaMind inference demos."
    )
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("overview", help="Show the delivery demo overview.")

    local_parser = subparsers.add_parser("local", help="Run the local/distributed-sim demo.")
    local_parser.add_argument("--mode", choices=["local", "distributed-sim", "both"], default="both")
    local_parser.add_argument("--model-id", default="Qwen/Qwen2.5-0.5B-Instruct")
    local_parser.add_argument("--family", type=local_demo._parse_family, default=local_demo.ModelFamily.QWEN)
    local_parser.add_argument("--device", default="cpu")
    local_parser.add_argument("--prompt", default="用一句话介绍你自己。")
    local_parser.add_argument("--max-tokens", type=int, default=8)
    local_parser.add_argument("--temperature", type=float, default=0.0)
    local_parser.add_argument("--num-shards", type=int, default=2)

    planner_parser = subparsers.add_parser("planner", help="Run the cluster planner demo.")
    planner_parser.add_argument("--scripted", action="store_true", help="Run a fixed scripted planner walkthrough.")

    subparsers.add_parser("lifecycle", help="Run the runtime lifecycle demo.")

    status_parser = subparsers.add_parser("status", help="Show a stable runtime/coordinator status snapshot.")
    status_parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")

    all_parser = subparsers.add_parser("all", help="Run the recommended delivery walkthrough.")
    all_parser.add_argument("--model-id", default="Qwen/Qwen2.5-0.5B-Instruct")
    all_parser.add_argument("--family", type=local_demo._parse_family, default=local_demo.ModelFamily.QWEN)
    all_parser.add_argument("--device", default="cpu")
    all_parser.add_argument("--prompt", default="用一句话介绍你自己。")
    all_parser.add_argument("--max-tokens", type=int, default=8)
    all_parser.add_argument("--temperature", type=float, default=0.0)
    all_parser.add_argument("--num-shards", type=int, default=2)
    return parser


def render_overview() -> str:
    return "\n".join(
        [
            "=== PARAMIND DELIVERY DEMO OVERVIEW ===",
            "This delivery demo is the single entrypoint for inference showcase and acceptance.",
            "",
            "Available tracks:",
            "- local: run the real single-node and distributed-sim inference demo",
            "- planner: show node selection, scoring, join/drop, and shard reassignment",
            "- lifecycle: show prepare, ready, commit, draining, and release",
            "- status: show the final runtime/coordinator snapshot in text or JSON",
            "",
            "Recommended order:",
            "1. local",
            "2. planner --scripted",
            "3. lifecycle",
            "4. status",
        ]
    )


def render_status_text(state: dict) -> str:
    snapshot = state["final_snapshot"]
    coordinator = snapshot["coordinator"]
    rows = []
    for runtime in snapshot["runtimes"]:
        if not runtime["shards"]:
            rows.append((runtime["node_id"], "-", "-", "-", "-", "-"))
            continue
        for shard in runtime["shards"]:
            start_layer, end_layer = shard["shard_key"]
            rows.append(
                (
                    runtime["node_id"],
                    f"{start_layer}-{end_layer}",
                    shard["state"],
                    shard["active_owner"],
                    shard["pending_unload"],
                    shard["inflight_requests"],
                )
            )
    return "\n".join(
        [
            "=== DELIVERY STATUS ===",
            render_key_value_block(
                "CURRENT CLUSTER",
                {
                    "phase": coordinator["phase"],
                    "reconfiguration_id": coordinator["reconfiguration_id"],
                    "ready_nodes": coordinator["ready_nodes"],
                    "reconfig_ready_nodes": coordinator["reconfig_ready_nodes"],
                    "reconfiguration_committed": coordinator["reconfiguration_committed"],
                },
            ),
            render_table(
                "SHARD STATES",
                columns=["node", "shard", "state", "active_owner", "pending_unload", "inflight_requests"],
                rows=rows,
            ),
        ]
    )


def run_local_command(args) -> str:
    from io import StringIO
    from contextlib import redirect_stdout

    buffer = StringIO()
    with redirect_stdout(buffer):
        local_demo.run_selected_demos(
            mode=getattr(args, "mode", "both"),
            model_id=args.model_id,
            family=args.family,
            device=args.device,
            prompt=args.prompt,
            max_tokens=args.max_tokens,
            temperature=args.temperature,
            num_shards=args.num_shards,
            show_see_also=False,
        )
    return buffer.getvalue().rstrip()


def run_planner_command(scripted: bool) -> str:
    if scripted:
        return cluster_planner_demo.run_scripted_demo(SCRIPTED_PLANNER_COMMANDS, include_guide=True)
    return cluster_planner_demo.ClusterDemoSession().render_current(include_guide=True)


def run_lifecycle_command() -> str:
    return runtime_lifecycle_demo.run_demo()


def run_status_command(as_json: bool = False) -> str:
    state = runtime_lifecycle_demo.build_demo_state()
    if as_json:
        return json.dumps(state, ensure_ascii=False, indent=2)
    return render_status_text(state)


def run_all_command(args) -> str:
    sections = [
        "=== DELIVERY STEP 1: LOCAL ===\n" + run_local_command(args),
        "=== DELIVERY STEP 2: PLANNER ===\n" + run_planner_command(scripted=True),
        "=== DELIVERY STEP 3: LIFECYCLE ===\n" + run_lifecycle_command(),
        "=== DELIVERY STEP 4: STATUS ===\n" + run_status_command(as_json=False),
    ]
    return "\n\n".join(sections)


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    command = args.command or "overview"

    if command == "overview":
        print(render_overview())
        return 0
    if command == "local":
        print(run_local_command(args))
        return 0
    if command == "planner":
        print(run_planner_command(scripted=args.scripted))
        return 0
    if command == "lifecycle":
        print(run_lifecycle_command())
        return 0
    if command == "status":
        print(run_status_command(as_json=args.json))
        return 0
    if command == "all":
        print(run_all_command(args))
        return 0

    parser.error(f"Unknown command: {command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
