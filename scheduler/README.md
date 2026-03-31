# Scheduler Module — Planning / Orchestration 协作说明

## 文件结构

`scheduler/` 负责 **节点画像、placement、replan、coordinator、cluster-level orchestration**。它不负责 shard 权重加载和本地执行，也不负责真实 transport。

```text
scheduler/
├── __init__.py
├── ClusterTypes.py         # NodeProfile / PlacementPlan / ReconfigurationPlan
├── ClusterPlanner.py       # static planning / DP replanning / diff helper / metrics
├── ClusterCoordinator.py   # plan / replan / prepare / commit / barrier
├── NodeInventory.py        # NodeState / ClusterState / 本机节点快照
├── DeviceProfile.py        # 兼容旧 NodeProfile 的 profile helper
└── README.md

test/scheduler/
├── test_cluster_coordinator.py
├── test_cluster_planner_demo.py
├── test_cluster_replan_flow.py
├── test_coordinator_metrics.py
├── test_device_profile.py
├── test_dp_cluster_planner.py
├── test_node_inventory.py
├── test_scan_local_hardware.py
├── test_static_cluster_planner.py
└── test_sticky_replanner.py
```

如果你关心的是“谁负责哪段层、join/leave 后如何变、哪个节点更适合当 coordinator”，应该先看这里，而不是 `inference/`。

## 关键接口

### 1. 核心数据模型

最重要的对象是：

- `NodeProfile`
- `NodeState`
- `ShardAssignment`
- `PlacementPlan`
- `NodeReconfigurationAction`
- `ReconfigurationPlan`

其中最关键的数据流是：

```text
NodeState -> PlacementPlan -> ReconfigurationPlan
```

`NodeState` 是更现代的 canonical node model；`NodeProfile` 当前更多承担兼容层角色。

### 2. planner / replanner 入口

最关键的 planner 接口是：

- `plan_static_distribution(...)`
- `replan_distribution(...)`
- `diff_assignment_changes(...)`
- `choose_coordinator(...)`
- `coordinator_score(...)`
- `estimate_reference_stage_width(...)`
- `estimate_stage_time_for_node(...)`

如果以后要做更高级的分配算法，这些函数就是最自然的替换点。

### 3. coordinator 入口

`ClusterCoordinator` 是 scheduler 层面向执行层的 orchestration 入口，当前最重要的方法是：

- `build_plan(...)`
- `replan(...)`
- `build_reconfiguration(...)`
- `broadcast_plan(...)`
- `broadcast_reconfiguration(...)`
- `begin_reconfiguration(...)`
- `mark_reconfig_ready(...)`
- `debug_snapshot()`

它负责编排 phases、ready barrier、prepare/commit 时间线，但不负责本地 shard 怎么加载。

### 4. inventory / hardware snapshot 入口

如果你关心本机或节点画像，重点看：

- `build_node_state(...)`
- `from_local_snapshot(...)`
- `snapshot_local_node_profile(...)`
- `build_node_profile(...)`

`scripts/scan_local_hardware.py` 也已经基于这些入口工作。

## 关键逻辑

### 1. scheduler 的职责边界

`scheduler/` 只负责决定：

- 哪些节点参与
- 每个节点承载哪些连续 shard
- 旧 plan 和新 plan 的差值是什么
- coordinator 如何选择与推进重配置阶段

它不负责：

- 本地 shard 权重如何加载
- request inflight / draining
- 真实 p2p transport / signal server / retry / rollback

### 2. cold start 与 replan

当前 cold start 是连续切分 planner，目标是最小化 bottleneck stage time；replan 则在这个基础上加上迁移代价和边界稳定性约束。sticky replanning、loaded shard 偏好、coordinator metrics 都已经体现在这一层，而不是执行层。

### 3. prepare / commit 与执行层的关系

`scheduler/` 负责生成 `ReconfigurationPlan`，里面按节点组织 `keep / load / move_in / unload`。真正执行这些动作的是 `inference/NodeRuntime`。也就是说：

- `scheduler/` 决定 **谁做什么**
- `inference/` 决定 **本地怎么做**
- `p2p/` 决定 **消息怎么送到**

## 当前状态 / 验证 / 下一步

当前 scheduler 已从 `inference/` 中抽成顶层并列模块，调度与执行的边界比之前清楚很多。默认验证入口是：

- `python3.11 -m pytest -q --tb=short test/scheduler`
- `make test-scheduler`
- `python3.11 scripts/cluster_planner_demo.py`
- `python3.11 scripts/scan_local_hardware.py`

当前已知限制是：`p2p/` 还没有一起重构，因此 transport 相关测试和代码仍保留在原目录；本轮只处理 scheduler 与 inference 的职责拆分，不触及真正的网络闭环。
