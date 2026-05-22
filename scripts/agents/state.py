"""
finRAG Agent — LangGraph State 定义

整个 Agent RAG 系统的全局状态，在 LangGraph 节点间传递。
"""

from typing import TypedDict, Any


class FinRAGState(TypedDict):
    """LangGraph 全局状态"""

    # ── 输入层 ──────────────────────────────────────────────────
    messages: list[dict]          # 完整对话历史 [{"role": ..., "content": ...}]
    query: str                    # 当前用户提问（已指代消解）
    session_id: str               # 会话 ID，用于记忆持久化

    # ── 查询转换层 ──────────────────────────────────────────────
    company: str                  # 识别出的公司名（如"五粮液"）
    year: str                     # 识别出的年份（如"2025"）
    rewritten_queries: list       # 多路查询结果
    hyde_document: str            # HyDE 假设文档

    # ── 意图层 ──────────────────────────────────────────────────
    intent: str                   # 意图: factual / comparative / computational / multi_entity
    intent_confidence: float      # 置信度
    entities: list[str]           # 查询中检测到的实体/公司列表

    # ── 子问题层 ────────────────────────────────────────────────
    sub_questions: list[dict]     # 子问题列表 [{"id": int, "question": str, "company": str, "year": str}]
    sub_results: list[dict]       # 子问题检索结果

    # ── 检索层 ──────────────────────────────────────────────────
    retrieved_chunks: list  # 原始检索到的块（RetrievalResult 对象列表）
    context: str                  # 处理后上下文（压缩/拼接后，喂给 LLM）
    structured_metrics: list[dict] # 结构化财务指标 [{"metric":"营业收入","value":4.0529e10,"unit":"元","year":2025},...]

    # ── 代码解释器层 ────────────────────────────────────────────
    code: str                     # LLM 生成的 Python 代码
    code_output: str              # 代码执行结果
    code_error: str               # 代码执行错误

    # ── 输出层 ──────────────────────────────────────────────────
    answer: str                   # 最终回答
    citations: list[dict]         # 引用来源 [{"text": ..., "company": ..., "year": ...}]
    error: str                    # 错误信息


def make_initial_state(session_id: str = "default") -> FinRAGState:
    """创建初始空状态"""
    return FinRAGState(
        messages=[],
        query="",
        session_id=session_id,
        company="",
        year="",
        rewritten_queries=[],
        hyde_document="",
        intent="",
        intent_confidence=0.0,
        entities=[],
        sub_questions=[],
        sub_results=[],
        retrieved_chunks=[],
        context="",
        structured_metrics=[],
        code="",
        code_output="",
        code_error="",
        answer="",
        citations=[],
        error="",
    )
