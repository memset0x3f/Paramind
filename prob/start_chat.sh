#!/bin/bash

# Start Python backend server
echo "启动Python后端服务器..."
source venv/bin/activate
python backend.py &
BACKEND_PID=$!

# Wait for backend to start
sleep 3

# Start first Electron instance
echo "启动第一个Electron聊天室实例..."
npm start &
ELECTRON1_PID=$!

# Wait a moment
sleep 2

# Start second Electron instance
echo "启动第二个Electron聊天室实例..."
npm start &
ELECTRON2_PID=$!

echo "所有服务已启动!"
echo "Python后端 PID: $BACKEND_PID"
echo "Electron实例1 PID: $ELECTRON1_PID"
echo "Electron实例2 PID: $ELECTRON2_PID"
echo ""
echo "按 Ctrl+C 停止所有服务"

# Function to cleanup on exit
cleanup() {
    echo "正在停止所有服务..."
    kill $BACKEND_PID 2>/dev/null
    kill $ELECTRON1_PID 2>/dev/null
    kill $ELECTRON2_PID 2>/dev/null
    pkill -f "electron" 2>/dev/null
    pkill -f "python backend.py" 2>/dev/null
    exit 0
}

# Set trap to cleanup on script exit
trap cleanup SIGINT SIGTERM

# Wait for any process to exit
wait
