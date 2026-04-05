# ParaMind desktop 打包和分发指南

## 概述

本指南将帮助您将 ParaMind desktop 打包成可分发的安装程序，并说明当前可用的打包前提。

## 网络通信原理

### 本地网络通信
- 应用启动时自动启动Python Flask后端服务器
- 后端服务器绑定到 `0.0.0.0:5001`，允许局域网内其他设备访问
- 前端通过HTTP API与后端通信
- 支持多设备同时连接同一个聊天室

### 设备间通信流程
1. 设备A启动应用，成为服务器
2. 设备A显示其IP地址和端口
3. 设备B启动应用，连接到设备A的IP地址
4. 两台设备可以实时聊天

## 打包步骤

### 1. 安装依赖

```bash
cd /Users/acropolis/Github_Project/Paramind/paramind/apps/desktop
uv sync
npm ci
```

开发环境以 `uv` 管理 Python 依赖；`requirements.txt` 仅用于 `npm run build:python` 生成 portable Python 运行时。

### 2. 创建应用图标

创建 `assets` 文件夹并添加图标文件：
- `icon.ico` (Windows, 256x256)
- `icon.icns` (macOS, 512x512)
- `icon.png` (Linux, 512x512)

### 3. 打包命令

```bash
# 开发环境测试
npm start

# 构建 portable Python
npm run build:python

# 打包所有平台
npm run build

# 打包特定平台
npm run build:win    # Windows
npm run build:mac    # macOS
npm run build:linux  # Linux

# 仅打包不创建安装程序
npm run pack
```

### 4. 输出文件

打包完成后，安装程序将位于 `dist/` 文件夹：
- Windows: `ParaMind Setup.exe`（当前 portable Python 仍未默认支持）
- macOS: `ParaMind.dmg`
- Linux: `ParaMind.AppImage`

## 当前平台支持状态

- macOS Apple Silicon：已配置默认 portable Python 资产
- macOS Intel：已配置默认 portable Python 资产
- Linux x86_64：已配置默认 portable Python 资产
- Windows：Electron metadata 已保留，但 `scripts/build-python.sh` 还没有默认 Windows portable Python 资产；在补齐并完成 smoke test 之前，不应把 Windows 视为 ready

## Windows 本地开发脚本

仓库现在额外提供了一个实验性的双实例启动脚本：

```powershell
npm run start:chat:win
```

它会尝试：
- 清理 `9010 / 5001 / 5002` 端口上的残留监听进程
- 启动两个 Electron 实例
- 为两个实例分别注入 `peer-a` / `peer-b` 的环境变量

这只是一个测试性质的 Windows 入口，不代表 Windows 已经 fully supported。如果你的 PowerShell、npm 路径或本地网络权限与默认假设不同，仍然需要自行调整 `scripts/start_chat.ps1`。

## 分发和安装

### Windows
1. 分发 `ParaMind Setup.exe`
2. 用户双击运行安装程序
3. 安装程序会：
   - 安装应用到 Program Files
   - 创建桌面快捷方式
   - 创建开始菜单项
   - 自动启动应用

### macOS
1. 分发 `ParaMind.dmg`
2. 用户双击DMG文件
3. 拖拽应用到Applications文件夹
4. 首次运行可能需要允许安全设置

### Linux
1. 分发 `ParaMind.AppImage`
2. 用户下载后：
   ```bash
   chmod +x ParaMind.AppImage
   ./ParaMind.AppImage
   ```

## 网络通信测试

### 单设备测试
1. 启动应用
2. 输入用户名加入聊天室
3. 发送消息测试基本功能

### 多设备测试
1. 设备A启动应用，记录显示的IP地址
2. 设备B启动应用，在连接设置中输入设备A的IP地址
3. 两台设备分别输入不同用户名
4. 测试消息发送和接收

### 网络要求
- 设备必须在同一局域网内
- 防火墙需要允许5001端口通信
- 路由器不需要特殊配置

## 故障排除

### 常见问题

1. **无法连接到其他设备**
   - 检查防火墙设置
   - 确认设备在同一网络
   - 验证IP地址正确

2. **Python后端启动失败**
   - 检查Python环境是否正确打包
   - 查看应用日志输出

3. **消息发送失败**
   - 检查网络连接
   - 确认后端服务器正在运行

### 调试模式

开发环境下可以启用调试：
```bash
NODE_ENV=development npm start
```

## 高级配置

### 自定义端口
修改 `backend.py` 中的端口设置：
```python
app.run(host='0.0.0.0', port=YOUR_PORT, debug=False)
```

### 修改应用信息
编辑 `package.json` 中的 `build` 配置：
```json
{
  "build": {
    "appId": "com.yourcompany.yourapp",
    "productName": "Your App Name",
    "directories": {
      "output": "dist"
    }
  }
}
```

## 安全考虑

1. **网络安全**
   - 应用仅监听局域网接口
   - 不包含敏感数据存储
   - 消息不持久化存储

2. **代码保护**
   - Python代码会被打包但未加密
   - 前端代码可通过开发者工具查看
   - 建议对敏感逻辑进行混淆

## 性能优化

1. **启动时间**
   - portable Python 会增加应用大小
   - 当前方案优先保证 packaged runtime 可独立运行，未做体积优化

2. **内存使用**
   - 消息历史限制为50条
   - 定期清理离线用户

3. **网络效率**
   - 使用轮询而非WebSocket（简化实现）
   - 可考虑升级到WebSocket提高效率

## 更新和维护

### 应用更新
1. 修改版本号
2. 重新打包
3. 分发新版本

### 依赖更新
```bash
# 更新Python依赖
pip install --upgrade -r requirements.txt

# 更新Node.js依赖
npm update
```

## 总结

通过以上步骤，您可以成功打包和分发Electron-Python聊天应用。应用支持：

✅ 跨平台运行（Windows/macOS/Linux）
✅ 局域网内多设备通信
✅ 自动Python后端管理
✅ 用户友好的安装程序
✅ 实时聊天功能

分发后的应用可以在不同设备间实现完整的聊天功能，无需额外配置。
