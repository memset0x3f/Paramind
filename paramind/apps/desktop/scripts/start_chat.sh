#!/bin/bash

# Run from project root regardless of where this script is called
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

COORDINATOR_PORT="${PARAMIND_COORDINATOR_PORT:-9010}"
BASE_DATA_DIR="${PARAMIND_APP_DATA_DIR:-$ROOT_DIR/.paramind-local}"
mkdir -p "$BASE_DATA_DIR"

# Each Electron instance now starts its own FastAPI backend automatically.
echo "启动第一个 Electron 实例..."
PARAMIND_COORDINATOR_PORT="$COORDINATOR_PORT" \
PARAMIND_INSTANCE_ID="peer-a" \
PARAMIND_INSTANCE_NAME="Peer A" \
PARAMIND_BACKEND_PORT="5001" \
PARAMIND_INSTANCE_DATA_DIR="$BASE_DATA_DIR/peer-a" \
npm start &
ELECTRON1_PID=$!

# Wait a moment
sleep 2

# Start second Electron instance
echo "启动第二个 Electron 实例..."
PARAMIND_COORDINATOR_PORT="$COORDINATOR_PORT" \
PARAMIND_INSTANCE_ID="peer-b" \
PARAMIND_INSTANCE_NAME="Peer B" \
PARAMIND_BACKEND_PORT="5002" \
PARAMIND_INSTANCE_DATA_DIR="$BASE_DATA_DIR/peer-b" \
npm start &
ELECTRON2_PID=$!

echo "所有服务已启动!"
echo "Electron实例1 PID: $ELECTRON1_PID"
echo "Electron实例2 PID: $ELECTRON2_PID"
echo ""
echo "按 Ctrl+C 停止所有服务"

# Function to cleanup on exit
cleanup() {
    echo "正在停止所有服务..."
    kill $ELECTRON1_PID 2>/dev/null
    kill $ELECTRON2_PID 2>/dev/null
    pkill -f "electron" 2>/dev/null
    pkill -f "paramind.apps.desktop.python.backend:create_app" 2>/dev/null
    pkill -f "paramind.apps.desktop.python.app.coordinator" 2>/dev/null
    exit 0
}

# Set trap to cleanup on script exit
trap cleanup SIGINT SIGTERM

# Wait for any process to exit
wait
