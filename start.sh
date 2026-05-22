#!/bin/bash
# finRAG 一键启动
# 用法: bash start.sh

set -e

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
BACKEND_PID=""
FRONTEND_PID=""

cleanup() {
    echo ""
    echo "🛑 正在关闭服务..."
    [ -n "$BACKEND_PID" ] && kill "$BACKEND_PID" 2>/dev/null
    [ -n "$FRONTEND_PID" ] && kill "$FRONTEND_PID" 2>/dev/null
    echo "✅ 已关闭"
    exit 0
}
trap cleanup SIGINT SIGTERM

cd "$PROJECT_DIR"

# 激活虚拟环境
source .venv/bin/activate

# 启动 Ollama（如果未运行）
if ! curl -s http://localhost:11434 > /dev/null 2>&1; then
    echo "🔄 启动 Ollama..."
    nohup ollama serve &> /tmp/ollama.log &
    OLLAMA_PID=$!
    for i in $(seq 1 15); do
        if curl -s http://localhost:11434 > /dev/null 2>&1; then
            echo "✅ Ollama 已就绪"
            break
        fi
        sleep 1
    done
fi

# 启动后端
echo "🚀 启动后端 (port 8000)..."
cd backend
nohup uvicorn main:app --host 0.0.0.0 --port 8000 --reload &> /tmp/finrag-backend.log &
BACKEND_PID=$!
cd ..

# 等待后端就绪
echo "⏳ 等待后端就绪..."
for i in $(seq 1 30); do
    if curl -s http://localhost:8000/health > /dev/null 2>&1; then
        echo "✅ 后端已就绪"
        break
    fi
    sleep 1
done

# 启动前端
echo "🚀 启动前端 (port 3000)..."
cd frontend
nohup npm run dev &> /tmp/finrag-frontend.log &
FRONTEND_PID=$!
cd ..

# 等待前端就绪
echo "⏳ 等待前端就绪..."
for i in $(seq 1 60); do
    if curl -s http://localhost:3000 > /dev/null 2>&1; then
        echo "✅ 前端已就绪"
        break
    fi
    sleep 1
done

# 打开浏览器
echo "🌐 正在打开浏览器..."
powershell.exe -Command "Start-Process 'http://localhost:3000'" 2>/dev/null || \
    xdg-open http://localhost:3000 2>/dev/null || \
    echo "请手动打开 http://localhost:3000"

echo ""
echo "======================================"
echo "  finRAG 已启动!"
echo "  Backend:  http://localhost:8000"
echo "  Frontend: http://localhost:3000"
echo "  按 Ctrl+C 关闭所有服务"
echo "======================================"

# 保持运行
wait
