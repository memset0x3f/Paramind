# ParaMind Desktop Renderer

## 文件结构

```
renderer/
├── index.html    # 主入口，HTML + CSS 设计系统（CSS变量、布局、组件样式）
├── chat.js      # 所有前端逻辑：状态管理、渲染、SSE推理、加入/退出流程
└── renderer.js  # Electron 主进程通信桥接（IPC）
```

## 核心状态（chat.js 第 3–45 行）

```javascript
state = {
  joinedConvId: null,      // 当前已加入的会话 ID（群/DM）
  joiningConvId: null,     // 加入/退出流程进行中的会话 ID
  joiningAction: null,      // 'join' | 'exit'
  aiBusy: false,           // AI 推理中标志
  draft: null,             // AI 推理草稿卡：{ convId, prompt, text, topo, generating, xhr, _lastPos, _pendingEvent, _firstTokenTime }
  lastRoute: null,         // 最近一次推理的拓扑信息：{ nodes, localNodeId, model, mode, device, memUsed, memTotal, utilization }
  conversations: [...]       // 所有会话（群聊 + DM）
  activeConvId: ...         // 当前活跃会话
}
```

## 关键代码位置

### 会话/加入流程
| 功能 | 位置 |
|------|------|
| JOIN/CONNECT 按钮点击 | `renderConvs()` 第 195–219 行，绑定 `.conv-action-btn.join` / `.connect-dm` |
| DISCONNECT/EXIT 按钮点击 | `renderConvs()` 第 201–214 行，绑定 `.conv-action-btn.exit` / `.exit-dm` |
| 加入逻辑（5s 模拟） | `handleJoin(convId)` 第 650–676 行 |
| 退出逻辑（5s 模拟） | `handleExit()` 第 683–698 行 |
| DM 连接成功 → 系统消息 | `handleJoin()` 内第 668–670 行 |
| 群聊加入 → 遗漏广播 | `_simulateJoinedBroadcasts(convId)` 第 663–680 行 |
| 成员点击 → 切换 DM | `renderSidebar()` 第 237–245 行 |
| 切换会话时自动断开 | `renderConvs()` 第 189–191 行 + `renderSidebar()` 第 242–244 行 |

### AI 推理（SSE）
| 功能 | 位置 |
|------|------|
| 启动推理 XHR | `startInference(prompt)` 第 544–599 行 |
| SSE 数据解析 | `_processSSEData(newData)` 第 493–558 行，处理 `route` / `token` / `done` 事件 |
| Draft 卡渲染 | `renderDraftCard()` 第 358–458 行 |
| Draft 卡 → 发送消息 | Draft 卡内 `#draftSend` 点击处理，第 444–454 行 |
| Draft 卡 → 复制文本 | Draft 卡内 `#draftCopy` 点击处理，第 440–442 行 |
| Draft 卡 → 关闭 | `dismissDraft()` 第 461–469 行 |
| 取消推理 | `cancelInference()` 第 620–622 行 |

### 渲染函数
| 函数 | 职责 |
|------|------|
| `renderAll()` | 调用 renderSidebar + renderChat + renderComposer |
| `renderSidebar()` | 渲染群聊列表、私聊列表、成员网格 |
| `renderChat()` | 渲染消息列表 + draft 卡 |
| `renderComposer()` | 根据 joinedConvId/aiBusy 状态设置输入框状态 |

### 组件 DOM ID（index.html）
| ID | 用途 |
|----|------|
| `groupList` | 群聊会话列表容器 |
| `dmList` | 私聊会话列表容器 |
| `memberList` | 成员网格容器 |
| `chatList` | 消息列表容器 |
| `composerInput` | 消息输入框 |
| `sendBtn` | 发送按钮 |
| `roomTitle` / `roomSub` | 聊天区标题/副标题 |
| `statusItemStatus` / `statusDot` / `statusLabel` | 状态栏 STATUS 项 |
| `statusModel` / `statusMode` / `statusDevice` | 状态栏模型/模式/设备信息 |
| `statusTTFT` / `statusTPS` | 状态栏首 token 延迟 / tokens per second |
| `statusLayers` / `statusMem` / `statusUtil` | 状态栏本机 layers / 内存占用 / GPU 利用率 |

## SSE 接口

**端点**: `POST http://localhost:5001/api/infer`
**请求体**: `{ prompt: string, mock?: bool }`
**响应**: `text/event-stream`

SSE 事件格式：
- `event: route` → `data: {"nodes": [{"id":"node-A","layers":"0-12"}], "localNodeId":"node-A", "model":"Qwen2.5-7B", "mode":"distributed", "device":"GPU×2", "memUsed":"2.1 GB", "memTotal":"16 GB", "utilization":"78%"}`
- `event: token` → `data: {"token":"字","ttft":0.234,"tps":20.5}`
- `event: done` → `data: {"tokens":120,"elapsed":5.2,"tps":23.1}`

## 本机节点高亮

AI 消息的拓扑详情中，本机节点以绿色高亮显示（`← 本机` 标记 + 绿色文字 + 发光圆点）。

拓扑详情由 `topoDetailHTML(nodes, localNodeId)` 生成，`localNodeId` 随消息一起存储（`pushMessage` 新增 `localNodeId` 参数）。

## 加入/退出状态机

```
idle ──[JOIN/CONNECT]──> joining (5s) ──[完成]──> joined
joined ──[EXIT/DISCONNECT]──> exiting (5s) ──[完成]──> idle
```

**一次只能加入一个会话**。切换会话时自动断开上一个。

## 开发调试

```bash
# 启动后端
cd paramind/apps/desktop/python
python backend.py

# 启动 Electron（从 desktop 目录）
npm start
# 或
electron .
```
