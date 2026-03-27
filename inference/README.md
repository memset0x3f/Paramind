# Inference Module — 协作说明

## 文件结构

`inference/` 负责把节点状态变成 placement，再把 placement 变成 shard lifecycle；它不负责 retry / rollback / 掉线恢复，也不负责 app UI。

```
inference/
├── __init__.py            # 延迟导出
├── ShardConfig.py         # shard 边界与角色
├── ModelRegistry.py       # 模型家族 → 架构信息
├── ShardLoader.py         # 懒加载：meta shell + selective safetensors load
├── InferenceEngine.py     # 单节点完整推理
├── DistributedEngine.py   # simulation + remote-forward hook
├── ClusterTypes.py        # NodeProfile / PlacementPlan / ReconfigurationPlan
├── NodeInventory.py       # NodeState / ClusterState / 本机节点快照
├── DeviceProfile.py       # 兼容旧 NodeProfile 的配置提取器
├── ClusterPlanner.py      # cold-start DP + replan DP + diff helper
├── ClusterCoordinator.py  # plan / replan / prepare / commit / barrier
├── NodeRuntime.py         # 节点侧 action 执行与 request accounting
├── ShardRegistry.py       # shard state / inflight / draining / release
├── ModelSplitter.py       # 遗留路线：整模型加载后裁剪
└── __pycache__/

test/inference/
├── test_shard_config.py
├── test_shard_loader.py
├── test_model_registry.py
├── test_llama_shard.py              # Llama：需 HF_TOKEN 或本地 snapshot
├── test_local_demo.py
├── test_package_exports.py
├── test_runtime_lifecycle_demo.py
└── test_delivery_demo.py

test/p2p/
├── test_device_profile.py
├── test_node_inventory.py
├── test_static_cluster_planner.py
├── test_dp_cluster_planner.py
├── test_cluster_coordinator.py
├── test_runtime_lifecycle.py
├── test_remote_distributed_engine.py
├── test_sticky_replanner.py
└── test_cluster_replan_flow.py

scripts/
├── delivery_demo.py                 # 统一交付展示入口
├── local_demo.py                    # 真实单机 / distributed-sim 推理演示
├── cluster_planner_demo.py          # 交互式 planner / join / drop 演示
├── runtime_lifecycle_demo.py        # prepare / commit / draining 演示
├── scan_local_hardware.py           # 本机节点状态扫描
└── demo_rendering.py                # demo 共用文本渲染 helper
```

如果你是 **算法协作者**，最应该先看的是：

- `NodeInventory.py`
- `ClusterTypes.py`
- `ClusterPlanner.py`

如果你是 **p2p / orchestration 协作者**，最应该先看的是：

- `ClusterTypes.py`
- `ClusterCoordinator.py`
- `NodeRuntime.py`
- `ShardRegistry.py`

如果你是 **演示 / 验证 / app 接线协作者**，先看：

- `scripts/delivery_demo.py`
- `scripts/cluster_planner_demo.py`
- `scripts/runtime_lifecycle_demo.py`
- `scripts/scan_local_hardware.py`

## 关键接口

### 1. 核心数据模型：系统交换的“真对象”

这里最重要的不是函数，而是四个数据模型，因为 planner、runtime、demo、以后接 p2p 时，边界都围绕它们展开。

`NodeState`（`NodeInventory.py`）是当前唯一 canonical node model。它表达节点的：

- `node_id`
- `device_type`
- `free_memory_gb`
- `kv_headroom_gb`
- `max_blocks_capacity`
- `block_latency_ms` / `block_throughput`
- `loaded_ranges`
- `online`

派生方法最关键的是：

- `effective_capacity_blocks(...)`
- `effective_speed()`

`PlacementPlan`（`ClusterTypes.py`）是 planner 的直接输出。它包含：

- `model_id`
- `assignments: list[ShardAssignment]`
- `coordinator_id`

每个 `ShardAssignment` 表达：

- `node_id`
- `start_layer`
- `end_layer`
- `role` (`first` / `middle` / `last`)
- `source_node_id`

`ReconfigurationPlan`（`ClusterTypes.py`）是 coordinator 面向执行层的输出。它把新旧 plan 的差值组织成：

- `actions_by_node: dict[node_id, list[NodeReconfigurationAction]]`

`ShardRecord`（`ShardRegistry.py`）是节点本地 runtime 的真实状态。它不是 planner 视角，而是执行视角，当前至少跟踪：

- `state`
- `active_owner`
- `pending_unload`
- `inflight_requests`
- `last_prepare_ts`
- `last_commit_ts`

如果只记一件事，请记这个映射：

```text
NodeState -> PlacementPlan -> ReconfigurationPlan -> ShardRecord
```

这就是当前 inference 子系统的主数据流。

`ShardConfig.dtype` 现在已经进入真实加载链路，但语义是“**config 优先，未指定时保留原生权重 dtype**”。也就是说，`ShardLoader` 会先检查 `ShardConfig.dtype`；如果它是：

- `float16`
- `bfloat16`
- `float32`

那么 shell 创建和 layer / embed / norm / lm_head materialize 都会显式收敛到这个 dtype；如果 `dtype=None`，则会从实际加载到的 safetensors tensor 推断 native dtype，并沿用它，而不是再强制 cast 到某个固定默认值。

### 2. 给算法协作者的入口：哪些函数是真正可替换点

算法协作者最应该关心的不是 runtime 细节，而是下面这些入口：

- `plan_static_distribution(model_id, total_layers, nodes, layer_work=None)`
- `replan_distribution(current, nodes, total_layers, layer_work=None)`
- `choose_coordinator(nodes, total_layers=None, current_plan=None)`
- `diff_assignment_changes(current, new)`

当前默认目标函数：

- cold start：最小化 **estimated bottleneck stage time**
- replan：在 bottleneck 的基础上，加 migration / instability 惩罚

当前近似 / heuristic 的位置：

- `_estimate_stage_cost(...)`
- `_estimate_migration_cost(...)`
- `_arrange_nodes_for_pipeline(...)`
- `estimate_reference_stage_width(...)`

如果以后要继续做更强的算法，最自然的替换点也就是这些位置，而不是改 coordinator 或 runtime。

### 3. 给 p2p / orchestration 协作者的入口：系统现在期待什么

p2p 协作者最应该关心的是 coordinator/runtime 的消息语义，而不是 DP 怎么写。

当前 coordinator 入口：

- `build_plan(profiles)`
- `replan(profiles)`
- `build_reconfiguration(profiles)`
- `broadcast_plan(plan)`
- `begin_reconfiguration(reconfiguration)`
- `mark_reconfig_ready(node_id)`
- `debug_snapshot()`

当前 runtime 入口：

- `attach_transport(transport, on_ready=...)`
- `handle_cluster_plan(payload)`
- `handle_reconfiguration_prepare(payload)`
- `handle_reconfiguration_commit(payload)`
- `prepare_reconfiguration(model_id, actions)`
- `commit_reconfiguration(actions)`
- `begin_request(shard_key)`
- `finish_request(shard_key)`
- `debug_snapshot()`

当前 action vocabulary 已固定：

- `keep`
- `load`
- `move_in`
- `unload`

当前 transport / orchestration 语义已固定为两阶段：

- `cluster_reconfigure_prepare`
- `cluster_reconfigure_commit`

需要明确的是，**这套 inference 子系统现在只保证本地 lifecycle 与 barrier 语义正确**。还没做的仍然属于后续 `p2p/`：

- retry
- timeout recovery
- rollback
- disconnect handling
- true peer-to-peer weight transfer

### 4. 给加载 / 模型协作者的入口：懒加载链路从哪里进入

加载相关真正要看的是：

- `ShardLoader(config, device).load()`
- `ModelShard.forward(...)`
- `ModelRegistry.get(...)`

如果你关心模型家族差异，要优先看：

- `ModelRegistry.py`
- `ShardLoader.py`

如果你关心 role-aware assembly，要看：

- first shard：`embed_tokens`
- middle shard：`layers`
- last shard：`layers + norm + lm_head`

Llama 有一个必须明确写出的运行条件：**Llama 相关验证默认需要 `HF_TOKEN` 或本地 snapshot**。Qwen 的公开小模型更适合直接在线拉取；Llama 这条线不能默认假设匿名下载可行。因此：

- 要么配置 `HF_TOKEN`
- 要么把本地模型目录作为 `model_id` / 路径传入

## 关键逻辑

### 1. 先讲边界：`inference/` 负责什么，不负责什么

`inference/` 当前负责四件事：第一，识别节点能力并形成 `NodeState`；第二，把节点集合变成 placement；第三，把 placement 变化变成可执行的 shard lifecycle；第四，把这些状态通过 demo / snapshot 暴露出来。它不负责最终网络闭环，不负责 retry / rollback，不负责 UI，也不负责以后更高级的 placement 算法研究本身。

如果把整个子系统压成一句话，就是：

```text
用节点状态驱动 placement，用 placement 驱动 shard lifecycle。
```

### 2. 节点进入 / 退出 / 动态分布：完整过程

当前 join / leave 不是“节点集合变了就简单平均重切”，而是一个四步链路：

1. `NodeInventory` 把输入节点统一规范化为 `NodeState`
2. `ClusterPlanner` 生成新的 `PlacementPlan`
3. `diff_assignment_changes(...)` 把 old/new plan 变成 `keep / load / move_in / unload`
4. `ClusterCoordinator` + `NodeRuntime` 用 prepare / commit 把它真正执行出去

#### cold start

`plan_static_distribution(...)` 做的是 **连续切分 DP**，目标是最小化最慢 stage 的估计时间，而不是让每个节点分到相同层数。这里的 stage cost 主要由三部分组成：

- 该段层工作量 / 节点速度
- `first` / `last` 角色惩罚
- memory pressure / 容量约束

因此强节点会自然拿更大的段，但仍然受 capacity 约束。

#### join / leave

`replan_distribution(...)` 并不是“能不动就不动”这么简单。它先检查当前 assignment 是否还能完全保留；只有在 **节点集合没变** 且所有 assignment 仍然可保留时，才直接返回 current。只要：

- 有新节点加入
- 有旧节点离开
- 现有 assignment 已经不再可行

它就会重新做一个带 migration / instability 惩罚的 DP，让边界可以调整，而不是只换 owner 不动边界。

#### old/new plan 如何变成动作

`diff_assignment_changes(current, new)` 会把 plan 差值翻译成每个节点自己的动作集：

- shard 还是原 owner：`keep`
- 新 owner 且有旧 owner：`move_in`
- 新 owner 且没有旧 owner：`load`
- 旧 owner 不再持有：`unload`

#### prepare / commit

coordinator 不会在新 plan 算出来后立即切换，而是走两阶段：

- `prepare`：目标节点先把 shard 载入到 `ready`
- `commit`：只有相关节点都 ready 后，才提升新 owner 为 `serving`

旧 owner 的 `unload` 也不会立刻执行，而是可能转成 `draining`。

### 3. 一个真实 example：`node-d` 加入后发生了什么

用当前 demo 里真实出现过的场景来说：

初始只有三个在线节点：

- `node-a`
- `node-b`
- `node-c`

cold start 可能得到：

- `node-a: 0-12 (first)`
- `node-c: 12-16 (middle)`
- `node-b: 16-24 (last)`

这说明：

- `node-a` 拿了比较大的前段
- `node-c` 很弱，只拿了很小的一段
- `node-b` 负责尾段

当更强的 GPU 节点 `node-d` 加入后，replan 可能变成：

- `node-d: 0-16 (first) <- node-a`
- `node-b: 16-21 (middle)`
- `node-c: 21-23 (middle) <- node-b`
- `node-a: 23-24 (last) <- node-b`

这里的含义是：

- `node-d` 接走了前面的大段
- `node-a` 不再扛前段，而是只保留一个很小的尾段
- `node-b` 被切小
- `node-c` 继续只拿较小范围

然后系统不会直接删掉旧 shard，而是：

1. prepare：`node-d` 先本地 reload `0-16`
2. commit：`node-d` 变成 `serving`
3. 旧 owner 如果还有 inflight 请求，则变成 `draining`
4. inflight 清零后旧 shard 才真正释放

### 4. 运行期安全：为什么现在不会“新 shard 未 ready，旧 shard 先消失”

当前运行时安全的核心不在 planner，而在 `NodeRuntime` + `ShardRegistry`。

`ShardRecord` 现在跟踪：

- `state`
- `active_owner`
- `pending_unload`
- `inflight_requests`
- `last_prepare_ts`
- `last_commit_ts`

关键语义是：

- `serving`：可以接新请求
- `ready`：已准备好，但还未开始 serving
- `draining`：不再接新请求，只等旧请求完成

在 `commit_reconfiguration(...)` 里：

- `keep/load/move_in` 会被提升为 `serving`
- `unload` 如果对应 shard 还有 inflight 请求，不会立刻 remove，而是转成 `draining`
- 只有 `draining + pending_unload + inflight_requests == 0` 时才真正释放

因此现在已经有最小运行期安全保证：

> 新 owner 先 ready，再 commit；old owner 如有旧请求，先 drain，再 release。

### 5. 懒加载：现在到底懒在什么地方

当前 `ShardLoader` 不是“整模型加载后裁剪”，而是：

1. 通过本地路径或 `snapshot_download(...)` 定位模型
2. 读取 `model.safetensors.index.json`
3. 用 `meta` device 建立空壳 `AutoModelForCausalLM`
4. 只根据当前 shard 的角色和边界，筛选需要的 key 前缀
5. 只 load 这些 key 所在的 safetensors 文件
6. 只把当前 shard 需要的模块 `assign=True` 到实际 device

所以这里懒在两层：

- **结构懒**：先是 meta shell，不分配整模型权重
- **权重懒**：只 materialize 当前 shard 真正需要的 tensor

#### role-aware assembly

当前 `_assemble_shard()` 是按角色组装的：

- first shard：`embed_tokens + layers`
- middle shard：`layers`
- last shard：`layers + norm + lm_head`

这就是为什么不同 shard 不是对称的。

#### Qwen / Llama 差异

Qwen 的 `rotary_emb` 是计算型模块，不在 safetensors 中，所以不能简单从 meta shell `.to(device)`；当前会根据 `model_config` 在目标 device 上重建。Qwen 最后一 shard 还需要处理 weight tying，因为 `lm_head.weight` 可能共享 `embed_tokens.weight`。

Llama 这边也要强调两点：

1. 当前环境下，Llama 同样需要正确处理 rotary / `position_embeddings`
2. **Llama 权重获取默认需要 `HF_TOKEN` 或本地 snapshot`**

因此 README 里把这个条件写成显式运行约束，而不是藏在测试文件名里。

### 6. 演示与观察：现在哪些脚本对应哪种心智模型

当前最好把 demo 分成三类理解：

#### `scripts/local_demo.py`
看的是“真实单机 / distributed-sim 推理”

#### `scripts/cluster_planner_demo.py`
看的是“节点加入/退出后，planner 如何重分布”

#### `scripts/runtime_lifecycle_demo.py`
看的是“prepare / commit / draining 如何执行”

#### `scripts/scan_local_hardware.py`
看的是“本机被扫描成什么样的 `NodeState`”

#### `scripts/delivery_demo.py`
看的是“把上面这些收成一个统一交付入口”

换句话说，这几个脚本不是重复功能，而是在展示不同平面：

- load plane
- planner plane
- lifecycle plane
- status plane

## 当前状态 / 验证 / 下一步

### 当前已经具备的能力

现在的 `inference/` 已经不是“本地跑个 Qwen demo”那么简单了。它已经具备：

- `NodeState` 级节点建模
- cold-start DP placement
- join / leave replan
- old/new placement diff
- prepare / commit reconfiguration
- shard-level runtime safety（`serving / ready / draining`）
- inflight accounting
- demo / snapshot / delivery CLI

也就是说，**算法、p2p、app** 三条后续主线现在都已经有清晰的接点。

### 当前还没完成的事

需要明确写给协作者看的限制有这些：

1. `move_in` 现在仍然是 **destination-local reload**，不是 peer-to-peer 权重传输  
2. 现在的运行期安全是 **shard-range 级别**，不是 mid-token request migration  
3. retry / rollback / disconnect recovery 仍然属于 `p2p/` 后续  
4. status 目前还是 demo/snapshot 级别，不是正式在线 API  
5. `_arrange_nodes_for_pipeline(...)` 仍然不是全局最优排列搜索器，只是共享 metric 下的轻量启发式

### 验证入口

这几个验证命令是当前最值得跑的：

```bash
python3.11 scripts/local_demo.py --mode both
printf 'start node-a,node-b,node-c\njoin node-d\nshow\nquit\n' | python3.11 scripts/cluster_planner_demo.py
python3.11 scripts/runtime_lifecycle_demo.py
python3.11 scripts/scan_local_hardware.py --json
python3.11 scripts/delivery_demo.py all
```

相关测试：

```bash
python3.11 -m pytest -q --tb=short test/inference/test_delivery_demo.py test/inference/test_runtime_lifecycle_demo.py test/inference/test_local_demo.py test/p2p/test_cluster_planner_demo.py test/p2p/test_runtime_lifecycle.py test/p2p/test_cluster_coordinator.py
```

如果你要做更完整的 inference 验证，可以继续跑：

```bash
python3.11 -m pytest -q --tb=short test/inference test/p2p/test_cluster_coordinator.py test/p2p/test_cluster_planner_demo.py test/p2p/test_cluster_replan_flow.py test/p2p/test_coordinator_metrics.py test/p2p/test_device_profile.py test/p2p/test_dp_cluster_planner.py test/p2p/test_node_inventory.py test/p2p/test_runtime_lifecycle.py test/p2p/test_scan_local_hardware.py test/p2p/test_static_cluster_planner.py test/p2p/test_sticky_replanner.py
```

这组验证当前有两个边界条件需要明确写清楚：第一，`test/inference/test_llama_shard.py` 依赖 `HF_TOKEN` 或 `HUGGINGFACE_HUB_TOKEN`，否则会因为模型访问凭证缺失而报错；第二，`test/p2p/test_remote_distributed_engine.py` 触碰的是 `p2p/` 行为，不应和纯 `inference/` 主线 blocker 混为一谈。

### 我建议的下一步

如果下一步是 **给算法同事继续做**，重点应该放在：

- `ClusterPlanner.py`
- 更严谨的 footprint / cost model
- 更强的 pipeline ordering / placement objective

如果下一步是 **给 p2p 同事继续做**，重点应该放在：

- `ClusterCoordinator.py`
- `NodeRuntime.py`
- transport message reliability
- retry / rollback / disconnect handling

如果下一步是 **给 app 同事继续做**，重点应该放在：

- 把 `debug_snapshot()` 接成 status API
- 把 delivery/status/planner/lifecycle 信息接入前端

这也是这次 README 重构的目的：让不同协作者不需要先读完整个历史过程，就能知道自己该从哪一层接手。
