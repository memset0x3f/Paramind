# Inference Module — Runtime / Execution 协作说明

## 文件结构

`inference/` 现在专门负责 **模型 shard 加载、本地执行、distributed simulation、runtime lifecycle**。节点画像、分配、replan、coordinator 已经抽到并列的 `scheduler/` 模块。

```text
inference/
├── __init__.py            # 延迟导出；对外继续暴露 runtime 常用符号
├── ShardConfig.py         # shard 边界、角色、dtype 策略
├── ModelRegistry.py       # 模型家族 → 架构信息
├── ShardLoader.py         # 懒加载：meta shell + selective safetensors load
├── InferenceEngine.py     # 单节点完整推理
├── DistributedEngine.py   # simulation + remote-forward hook
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
├── test_runtime_lifecycle.py
├── test_runtime_lifecycle_demo.py
└── test_delivery_demo.py
```

如果你关心的是“本地 shard 到底怎么被加载、怎么执行、怎么 drain”，就先看：

- `ShardConfig.py`
- `ShardLoader.py`
- `NodeRuntime.py`
- `ShardRegistry.py`

如果你关心的是“节点怎么分层、怎么 replan、谁当 coordinator”，那已经不在这里了，应该去看 `scheduler/README.md`。

## 关键接口

### 1. 加载与执行入口

最关键的入口是：

- `ShardLoader(config, device).load()`
- `ModelShard.forward(...)`
- `LocalInferenceEngine.stream(...)`
- `DistributedInferenceEngine.stream(...)`

这里的核心职责是：根据 `ShardConfig` 只加载当前 shard 需要的那部分权重，并把它组装成可运行的 `ModelShard`。

### 2. runtime lifecycle 入口

runtime 侧最关键的入口是：

- `NodeRuntime.attach_transport(...)`
- `NodeRuntime.handle_cluster_plan(...)`
- `NodeRuntime.handle_reconfiguration_prepare(...)`
- `NodeRuntime.handle_reconfiguration_commit(...)`
- `NodeRuntime.begin_request(shard_key)`
- `NodeRuntime.finish_request(shard_key)`
- `NodeRuntime.debug_snapshot()`

本层并不计算 placement；它只负责执行已经下发到本节点的 assignment / reconfiguration actions，并保证 `ready -> serving -> draining -> release` 语义正确。

### 3. shard state 模型

`ShardRegistry.py` 中的 `ShardRecord` 是本地执行层的真状态，当前至少跟踪：

- `state`
- `active_owner`
- `pending_unload`
- `inflight_requests`
- `last_prepare_ts`
- `last_commit_ts`

runtime safety 的关键约束也都围绕它展开。

### 4. dtype 语义

`ShardConfig.dtype` 的语义现在是：**config 优先，未指定时保留原生权重 dtype**。

也就是说：

- 如果 `dtype` 是 `float16 / bfloat16 / float32`，`ShardLoader` 会显式收敛到这个 dtype
- 如果 `dtype=None`，则会从实际加载到的 safetensors tensor 推断 native dtype，并沿用它

这条规则只描述 execution/runtime 路径，不描述 scheduler 如何决定 placement。

Llama 相关验证默认仍需要 `HF_TOKEN` 或本地 snapshot；这一点没有因为 scheduler 抽取而变化。

## 关键逻辑

### 1. 本层职责边界

`inference/` 当前只回答两类问题：

1. 当前 shard 需要哪些权重，如何懒加载
2. 已经分配到本节点的 shard 如何 prepare / commit / drain

它不再回答：

- 哪些节点应该持有哪些层
- join / leave 后如何重分配
- coordinator 怎么选

这些逻辑已经移动到并列的 `scheduler/` 模块。

### 2. 懒加载

`ShardLoader` 的主流程仍然是：

1. 定位模型
2. 读取 `model.safetensors.index.json`
3. 建 meta shell
4. 只收集当前 shard 需要的 key
5. 只加载必要 safetensors 文件
6. 组装当前 shard 需要的 layer / embed / norm / lm_head

也就是说，`inference/` 依然是“按 shard selective load”，不是“整模型全载再裁剪”。

### 3. runtime lifecycle

当前 lifecycle 语义仍是：

- prepare: 新 shard 到 `ready`，旧 shard 标 `pending_unload`
- commit: 新 owner `serving`，旧 owner 如有 inflight 则 `draining`
- finish_request: inflight 归零后，draining shard 才真正 release

所以运行期安全依然是本层的核心职责。

## 当前状态 / 验证 / 下一步

当前 `inference/` 已经与 `scheduler/` 解耦：运行期执行逻辑继续留在这里，调度与分配逻辑则移到并列模块。现在最重要的验证入口是：

- `python3.11 -m pytest -q --tb=short test/inference`
- `python3.11 scripts/local_demo.py`
- `python3.11 scripts/runtime_lifecycle_demo.py`

默认的 `make test-inference` 当前会排除名字里带 `llama` 的测试，因为 Llama 验证需要额外 Hugging Face 凭证，不适合作为默认本地 gate。若需要完整验证，再单独运行相关 Llama 测试并提供 token。
