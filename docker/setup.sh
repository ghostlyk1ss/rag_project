#!/usr/bin/env bash
# finRAG — Docker 初始化脚本
# 创建必要的目录结构，确保数据持久化目录存在。
set -e

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_DIR"

echo "========================================"
echo " finRAG Docker 初始化"
echo "========================================"

echo ""
echo "[1/2] 创建数据目录..."
mkdir -p docker/data/qdrant_db
mkdir -p docker/data/parsed
mkdir -p docker/data/raw
mkdir -p docker/cache/huggingface
echo "  ✅ docker/data/qdrant_db/"
echo "  ✅ docker/data/parsed/"
echo "  ✅ docker/data/raw/"
echo "  ✅ docker/cache/huggingface/"

echo ""
echo "[2/2] 检查环境变量..."
if [ ! -f .env ]; then
    if [ -f .env.example ]; then
        cp .env.example .env
        echo "  ⚠️  已从 .env.example 生成 .env"
        echo "  ➡️  请编辑 .env 填入 DeepSeek API Key"
    else
        echo "  ⚠️  未找到 .env 文件"
        echo "  ➡️  请创建 .env 并参考 README 配置"
    fi
else
    echo "  ✅ .env 已存在"
fi

echo ""
echo "========================================"
echo " ✅ 初始化完成！"
echo ""
echo "下一步："
echo "  docker compose build     # 构建镜像（首次较慢）"
echo "  docker compose up -d     # 启动服务"
echo "  docker compose logs -f   # 查看日志"
echo ""
echo "访问：http://localhost"
echo "========================================"
