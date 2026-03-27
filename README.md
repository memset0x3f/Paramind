# ParaMind

## 文件结构

这个 README 现在只承担一件事：**作为仓库目录导航**。运行细节、算法解释、推理生命周期、desktop 行为、测试策略，都应该去各自子目录里的文档看，而不是继续堆在根 README 里。

```text
Paramind/
├── common/                    # 共享概念、常量、配置工具
├── docs/                      # 计划、设计记录、研究 diary、总览说明
├── inference/                 # 分片加载、planner、runtime lifecycle、demo 相关核心
├── p2p/                       # P2P / transport 相关实验与适配代码
├── paramind/apps/desktop/     # Electron + FastAPI + SQLite 桌面应用
├── scripts/                   # 根目录可直接运行的 demo / 辅助脚本
├── test/                      # backend / inference / p2p / common 测试
├── requirements.txt           # 根目录 Python 依赖
├── pytest.ini                 # pytest 配置
└── Makefile                   # 常用根目录命令入口
```

如果你是第一次进这个仓库，最常见的入口有四个：想看推理系统本身，就从 `inference/README.md` 开始；想看 desktop app，就从 `paramind/apps/desktop/` 开始；想跑演示，就看 `scripts/`；想看设计与决策演化，就进 `docs/`。

## 关键接口

根 README 不再承担子系统 API 手册的职责；这里仅保留“从哪里进入”的接口导航。

### 仓库级导航入口

- `inference/README.md`：`inference/` 子系统说明，重点讲 `NodeState -> PlacementPlan -> ReconfigurationPlan -> ShardRecord`、join/leave、lazy loading、runtime safety、delivery demo。
- `docs/plans/`：实现计划、设计草案、研究笔记。
- `docs/diary/`：按天记录的验证、决策和变更轨迹。
- `paramind/apps/desktop/`：当前最可用的桌面应用实现，里面包含 Electron 主进程、renderer、FastAPI backend、desktop 本地运行脚本。
- `scripts/delivery_demo.py`：统一交付展示入口；如果只想看一条完整展示链，先从这里跑。

### 目录到职责的快速映射

- `common/`：仓库早期共用的基础模块。
- `inference/`：现在最完整的推理控制面与执行面实现。
- `p2p/`：更偏 transport / networking 的实验区，不等于完整 app。
- `paramind/apps/desktop/`：当前用户可感知的产品原型。
- `scripts/`：给开发、调试、演示使用的命令行入口，不是生产服务。
- `test/`：按子系统分层的测试集合；`test/backend/` 更偏 desktop backend，`test/inference/` 和 `test/p2p/` 更偏 inference/runtime/planner。

## 关键逻辑

这个仓库现在不是“单一应用 + 单一运行面”，而是三条并行演进的线放在同一个 repo 里。`inference/` 负责分片加载、动态分布、runtime lifecycle 和演示脚本；`paramind/apps/desktop/` 负责 Electron + FastAPI + SQLite 的产品原型；`p2p/` 负责更底层的 transport / networking 实验。因此，阅读顺序不应该从根 README 里一路向下找实现，而应该先决定自己关注的是哪条线，再进入对应子目录。

根目录的 `scripts/` 是把这些能力串起来的“看得见的入口”。如果你想直接观察系统行为而不是先读源码，优先看 `scripts/delivery_demo.py`、`scripts/cluster_planner_demo.py`、`scripts/runtime_lifecycle_demo.py` 和 `scripts/scan_local_hardware.py`。如果你想理解背后的实现，再回到 `inference/README.md`。

## 当前状态 / 验证 / 下一步

根 README 当前只负责目录导航，不再承载子系统运行说明。现在最稳定的入口是：看推理系统就读 `inference/README.md`；看应用原型就看 `paramind/apps/desktop/`；看设计背景就看 `docs/plans/` 和 `docs/diary/`；要直接演示就跑 `scripts/delivery_demo.py`。
