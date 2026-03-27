# Distributed Placement Research — 2026-03-25

## Summary

This note replaces the earlier “average split by compute score + fixed-boundary sticky replanner” mental model with a more realistic heterogeneous placement model for Paramind. The model assumes a LAN deployment for v1, so bandwidth is kept in the data model but not treated as the dominant term in the cold-start objective. Initial placement is modeled as a contiguous partition problem on a linear layer chain and is solved with dynamic programming. Replanning is modeled as a boundary-adjusting optimization with migration and instability penalties; the implementation uses a practical DP-based approximation rather than a full global optimum solver.

## Problem model

Treat the model as an ordered chain of layers `1..L`. A cluster state is a set of node states. Each node state should describe not only abstract “strength” but also effective placement capacity and an approximate per-block execution speed. In Paramind this is now represented by `inference/NodeInventory.py`, which defines `NodeState` and `ClusterState`.

For v1, a `NodeState` should contain at least:

- `node_id`
- `device_type`
- `free_memory_gb`
- `kv_headroom_gb`
- `max_blocks_capacity`
- `block_latency_ms` or `block_throughput`
- `loaded_ranges`
- `online`

The structure also preserves future network fields:

- `latency_ms_to_peers`
- `bandwidth_gbps_to_peers`

These link fields are deliberately not dominant in the v1 cold-start objective because the working assumption is that the first real deployment target is a low-latency LAN.

## Cold-start objective

Let the estimated work of layer `i` be `w_i`, and let node `n` have normalized speed `s_n`. If a contiguous shard from layer `a` to layer `b` is placed on node `n`, define the stage cost as:

\[
C_{\text{stage}}(a,b,n)=\frac{\sum_{i=a}^{b} w_i}{s_n}+P_{\text{role}}(a,b)+P_{\text{memory}}(a,b,n)
\]

The three terms mean:

- \(\sum w_i / s_n\): estimated execution time of that contiguous segment on that node.
- \(P_{\text{role}}(a,b)\): extra penalty if the segment is the first or last stage, since those stages carry embedding/output overhead.
- \(P_{\text{memory}}(a,b,n)\): capacity feasibility and memory-pressure penalty. If the shard exceeds the node’s block capacity, the assignment is infeasible; otherwise a smaller pressure term nudges the optimizer away from saturating the node.

The primary v1 cold-start objective is bottleneck minimization:

\[
\min \max_{k \in \text{stages}} C_{\text{stage}}(k)
\]

This says the initial placement should minimize the slowest stage, because that stage dominates single-request latency and strongly influences pipeline throughput. A softer variant can be kept in reserve:

\[
J_{\text{cold}}=\alpha \cdot \max C_{\text{stage}} + (1-\alpha)\cdot \sum C_{\text{stage}}
\]

but the current implementation uses the bottleneck-first objective because it is easier to reason about and test.

## Why dynamic programming fits cold start

Cold start has a special property: the model is a linear chain and each node is assigned at most one contiguous segment in v1. That turns the problem into a heterogeneous contiguous partition problem, which is a good fit for dynamic programming. With tens of nodes and tens or hundreds of layers, DP is still practical, explainable, and stable. It also naturally enforces contiguity, capacity feasibility, and first/last-stage penalties without introducing a heavyweight MILP dependency.

## Replan objective

Replanning differs from cold start because it has state. The current assignment matters, loaded ranges matter, and boundary movement itself has a cost. Replanning therefore uses a broader objective:

\[
J_{\text{replan}}=\alpha \cdot \max C_{\text{stage}}+\beta \cdot \sum C_{\text{stage}}+\gamma \cdot C_{\text{migration}}+\delta \cdot C_{\text{instability}}
\]

where:

- \(\max C_{\text{stage}}\) keeps tail latency under control.
- \(\sum C_{\text{stage}}\) approximates overall work / throughput pressure.
- \(C_{\text{migration}}\) penalizes moving ownership and loading shards on cold nodes.
- \(C_{\text{instability}}\) penalizes unnecessary boundary motion.

The migration term is modeled as:

\[
C_{\text{migration}}=\sum_{\text{new shards}}\Big(\lambda_1 \cdot \mathbf{1}[\text{owner changed}] + \lambda_2 \cdot \text{shard size} + \lambda_3 \cdot \mathbf{1}[\text{not preloaded}]\Big)
\]

and the instability term is modeled as:

\[
C_{\text{instability}}=\sum_{\text{boundaries}}\lambda_4 \cdot |\text{new boundary} - \text{old boundary}|
\]

This means a good replan is not just “fast in the abstract”; it is fast **and** conservative about moving work that is already in a reasonable place.

## Parameters that matter now vs. later

### v1 required parameters

The current planner should reason about:

- per-layer or per-block work estimates `w_i`
- node speed `s_n`, ideally from a block benchmark rather than a hand-written `compute_score`
- node block capacity
- first/last shard role penalties
- current `loaded_ranges`
- current boundary positions
- join / leave / online state

### v2 reserved parameters

The design intentionally keeps room for:

- peer latency / bandwidth
- measured load / unload time
- KV cache pressure under active traffic
- multi-request throughput terms and queueing effects

## Why replan is not a global exact optimum

Join/leave events turn placement into a stateful optimization problem with migration cost, capacity constraints, boundary movement, and loaded-state reuse. A full exact optimizer would be harder to maintain, harder to explain, and more likely to create unstable large-scale reshuffles. Paramind therefore prefers a practical approximation: keep the same external interface, allow boundaries to move, but solve a smaller structured problem with strong penalties against unnecessary change.

This is why the current implementation allows boundary adjustment but does not attempt a full ILP or MILP solver. The system is meant to be understandable, testable, and stable under small topology changes.

## Practical execution strategy

For cold start, the planner should:

1. normalize node inputs into `NodeState`
2. discard offline / zero-capacity nodes
3. compute a contiguous partition with DP
4. emit a `PlacementPlan` with first/middle/last roles

For replan, the planner should:

1. reuse the current plan if every current shard still fits on its current node
2. otherwise solve a boundary-adjusting DP that incorporates migration and instability penalties
3. preserve `source_node_id` so later orchestration can compute keep/load/move/unload diffs

## Implementation mapping

The current implementation maps this design into the following files:

- `inference/NodeInventory.py`
- `inference/DeviceProfile.py`
- `inference/ClusterPlanner.py`
- `inference/ClusterCoordinator.py`

The main public planner interfaces remain:

- `plan_static_distribution(model_id, total_layers, nodes, layer_work=None)`
- `replan_distribution(current, nodes, total_layers, layer_work=None)`

The coordinator interface remains stable:

- `ClusterCoordinator.build_plan(profiles)`
- `ClusterCoordinator.replan(profiles)`

## Validation criteria

The design is considered implemented when the following are true:

1. heterogeneous cold-start placement gives stronger nodes larger contiguous segments;
2. first/last stages are not pushed onto the weakest nodes by default;
3. impossible capacity states fail loudly;
4. replanning can change boundaries instead of only reassigning owners;
5. loaded ranges and prior ownership influence the replanned result;
6. coordinator-level tests continue to pass against the richer planner input.

## Current conclusion

Paramind is now moving away from the simplistic notion that “balanced layer count” is the same thing as good distribution. Under a LAN assumption, initial placement should primarily optimize for node quality and bottleneck latency, while replanning should add migration and instability costs so join/leave events do not thrash the cluster. The current codebase has been updated in this direction: node information is now modeled explicitly, cold start uses DP instead of average splitting, and replanning is allowed to adjust boundaries while preserving migration metadata for later orchestration work.
