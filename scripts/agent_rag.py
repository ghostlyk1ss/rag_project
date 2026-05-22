#!/usr/bin/env python3
"""
finRAG Agent RAG — 命令行入口

基于 LangGraph 的金融文档智能问答系统。
支持：多轮对话、指代消解、意图路由、子问题拆解、代码解释器。

用法:
    python scripts/agent_rag.py "五粮液2025年营业收入是多少？"
    python scripts/agent_rag.py --verbose "对比五粮液和贵州茅台的财务状况"
    python scripts/agent_rag.py --interactive          # 交互模式
"""

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path

# 确保能找到 scripts 包
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.agents.graph import run_agent_query


def setup_logging(verbose: bool):
    """配置日志"""
    level = logging.INFO if verbose else logging.WARNING
    logging.basicConfig(
        level=level,
        format="%(levelname)s | %(message)s",
        force=True,
    )
    # 抑制第三方库日志
    for lib in ["httpx", "urllib3", "openai", "sentence_transformers",
                 "transformers", "qdrant_client"]:
        logging.getLogger(lib).setLevel(logging.WARNING)


def print_result(state: dict, elapsed: float):
    """格式化打印 Agent 回答"""
    answer = state.get("answer", "")
    error = state.get("error", "")
    intent = state.get("intent", "?")
    citations = state.get("citations", [])

    print(f"\n{'='*60}")
    print(f"🤖 回答 ({intent}) — {elapsed:.1f}s")
    print(f"{'='*60}")
    print(f"\n{answer}\n")

    if error:
        print(f"⚠️  错误: {error}\n")

    if citations:
        print(f"📚 引用来源:")
        for c in citations:
            print(f"  [{c['index']}] {c['company']} {c['year']} | {c['text'][:100]}...")
        print()

    # 如果 verbose，打印中间状态
    if state.get("rewritten_queries"):
        print(f"🔄 多路查询: {[q['query'][:50] for q in state['rewritten_queries']]}")
    if state.get("sub_questions"):
        print(f"📋 子问题: {[q['question'][:50] for q in state['sub_questions']]}")
    if state.get("code"):
        print(f"💻 生成的代码 ({len(state['code'])} 字)")
    if state.get("code_output"):
        print(f"📊 代码结果: {state['code_output'][:200]}")
    if state.get("code_error"):
        print(f"❌ 代码错误: {state['code_error'][:200]}")


def single_query(query: str, verbose: bool = False):
    """单次查询"""
    setup_logging(verbose)
    print(f"🔍 查询: {query}")
    print(f"正在分析...")

    t0 = time.perf_counter()
    state = run_agent_query(query, verbose=verbose)
    elapsed = time.perf_counter() - t0

    print_result(state, elapsed)


def interactive_mode(verbose: bool = False):
    """交互式对话模式"""
    setup_logging(verbose)

    messages = []
    session_id = f"session_{int(time.time())}"
    print(f"\n{'='*60}")
    print(f"💬 finRAG Agent — 交互模式（输入 /exit 退出，/help 帮助）")
    print(f"{'='*60}\n")

    while True:
        try:
            query = input("你: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if not query:
            continue

        if query == "/exit":
            break
        if query == "/help":
            print("  /exit 退出  /clear 清空历史  /verbose 切换详细模式")
            continue
        if query == "/clear":
            messages = []
            print("✅ 对话历史已清空")
            continue
        if query == "/verbose":
            verbose = not verbose
            setup_logging(verbose)
            status = "开" if verbose else "关"
            print(f"✅ 详细模式: {status}")
            continue

        t0 = time.perf_counter()
        state = run_agent_query(query, messages=messages, session_id=session_id, verbose=verbose)
        elapsed = time.perf_counter() - t0

        # 打印回答
        answer = state.get("answer", "")
        error = state.get("error", "")
        intent = state.get("intent", "?")
        print(f"\n🤖 [Agent] ({intent}):")
        print(answer)
        if error:
            print(f"\n⚠️  {error}")

        # 更新对话历史
        messages.append({"role": "user", "content": query})
        messages.append({"role": "assistant", "content": answer})

        print()


def main():
    parser = argparse.ArgumentParser(
        description="finRAG Agent — 金融文档智能问答系统",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python scripts/agent_rag.py "五粮液2025年营业收入"
  python scripts/agent_rag.py -v "对比五粮液和贵州茅台的盈利能力"
  python scripts/agent_rag.py --interactive
        """,
    )
    parser.add_argument("query", nargs="*", help="查询问题")
    parser.add_argument("-v", "--verbose", action="store_true", help="详细输出（显示中间步骤）")
    parser.add_argument("-i", "--interactive", action="store_true", help="交互模式")
    parser.add_argument("--json", action="store_true", help="JSON 格式输出（仅单次查询）")

    args = parser.parse_args()

    query = " ".join(args.query) if args.query else ""

    if args.interactive or not query:
        interactive_mode(verbose=args.verbose)
    else:
        single_query(query, verbose=args.verbose)


if __name__ == "__main__":
    main()
