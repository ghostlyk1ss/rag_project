"""
finRAG Agent — LangGraph 工作流图

编排所有 Agent RAG 节点，形成完整的多步推理流水线。

流程图：

用户输入 → QueryRewriter (指代消解+多路查询+HyDE)
  → IntentRouter
    → factual: HybridRetriever → AnswerGenerator
    → comparative: SubQuestionEngine → 并行检索 → AnswerGenerator
    → computational: LLM 写代码 → CodeInterpreter → AnswerGenerator
      → 最终回答 (带引用)

每个节点可以独立失败，失败时走 error_handler 生成友好提示。
"""

import json
import logging
import os
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Literal

# LangGraph
from langgraph.graph import StateGraph, START, END

from scripts.agents.state import FinRAGState, make_initial_state
from scripts.agents.llm import get_llm
from scripts.agents.query_rewriter import QueryRewriter
from scripts.agents.intent_router import IntentRouter
from scripts.agents.sub_question import SubQuestionEngine
from scripts.agents.code_interpreter import CodeInterpreter
from scripts.agents.summarization_engine import SummarizationEngine
from scripts.agents.theme_aggregator import ThemeAggregator

from scripts.ingestion_v2 import Config
from scripts.retriever import HybridRetriever, format_results

logger = logging.getLogger("agent.graph")

# ── 全局单例（避免每次重新加载模型） ──────────────────────────
_retriever: HybridRetriever = None
_rewriter: QueryRewriter = None
_router: IntentRouter = None
_subq_engine: SubQuestionEngine = None
_code_interpreter: CodeInterpreter = None
_summ_engine: SummarizationEngine = None
_theme_engine: ThemeAggregator = None
_llm = None


def _get_retriever() -> HybridRetriever:
    global _retriever
    if _retriever is None:
        cfg = Config()
        _retriever = HybridRetriever(cfg)
        _retriever._load_embedder()
        bm25_path = cfg._BASE_DIR / "data" / "bm25_index.pkl"
        if bm25_path.exists():
            _retriever._load_bm25()
        else:
            logger.warning("BM25 索引不存在，将使用纯向量检索")
    return _retriever


def _get_rewriter():
    global _rewriter
    if _rewriter is None:
        _rewriter = QueryRewriter()
    return _rewriter


def _get_router():
    global _router
    if _router is None:
        _router = IntentRouter()
    return _router


def _get_subq():
    global _subq_engine
    if _subq_engine is None:
        _subq_engine = SubQuestionEngine()
    return _subq_engine


def _get_code_interp():
    global _code_interpreter
    if _code_interpreter is None:
        _code_interpreter = CodeInterpreter()
    return _code_interpreter


def _get_summ_engine():
    global _summ_engine
    if _summ_engine is None:
        _summ_engine = SummarizationEngine()
    return _summ_engine


def _get_theme_engine():
    global _theme_engine
    if _theme_engine is None:
        _theme_engine = ThemeAggregator()
    return _theme_engine


# ══════════════════════════════════════════════════════════════════
#  节点函数
# ══════════════════════════════════════════════════════════════════

# ── 问答系统 Prompt ──────────────────────────────────────────
PROMPT_COMPARATIVE = """你是一个专业的金融分析师。请基于检索到的财报数据，对多家公司进行对比分析。

要求：
1. 用数据说话，列出关键指标的数值对比
2. 指出各公司的优势和劣势
3. 如果数据不完整，明确说明哪些数据缺失
4. 使用表格或结构化格式展示对比结果
5. 列出引用来源"""

PROMPT_COMPUTATIONAL_TEMPLATE = """你是一个专业的金融计算助手。用户需要计算，我已帮你执行了 Python 代码。

代码执行结果：
{code_output}

{code_error_part}

请基于上述计算结果回答用户的问题。如果代码执行有误，在回答中说明。列出使用的数据和计算过程。"""

PROMPT_SUMMARIZATION = """你是一个专业的金融分析师。用户需要一份结构化摘要，我已经完成了 Map-Reduce 处理。

请基于检索到的 Map-Reduce 结果生成简洁、结构化的最终回答。

要求：
1. 以"【摘要】"开头，给出 1-2 句话的总体概括
2. 按主题分段，每段列出关键要点
3. 每个要点标注来源
4. 语言简洁专业，适合资本市场从业者阅读
5. 如果信息不足，明确说明"""

PROMPT_THEME_ANALYSIS = """你是一个专业的金融分析师。用户需要多文档观点分析，我已经完成了主题聚合。

请基于检索到的观点矩阵生成结构化回答。

要求：
1. 以"【观点矩阵】"开头，给出整体观点概况
2. 按主题分组展示，每组列出各机构观点
3. 标注共识 vs 分歧
4. 每个观点标注来源机构
5. 用表格或结构化格式展示
6. 语言简洁专业"""

PROMPT_FACTUAL = """你是一个专业的金融分析师。请基于检索到的资料回答用户的问题。

要求：
1. 直接回答问题，给出具体数值或分析结论
2. 引用来源（[来源1], [来源2]）
3. 如果数据不完整，说明哪些数据缺失
4. 语言简洁专业，适合资本市场从业者阅读
5. 不要编造数据

⚠️ 重要原则：
- 如果检索到的是券商研报内容，优先引用研报中的观点、预测和评级
- 如果检索到的是财务报告，优先采用合并利润表的数据（而非母公司报表）
- 如果同一信息出现在多个来源中，交叉验证后引用最可靠的那个
- 如果检索内容中包含"五粮液"，说明搜索无过滤（未指定公司），但仍尽力回答"""


def _build_retrieval_filters(company: str, year: str) -> dict:
    """构建检索过滤条件。"""
    filters = {}
    if company:
        try:
            from scripts.kb_meta import get_kb_index
            kb = get_kb_index()
            matched = kb.search_company(company)
            filters["company"] = company
            logger.info(f"   🏢 公司过滤: {company}")
        except ImportError:
            filters["company"] = company
    if year:
        filters["year"] = year
        logger.info(f"   📅 年份过滤: {year}")
    return filters


FINANCIAL_TERM_TO_STATEMENT = {
    "净利润": "合并利润表", "营业收入": "合并利润表", "营业成本": "合并利润表",
    "毛利率": "合并利润表", "营业利润": "合并利润表", "归母净利润": "合并利润表",
    "资产": "合并资产负债表", "负债": "合并资产负债表",
    "现金流": "合并现金流量表", "经营性现金流": "合并现金流量表",
    "净资产": "合并资产负债表", "ROE": "合并利润表",
}


def _augment_financial(query: str, chunks: list, filters: dict, retriever) -> list:
    """财务感知增强检索：当查询含财务指标时补充搜索对应报表标题。"""
    should_augment = any(term in query for term in FINANCIAL_TERM_TO_STATEMENT)
    if not should_augment or len(chunks) >= 6:
        return chunks

    target_statements = set()
    for term, stmt in FINANCIAL_TERM_TO_STATEMENT.items():
        if term in query:
            target_statements.add(stmt)

    for stmt in target_statements:
        logger.info(f"   📋 财务感知增强: 搜索\"{stmt}\"")
        try:
            extra = retriever.retrieve(
                query=stmt, top_k_final=5, use_reranker=False,
                filters=filters or None, with_parent=True,
            )
            existing_ids = {r.child_chunk.point_id for r in chunks}
            for e in extra:
                if e.child_chunk.point_id not in existing_ids:
                    chunks.append(e)
                    existing_ids.add(e.child_chunk.point_id)
        except Exception as e:
            logger.warning(f"   财务增强检索失败: {e}")
    return chunks


def _build_context_and_citations(chunks: list) -> tuple[str, list]:
    """构建上下文文本和引用列表。"""
    context_parts = []
    citations = []
    for i, r in enumerate(chunks, 1):
        c = r.child_chunk
        parent_text = r.parent_chunk.text if r.parent_chunk else c.text
        context_parts.append(f"[来源{i}] ({c.company}, {c.year}, {c.doc_type}):\n{parent_text}\n")
        citations.append({
            "index": i, "text": parent_text,
            "company": c.company, "year": c.year, "doc_type": c.doc_type,
        })
    context = "\n---\n".join(context_parts)
    return context, citations


def _extract_structured_metrics(chunks: list) -> list[dict]:
    """从检索结果中提取结构化财务指标。"""
    metrics = []
    seen = set()
    for r in chunks:
        c = r.child_chunk
        parent = r.parent_chunk
        payload = parent.payload if parent else c.payload
        fin_data = payload.get("financial_data", [])
        if not fin_data:
            continue
        for m in fin_data:
            key = (c.company, c.year, m.get("metric", ""))
            if key in seen:
                continue
            seen.add(key)
            metrics.append({
                "metric": m.get("metric", ""), "value": m.get("value", 0),
                "unit": m.get("unit", ""), "company": c.company,
                "year": c.year, "source": c.doc_type,
            })
    return metrics


def node_rewrite_query(state: FinRAGState) -> dict:
    """节点 1：查询转换（指代消解 + 多路查询 + HyDE）"""
    query = state.get("query", "")
    history = state.get("messages", [])

    try:
        rewriter = _get_rewriter()
        result = rewriter.rewrite(query, history)

        return {
            "query": result["resolved_query"],
            "company": result.get("company", ""),
            "year": result.get("year", ""),
            "rewritten_queries": result.get("multi_queries", []),
            "hyde_document": result.get("hyde_document", ""),
        }
    except Exception as e:
        logger.error(f"  查询转换失败: {e}")
        return {"error": f"查询转换失败: {e}"}


def node_route_intent(state: FinRAGState) -> dict:
    """节点 2：意图路由"""
    query = state.get("query", "")

    try:
        router = _get_router()
        result = router.route(query)
        return {
            "intent": result["intent"],
            "intent_confidence": result.get("confidence", 0.5),
            "entities": result.get("entities", []),
        }
    except Exception as e:
        logger.error(f"  意图路由失败: {e}")
        return {"intent": "factual", "intent_confidence": 0.5, "entities": []}


def node_retrieve_factual(state: FinRAGState) -> dict:
    """节点 3a：事实检索（混合检索 + 重排序）"""
    query = state.get("query", "")
    company = state.get("company", "")
    year = state.get("year", "")

    try:
        retriever = _get_retriever()
        filters = _build_retrieval_filters(company, year)

        # 多实体检测：如果 query 涉及多个公司，走独立检索 + 融合
        entities = state.get("entities", [])
        if len(entities) >= 2 and not company:
            return node_retrieve_multi_entity(state)

        # 先用带过滤的检索（精准），如果结果太少再回退
        chunks = retriever.retrieve(
            query=query, top_k_final=8, use_reranker=False,
            filters=filters or None, with_parent=True,
        )
        if len(chunks) < 3 and filters:
            logger.warning(f"   带过滤检索结果太少({len(chunks)}条)，回退到无过滤检索")
            chunks = retriever.retrieve(
                query=query, top_k_final=8, use_reranker=False,
                filters=None, with_parent=True,
            )

        # 财务感知增强检索
        chunks = _augment_financial(query, chunks, filters, retriever)
        logger.info(f"   📋 增强后: {len(chunks)} 条结果")

        # 构建上下文和引用
        context, citations = _build_context_and_citations(chunks)
        logger.info(f"  事实检索: {len(chunks)} 条结果，上下文 {len(context)} 字")

        # 提取结构化财务指标
        structured_metrics = _extract_structured_metrics(chunks)
        if structured_metrics:
            logger.info(f"   📊 提取 {len(structured_metrics)} 个结构化财务指标")

        return {
            "retrieved_chunks": chunks,
            "context": context,
            "citations": citations,
            "structured_metrics": structured_metrics,
        }
    except Exception as e:
        logger.error(f"  检索失败: {e}")
        return {"error": f"检索失败: {e}"}


def _strip_other_entities(query: str, target_entity: str) -> str:
    """从查询中移除其他实体名，生成干净的独立查询。

    例如: query="对比五粮液和贵州茅台的盈利能力", target="五粮液"
          → "五粮液的盈利能力"
    """
    # 从 KB 获取所有已知公司名
    try:
        from scripts.kb_meta import get_kb_index
        kb = get_kb_index()
        all_entities = kb.all_stocks
        # 补充别名
        for standard, aliases in kb._company_aliases.items():
            for a in aliases:
                if a not in all_entities:
                    all_entities.append(a)
    except Exception:
        all_entities = ["五粮液", "贵州茅台", "宁德时代", "比亚迪", "腾讯", "阿里巴巴"]

    # 移除其他实体
    stripped = query
    for entity in all_entities:
        if entity != target_entity and entity in stripped:
            stripped = stripped.replace(entity, "")

    # 清理残留的对比词和符号
    for word in ["对比", "比较", "vs", "与", "和", "、", "，", "  "]:
        stripped = stripped.replace(word, " ")
    stripped = stripped.strip().strip("，。、").strip()

    # 如果清理后为空，回退到 target_entity + query 前 20 字
    if not stripped or len(stripped) < 4:
        stripped = f"{target_entity} {query[:30]}"

    logger.info(f"   🧹 查询净化: [{target_entity}] {stripped}")
    return stripped


def node_retrieve_multi_entity(state: FinRAGState) -> dict:
    """节点 3f：多实体独立检索 + 融合（新增）

    当查询涉及多个公司时，为每个实体生成干净子查询，
    独立检索后通过 _multi_doc_fusion 融合结果。
    """
    query = state.get("query", "")
    entities = state.get("entities", [])

    if not entities:
        logger.warning("   ⚠️ 多实体检索但 entities 为空，回退到 factual 检索")
        return node_retrieve_factual(state)

    try:
        retriever = _get_retriever()

        # 对每个实体独立检索
        all_chunks = []
        for entity in entities:
            clean_query = _strip_other_entities(query, entity)
            logger.info(f"   🔍 多实体检索: [{entity}] {clean_query}")

            chunks = retriever.retrieve(
                query=clean_query,
                top_k_final=4,
                use_reranker=False,
                filters={"company": entity},
                with_parent=True,
            )
            all_chunks.append(chunks)
            logger.info(f"     → {len(chunks)} 条结果")

        # 多文档多样性融合
        from scripts.agents.sub_question import SubQuestionEngine
        fused = SubQuestionEngine._multi_doc_fusion(
            all_chunks, max_total=10, min_per_company=2,
        )
        logger.info(f"   🔄 多文档融合: {sum(len(c) for c in all_chunks)}→{len(fused)} 条")

        # 构建上下文和引用
        context, citations = _build_context_and_citations(fused)

        # 提取结构化指标
        structured_metrics = _extract_structured_metrics(fused)

        return {
            "retrieved_chunks": fused,
            "context": context,
            "citations": citations,
            "structured_metrics": structured_metrics,
        }
    except Exception as e:
        logger.error(f"  多实体检索失败: {e}")
        return {"error": f"多实体检索失败: {e}"}


def node_decompose_comparative(state: FinRAGState) -> dict:
    """节点 3b：对比分析 → 拆子问题"""
    query = state.get("query", "")

    try:
        subq = _get_subq()
        result = subq.decompose(query)
        return {
            "sub_questions": result.get("questions", []),
        }
    except Exception as e:
        logger.error(f"  子问题拆解失败: {e}")
        return {"error": f"子问题拆解失败: {e}", "sub_questions": []}


def node_execute_sub_questions(state: FinRAGState) -> dict:
    """节点 4b：执行子问题检索"""
    questions = state.get("sub_questions", [])

    if not questions:
        return {"sub_results": []}

    try:
        retriever = _get_retriever()
        subq = _get_subq()
        results = subq.execute_sub_questions(state, retriever, questions)

        # 构建合并上下文
        context_parts = []
        for r in results:
            q = r["question"]
            context_parts.append(f"## 子问题: {q['question']}\n")
            for j, chunk in enumerate(r.get("results", []), 1):
                c = chunk.child_chunk
                parent_text = chunk.parent_chunk.text if chunk.parent_chunk else c.text
                context_parts.append(f"[子q{q['id']}-{j}] {parent_text[:500]}\n")

        context = "\n".join(context_parts)

        return {
            "sub_results": results,
            "context": context,
        }
    except Exception as e:
        logger.error(f"  子问题执行失败: {e}")
        return {"error": f"子问题执行失败: {e}"}


def node_generate_code(state: FinRAGState) -> dict:
    """节点 3c：LLM 生成 Python 代码"""
    query = state.get("query", "")
    context = state.get("context", "")
    structured_metrics = state.get("structured_metrics", [])

    try:
        llm = get_llm()
        ci = _get_code_interp()

        # 构建结构化指标的 Python 变量声明
        metrics_vars = ""
        if structured_metrics:
            var_lines = []
            # 按 company + year 分组
            groups = defaultdict(list)
            for m in structured_metrics:
                key = f"{m.get('company', '')}_{m.get('year', '')}"
                key = re.sub(r'[^a-zA-Z0-9_]', '_', key)
                groups[key].append(m)

            for group_key, metrics in groups.items():
                var_lines.append(f"# {group_key}")
                for m in metrics:
                    var_name = m.get("metric", "").replace(" ", "_")
                    val = m.get("value", 0)
                    unit = m.get("unit", "")
                    var_lines.append(f"{var_name} = {val}  # {unit}")
            metrics_vars = "\n".join(var_lines)
            logger.info(f"   📊 结构化指标: {len(structured_metrics)} 个变量")

        code_prompt = ci.build_code_prompt(query, context, metrics_vars)
        messages = [
            {"role": "system", "content": f"""你是一个金融计算专家。只输出可运行的 Python 代码。

可用预处理变量：
{metrics_vars or '(无预处理变量，请从 context 中提取数据)'}

规则：
1. 如果上述变量中有所需数据，直接引用变量名（如 `print(营业收入)`）
2. 如果数据不在变量中，在代码中从 _context 字符串提取
3. 输出数字时要带来源说明
4. 使用 print() 输出结果
5. 不要定义与变量名同名的函数或类"""},
            {"role": "user", "content": code_prompt},
        ]
        llm_output = llm.chat(messages, temperature=0.1, max_tokens=2048)
        code = ci.extract_code(llm_output)

        logger.info(f"  Code 生成: {len(code)} 字符")
        return {"code": code}
    except Exception as e:
        logger.error(f"  代码生成失败: {e}")
        return {"error": f"代码生成失败: {e}", "code": ""}


def node_execute_code(state: FinRAGState) -> dict:
    """节点 4c：执行代码"""
    code = state.get("code", "")
    if not code:
        return {"code_output": "", "code_error": "没有代码可执行"}

    try:
        ci = _get_code_interp()

        # 从上下文中提取可用数据
        context = state.get("context", "")
        structured_metrics = state.get("structured_metrics", [])
        input_data = {"_context": context}

        # 将结构化指标作为预定义变量传入
        if structured_metrics:
            for m in structured_metrics:
                var_name = m.get("metric", "").replace(" ", "_")
                input_data[var_name] = m.get("value", 0)
            input_data["_metrics"] = structured_metrics
            logger.info(f"   📊 传入 {len(structured_metrics)} 个结构化变量到执行环境")

        result = ci.execute(code, input_data)

        if result["success"]:
            logger.info(f"  Code 执行成功: {result['output'][:200]}")
            return {
                "code_output": result["output"],
                "code_error": "",
            }
        else:
            logger.warning(f"  Code 执行失败: {result['error']}")
            return {
                "code_output": result.get("stdout", ""),
                "code_error": result.get("error", ""),
            }
    except Exception as e:
        logger.error(f"  代码执行异常: {e}")
        return {"code_error": f"执行异常: {e}"}


def node_summarize(state: FinRAGState) -> dict:
    """节点 3d：Map-Reduce 查询聚焦摘要"""
    query = state.get("query", "")
    company = state.get("company", "")

    try:
        retriever = _get_retriever()
        engine = _get_summ_engine()

        filters = {}
        if company:
            filters["company"] = company
            logger.info(f"   🏢 摘要过滤: {company}")

        result = engine.summarize(query, retriever, filters=filters or None)

        # 构建上下文字符串
        if result["themes"]:
            context_parts = [f"【摘要】{result['summary']}\n"]
            for t in result["themes"]:
                theme = t.get("theme", "")
                context_parts.append(f"\n## {theme}")
                for p in t.get("points", []):
                    sources = ", ".join(p.get("sources", []))
                    context_parts.append(f"- {p['point']} ({sources})")
            context = "\n".join(context_parts)
        else:
            context = result.get("summary", "未能生成摘要。")

        logger.info(f"   ✅ 摘要完成: {len(result.get('themes', []))} 个主题, {len(context)} 字")
        return {
            "context": context,
            "retrieved_chunks": [],
            "citations": [],
        }
    except Exception as e:
        logger.error(f"  摘要失败: {e}")
        return {"error": f"摘要生成失败: {e}"}


def node_theme_analysis(state: FinRAGState) -> dict:
    """节点 3e：多文档主题聚合与观点溯源"""
    query = state.get("query", "")
    company = state.get("company", "")

    try:
        retriever = _get_retriever()
        engine = _get_theme_engine()

        filters = {}
        if company:
            filters["company"] = company
            logger.info(f"   🏢 主题分析过滤: {company}")

        result = engine.analyze(query, retriever, filters=filters or None)

        context = result.get("matrix_text", "未能生成观点矩阵。")
        logger.info(f"   ✅ 主题分析完成: {len(result.get('themes', []))} 个主题, "
                    f"{result.get('num_sources',0)} 个来源")

        # 构建引用列表
        citations = []
        for s in result.get("source_list", []):
            citations.append({"text": s[:100], "company": company, "doc_type": "研报"})

        return {
            "context": context,
            "retrieved_chunks": [],
            "citations": citations,
        }
    except Exception as e:
        logger.error(f"  主题分析失败: {e}")
        return {"error": f"主题分析失败: {e}"}


def node_generate_answer(state: FinRAGState) -> dict:
    """节点 5：生成最终回答"""
    query = state.get("query", "")
    intent = state.get("intent", "factual")
    context = state.get("context", "")
    code_output = state.get("code_output", "")
    code_error = state.get("code_error", "")
    citations = state.get("citations", [])

    try:
        llm = get_llm()

        # 根据意图构建不同的 prompt（使用模块级常量）
        if intent == "comparative":
            system_prompt = PROMPT_COMPARATIVE
        elif intent == "computational":
            error_part = f"\n\n代码执行出错: {code_error[:500]}" if code_error else ""
            system_prompt = PROMPT_COMPUTATIONAL_TEMPLATE.format(
                code_output=code_output[:2000],
                code_error_part=error_part,
            )
        elif intent == "summarization":
            system_prompt = PROMPT_SUMMARIZATION
        elif intent == "theme_analysis":
            system_prompt = PROMPT_THEME_ANALYSIS
        else:
            system_prompt = PROMPT_FACTUAL

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"用户问题: {query}\n\n检索到的上下文:\n{context[:8000]}"},
        ]

        answer = llm.chat(messages, temperature=0.1, max_tokens=4096)
        logger.info(f"  回答生成: {len(answer)} 字符")

        return {"answer": answer}
    except Exception as e:
        logger.error(f"  回答生成失败: {e}")
        return {"error": f"回答生成失败: {e}"}


def node_error_handler(state: FinRAGState) -> dict:
    """错误处理节点：生成友好错误信息"""
    error = state.get("error", "未知错误")
    query = state.get("query", "")

    logger.warning(f"⚠️  错误处理: {error}")

    # 尝试用 LLM 生成友好的错误回复
    try:
        llm = get_llm()
        msg = [
            {"role": "system", "content": "你是一个友好的金融问答助手。由于技术原因，我无法完整回答这个问题。请用中文给出简短友好的解释，并建议用户简化或重新表述问题。"},
            {"role": "user", "content": f"用户问: {query}\n技术错误: {error}"},
        ]
        answer = llm.chat(msg, temperature=0.3, max_tokens=500)
    except Exception:
        answer = f"抱歉，在处理您的问题时遇到了技术问题：{error[:200]}。请简化问题后重试。"

    return {"answer": answer}


# ══════════════════════════════════════════════════════════════════
#  路由函数 (条件边)
# ══════════════════════════════════════════════════════════════════


def router_intent(state: FinRAGState) -> Literal["retrieve_factual", "decompose_comparative", "generate_code", "node_summarize", "node_theme_analysis", "error_handler"]:
    """根据意图路由到不同处理流程"""
    if state.get("error"):
        return "error_handler"
    intent = state.get("intent", "factual")
    if intent == "comparative":
        return "decompose_comparative"
    elif intent == "computational":
        return "retrieve_factual"
    elif intent == "multi_entity":
        return "retrieve_factual"       # 走检索节点（内部有多实体分支）
    elif intent == "summarization":
        return "node_summarize"
    elif intent == "theme_analysis":
        return "node_theme_analysis"
    else:
        return "retrieve_factual"


def router_has_error(state: FinRAGState) -> Literal["error_handler", "generate_answer"]:
    """检查是否有错误"""
    if state.get("error"):
        return "error_handler"
    return "generate_answer"


# ══════════════════════════════════════════════════════════════════
#  构建图
# ══════════════════════════════════════════════════════════════════

def build_graph() -> StateGraph:
    """构建完整的 Agent RAG 工作流图"""

    builder = StateGraph(FinRAGState)

    # ── 注册节点 ──────────────────────────────────────────────
    builder.add_node("rewrite_query", node_rewrite_query)
    builder.add_node("route_intent", node_route_intent)
    builder.add_node("retrieve_factual", node_retrieve_factual)
    builder.add_node("decompose_comparative", node_decompose_comparative)
    builder.add_node("execute_sub_questions", node_execute_sub_questions)
    builder.add_node("generate_code", node_generate_code)
    builder.add_node("execute_code", node_execute_code)
    builder.add_node("node_summarize", node_summarize)
    builder.add_node("node_theme_analysis", node_theme_analysis)
    builder.add_node("generate_answer", node_generate_answer)
    builder.add_node("error_handler", node_error_handler)

    # ── 边 ────────────────────────────────────────────────────
    builder.add_edge(START, "rewrite_query")
    builder.add_edge("rewrite_query", "route_intent")

    # 意图路由（条件边）
    builder.add_conditional_edges("route_intent", router_intent)

    # 事实检索 → 生成回答（或错误处理）
    builder.add_conditional_edges("retrieve_factual", router_has_error)

    # 对比分析
    builder.add_edge("decompose_comparative", "execute_sub_questions")
    builder.add_conditional_edges("execute_sub_questions", router_has_error)

    # 数值计算
    builder.add_edge("generate_code", "execute_code")
    builder.add_conditional_edges("execute_code", router_has_error)

    # 摘要总结 → 生成回答
    builder.add_conditional_edges("node_summarize", router_has_error)

    # 观点分析 → 生成回答
    builder.add_conditional_edges("node_theme_analysis", router_has_error)

    # 回答 → 结束
    builder.add_edge("generate_answer", END)
    builder.add_edge("error_handler", END)

    # ── 编译 ──────────────────────────────────────────────────
    graph = builder.compile()
    return graph


# ══════════════════════════════════════════════════════════════════
#  便捷调用
# ══════════════════════════════════════════════════════════════════

_graph_instance = None


def get_graph() -> StateGraph:
    """获取全局编译后的图实例"""
    global _graph_instance
    if _graph_instance is None:
        _graph_instance = build_graph()
    return _graph_instance


def run_agent_query(
    query: str,
    messages: list[dict] = None,
    session_id: str = "default",
    verbose: bool = False,
) -> FinRAGState:
    """
    执行一次完整的 Agent RAG 查询。

    参数:
        query: 用户问题
        messages: 对话历史
        session_id: 会话 ID
        verbose: 是否打印详细日志

    返回:
        最终状态（包含 answer, citations, error 等）
    """
    if verbose:
        logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")
        logging.getLogger("agent").setLevel(logging.INFO)
        logging.getLogger("retriever").setLevel(logging.INFO)

    graph = get_graph()

    initial = make_initial_state(session_id=session_id)
    initial["query"] = query
    initial["messages"] = messages or []

    result = graph.invoke(initial)
    return result
