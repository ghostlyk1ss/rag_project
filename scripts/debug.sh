#!/usr/bin/env bash
# finRAG 快速调试入口
# 用法: bash scripts/debug.sh [选项] [查询语句]
# 示例:
#   bash scripts/debug.sh --stats              # 看统计
#   bash scripts/debug.sh --browse             # 浏览数据
#   bash scripts/debug.sh "五粮液营业收入"      # 混合检索
#   bash scripts/debug.sh --rerank "净利润"     # 混合+重排序
#   bash scripts/debug.sh --agent "五粮液2025年净利润是多少？"  # Agent RAG（新！）
#   bash scripts/debug.sh --filter year=2025 "营业收入"

cd "$(dirname "$0")/.."
source .venv/bin/activate

export HF_HUB_OFFLINE=1

# Agent RAG 模式
if [[ "$1" == "--agent" ]]; then
    shift
    python scripts/agent_rag.py "$@"
else
    python scripts/search.py "$@"
fi
