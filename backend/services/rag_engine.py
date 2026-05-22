"""
finRAG API — RAG 引擎服务
=========================
封装检索+生成逻辑，供 FastAPI 路由调用。
支持流式（SSE）和阻塞两种模式。
"""
import json
import logging
import os
import time
from pathlib import Path
from typing import AsyncGenerator, Optional

from langsmith import traceable
from langsmith.run_helpers import trace
from dotenv import load_dotenv

load_dotenv()

from backend.config import settings
from scripts.ingestion_v2 import normalize_company
from scripts.agents.query_rewriter import expand_financial_query
import re
import copy
from concurrent.futures import ThreadPoolExecutor, as_completed

logger = logging.getLogger("rag_engine")

# ── 全局单例（模型懒加载） ──────────────────────────────
_retriever = None
_llm = None
_safe_retriever = None  # 安全模式检索器（使用 finrag_safe 集合）
_safe_llm = None  # 安全模式 LLM（Ollama 本地模型）

# ── 可识别公司名列表（模块级，rag_query / rag_stream 共用） ──
KNOWN_ENTITIES = [
    "贵州茅台", "五粮液", "招商银行", "中国平安",
    "北方长龙", "中芯国际", "广东鸿特", "联讯仪器", "长裕集团",
    "海尔智家", "中矿资源", "天齐锂业", "徐工机械", "皖仪科技",
    "奥浦迈", "甘李药业", "陕西能源", "福斯特", "同享科技",
    "中国国家铁路集团", "国铁集团", "宁德时代", "比亚迪",
    "新产业", "甘肃陇神戎发药业",
]


def _get_retriever(mode="pro"):
    global _retriever, _safe_retriever
    if mode == "safe":
        if _safe_retriever is None:
            from scripts.retriever import HybridRetriever
            from scripts.ingestion_v2 import Config as IngestionConfig

            cfg = IngestionConfig()
            cfg.QDRANT_DB_PATH = settings.QDRANT_PATH
            cfg.QDRANT_COLLECTION = settings.QDRANT_COLLECTION_SAFE
            qdrant_host = os.getenv("QDRANT_HOST", settings.QDRANT_HOST)
            if qdrant_host and qdrant_host not in ("localhost", "127.0.0.1"):
                cfg.QDRANT_MODE = "remote"
                cfg.QDRANT_HOST = qdrant_host
                cfg.QDRANT_PORT = int(os.getenv("QDRANT_PORT", str(settings.QDRANT_PORT)))
            else:
                cfg.QDRANT_MODE = "disk"

            # 离线模式由环境变量 HF_HUB_OFFLINE 控制（不强制）
            _safe_retriever = HybridRetriever(cfg)
            _safe_retriever._connect_qdrant()
            _safe_retriever._load_embedder()
            _safe_retriever._load_bm25()
        return _safe_retriever
    
    # 专业模式（默认）
    if _retriever is None:
        from scripts.retriever import HybridRetriever
        from scripts.ingestion_v2 import Config as IngestionConfig

        cfg = IngestionConfig()
        cfg.QDRANT_DB_PATH = settings.QDRANT_PATH
        cfg.QDRANT_COLLECTION = settings.QDRANT_COLLECTION_PRO
        # 如果 QDRANT_HOST 不是 localhost，切换为远程模式
        qdrant_host = os.getenv("QDRANT_HOST", settings.QDRANT_HOST)
        if qdrant_host and qdrant_host not in ("localhost", "127.0.0.1"):
            cfg.QDRANT_MODE = "remote"
            cfg.QDRANT_HOST = qdrant_host
            cfg.QDRANT_PORT = int(os.getenv("QDRANT_PORT", str(settings.QDRANT_PORT)))
        else:
            cfg.QDRANT_MODE = "disk"

        # 离线模式由环境变量 HF_HUB_OFFLINE 控制（不强制）
        _retriever = HybridRetriever(cfg)
        _retriever._connect_qdrant()  # pre-connect before parallel threads
        _retriever._load_embedder()
        _retriever._load_bm25()
    return _retriever


def _get_llm(mode="pro"):
    global _llm, _safe_llm
    if mode == "safe":
        if _safe_llm is None:
            from scripts.agents.llm import get_safe_llm
            _safe_llm = get_safe_llm()
        return _safe_llm
    # 专业模式（默认）
    global _llm
    if _llm is None:
        from scripts.agents.llm import get_llm
        _llm = get_llm()
    return _llm


def _get_rewriter():
    from scripts.agents.query_rewriter import QueryRewriter
    return QueryRewriter()


def _get_router():
    from scripts.agents.intent_router import IntentRouter
    return IntentRouter()


def _get_kb():
    from scripts.kb_meta import get_kb_index
    return get_kb_index()


# ── 文档删除（复用已有 Qdrant 连接） ─────────────────────

def delete_from_qdrant(doc_id: str, collections: list[str]) -> dict[str, str]:
    """从 Qdrant 删除指定文档的所有向量（复用已有持久连接，避免 disk 锁冲突）。
    
    Args:
        doc_id: 文档 ID（不含扩展名）
        collections: 要操作的集合列表，如 ["finrag_pro", "finrag_safe"]
    
    Returns:
        {collection_name: status_str, ...}
    """
    from qdrant_client.http import models as qmodels

    results = {}
    for mode, retriever_name in [("pro", "_retriever"), ("safe", "_safe_retriever")]:
        retriever = _get_retriever(mode)
        client = retriever._qdrant_client
        if client is None:
            results[f"finrag_{mode}"] = "no_connection"
            continue

        col = settings.QDRANT_COLLECTION_PRO if mode == "pro" else settings.QDRANT_COLLECTION_SAFE
        if col not in collections:
            continue

        try:
            deleted = client.delete(
                collection_name=col,
                points_selector=qmodels.FilterSelector(
                    filter=qmodels.Filter(
                        must=[qmodels.FieldCondition(
                            key="source_file",
                            match=qmodels.MatchValue(value=f"{doc_id}.md"),
                        )]
                    )
                ),
            )
            results[col] = str(getattr(deleted, "status", "completed"))
            logger.info(f"  ✅ Qdrant {col}: 已删除 {doc_id}")
        except Exception as e:
            results[col] = f"error: {e}"
            logger.warning(f"  ⚠️ Qdrant {col} 删除失败: {e}")

    return results


# ── 检索 + 生成（阻塞模式） ─────────────────────────────

def _load_history(session_id: str, max_rounds: int = 4) -> list[dict]:
    """加载对话历史，用于注入 LLM prompt。

    Args:
        session_id: 对话 ID
        max_rounds: 最多取最近 N 轮（每轮 user+assistant 各一条）

    Returns:
        [{role, content}, ...] 格式的消息列表，适合直接拼入 LLM messages。
        注意: 排除当前轮次的最新消息（由 chat.py 调用方负责保存当前用户输入）。
    """
    from backend.routers.conversations import _load_conv

    conv = _load_conv(session_id)
    if not conv:
        return []

    msgs = conv.get("messages", [])
    # 排除最后一条（当前轮次的用户消息，由 chat.py 刚刚 add_message）
    past = msgs[:-1] if msgs else []
    # 取最近 max_rounds*2 条历史消息
    recent = past[-(max_rounds * 2):]

    history = []
    for m in recent:
        history.append({
            "role": m["role"],
            "content": m["content"][:2000],  # 截断避免超 token
        })
    return history


def _parallel_entity_retrieve(
    entity, clean_base, detected_entities,
    retriever, strategy, intent,
):
    """为单个实体执行检索（供 ThreadPoolExecutor 调用）。"""
    sub_query = clean_base
    for other in detected_entities:
        if other != entity:
            sub_query = sub_query.replace(other, "").strip()
    sub_query = f"{entity} {sub_query}"
    sub_query = re.sub(r'\s+', ' ', sub_query).strip()

    canonical = normalize_company(entity)
    entity_results = retriever.retrieve(
        query=sub_query,
        top_k_final=max(8, strategy["top_k_final"] // len(detected_entities)),
        use_reranker=strategy["use_reranker"],
        strict_filters={"company": canonical},
        score_threshold=0.0,
        intent=intent,
        with_parent=True,
    )
    return entity, sub_query, entity_results


@traceable(name="rag_query", run_type="chain")
def rag_query(
    query: str,
    top_k: int = 8,
    use_reranker: bool = False,
    stream: bool = False,
    session_id: str | None = None,
    verify_on_error: bool = False,
    mode: str = "pro",
) -> dict:
    """
    完整的 RAG 查询流水线。
    返回：{answer, citations, intent, context, elapsed_sec}
    """
    t0 = time.perf_counter()

    # Layer 4: 精确查询缓存（跳过整个 pipeline）
    from backend.services.cache import query_cache, make_key
    qkey = f"q:{make_key(query=query, top_k=top_k, reranker=use_reranker, mode=mode)}"
    cached = query_cache.get(qkey)
    if cached is not None:
        logger.info(f"⚡ 查询缓存命中: \"{query[:40]}...\"")
        cached["from_cache"] = True
        cached["elapsed_sec"] = 0.0
        return cached

    # ⭐ 预算守卫（Cache miss → 即将调用 LLM，先检查预算）
    from backend.services.budget_guard import check_budget
    budget = check_budget(session_id or "default")
    if budget["action"] == "reject":
        logger.warning(f"⛔ 预算守卫拦截: {budget['message']}")
        return {
            "answer": f"❌ 预算已耗尽，无法继续回答。{budget.get('message', '')}",
            "citations": [], "intent": "factual", "context": "", "elapsed_sec": 0.0,
            "budget_blocked": True,
        }
    if budget["action"] in ("warn", "cache_only"):
        logger.info(f"⚠️ 预算警告: {budget['message']}")

    # 1. Query Rewriting — 默认跳过 LLM 改写（用关键词规则替代）
    # 多轮对话时自动启用 LLM 改写（指代消解需要）
    skip_rewrite = os.environ.get("SKIP_QUERY_REWRITE", "1") != "0"
    history_msgs = _load_history(session_id) if session_id else []
    if skip_rewrite and history_msgs:
        skip_rewrite = False  # 有历史对话 → 启用 LLM 改写（指代消解）

    if skip_rewrite:
        # 关键词规则路径（零LLM成本）
        expanded = expand_financial_query(query)
        resolved_query = expanded[0]  # 扩展后的主查询
        company = ""
        for kw in KNOWN_ENTITIES:
            if kw in query:
                company = kw
                break
        if company:
            company = normalize_company(company)
        year = ""
        ym = re.search(r"(20[0-9]{2})", query)
        if ym:
            year = ym.group(1)
        # 简单意图检测（基于关键词）
        intent = "factual"
        comp_kw = ["对比", "比较", "vs", "VS", "不同", "区别", "哪个更"]
        summ_kw = ["总结", "概括", "列举", "哪些", "几个主要", "归纳", "综述", "汇总"]
        reason_kw = ["为什么", "原因", "说明什么", "驱动", "逻辑", "意味着"]
        calc_kw = ["计算一下", "计算得出", "推算", "估算", "同比变化", "变化幅度", "增长率", "增减速"]
        if any(kw in query for kw in comp_kw):
            intent = "comparative"
        elif any(kw in query for kw in summ_kw):
            intent = "summary"
        elif any(kw in query for kw in reason_kw):
            intent = "analytical"
        elif any(kw in query for kw in calc_kw):
            intent = "computational"
    else:
        with trace("query_rewriting", run_type="chain") as span_rewrite:
            kb = _get_kb()
            rewriter = _get_rewriter()
            rewrite_result = rewriter.rewrite(query, history_msgs)
            resolved_query = rewrite_result.get("resolved_query", query)
            raw_company = rewrite_result.get("company", "")
            year = rewrite_result.get("year", "")

            # 通过 KB 标准化公司名
            company = ""
            if raw_company:
                matched = kb.search_company(raw_company)
                if matched:
                    company = raw_company
                else:
                    company = raw_company
            if not company:
                text_extracted = kb.extract_company_from_text(query)
                if text_extracted:
                    company = text_extracted

            # 公司名已通过 normalize_company 统一为规范名
            span_rewrite.add_outputs({
            "resolved_query": resolved_query,
            "company": company,
            "year": year,
        })
        span_rewrite.add_metadata({
            "has_history": bool(history_msgs),
            "history_rounds": len(history_msgs) // 2 if history_msgs else 0,
        })

    # 2. Intent Routing（跳过LLM版本，用skip_rewrite的关键词规则）
    if not skip_rewrite:
        with trace("intent_routing", run_type="chain") as span_route:
            router = _get_router()
            intent_result = router.route(resolved_query)
            intent_llm = intent_result.get("intent", "factual")
            # 关键词规则优先级高于LLM（尤其是总结/对比类）
            intent = intent_llm
            span_route.add_outputs({"intent": intent})

    # 3. Retrieval（多阶段 + 软过滤 + 意图控制）
    with trace("retrieval", run_type="retriever") as span_ret:
        retriever = _get_retriever(mode=mode)
        
        # 意图→检索策略映射（含验证模式）
        intent_strategies = {
            "factual":       {"top_k_final": 8,  "use_reranker": False, "score_threshold": 0.1,  "verify": "light"},
            "analytical":    {"top_k_final": 12, "use_reranker": True,  "score_threshold": 0.1,  "verify": "full"},
            "comparative":   {"top_k_final": 15, "use_reranker": True,  "score_threshold": 0.1,  "verify": "full"},
            "summary":       {"top_k_final": 10, "use_reranker": True,  "score_threshold": 0.1,  "verify": "light"},
            "computational": {"top_k_final": 8,  "use_reranker": False, "score_threshold": 0.1,  "verify": "skip"},
        }
        strategy = intent_strategies.get(intent, intent_strategies["factual"])

        # ── 轻量检索检测：简单事实查询走快速路径 ──
        comparison_kw = ["对比", "比较", "vs", "VS", "不同", "区别", "哪个更", "差异"]
        is_lightweight = (
            intent == "factual"
            and len([e for e in KNOWN_ENTITIES if e in resolved_query]) <= 1
            and not any(kw in resolved_query for kw in comparison_kw)
        )
        if is_lightweight:
            logger.info(f"   ⚡ 轻量检索模式: top_k=5, 无BM25")

        # ── 多实体检测：比较/跨文档查询拆分为独立检索 ──
        detected_entities = [e for e in KNOWN_ENTITIES if e in resolved_query]
        strict_filters = None

        if len(detected_entities) >= 2:
            # 多实体检索：为每个实体生成独立子查询后分别检索
            span_ret.add_metadata({"multi_entity": True, "entities": detected_entities})
            all_chunks = []
            seen_parents = set()
            strict_filters = None

            # 从原查询中去除对比词和其他实体名，为每个实体生成干净的子查询
            comparison_noise = ["对比", "比较", "vs", "VS", "不同", "区别", "哪个", "更", "分别",
                                "哪一个", "差异", "孰高孰低", "怎么", "和", "与", "跟", "及"]
            clean_base = resolved_query
            for noise in comparison_noise:
                clean_base = clean_base.replace(noise, "").strip()
            # 去掉所有实体名（后面逐个追加当次要查的）
            for ent in detected_entities:
                clean_base = clean_base.replace(ent, "").strip()
            clean_base = re.sub(r'[,，。？、\s]+', ' ', clean_base).strip()

            # 并行检索：每个实体独立检索（ThreadPoolExecutor）
            all_chunks_lock = []  # 替代 list + set，线程安全
            seen_parents_lock = set()
            from threading import Lock
            merge_lock = Lock()

            def _merge_results(entity, sub_query, entity_results):
                nonlocal all_chunks_lock, seen_parents_lock
                logger.info(f"      🎯 [{entity}] 子查询: \"{sub_query}\" → {len(entity_results)} 条")
                with merge_lock:
                    for r in entity_results:
                        pid = r.child_chunk.parent_id
                        if pid not in seen_parents_lock:
                            seen_parents_lock.add(pid)
                            all_chunks_lock.append(r)

            with ThreadPoolExecutor(max_workers=len(detected_entities)) as executor:
                futures = []
                for entity in detected_entities:
                    future = executor.submit(
                        _parallel_entity_retrieve,
                        entity, clean_base, detected_entities,
                        retriever, strategy, intent,
                    )
                    futures.append(future)
                for future in as_completed(futures):
                    entity, sub_query, entity_results = future.result()
                    _merge_results(entity, sub_query, entity_results)

            all_chunks_lock.sort(key=lambda x: x.child_chunk.score, reverse=True)
            chunks = all_chunks_lock[:strategy["top_k_final"]]
            logger.info(f"   🔀 多实体检索: {len(detected_entities)} 个实体 → {len(chunks)} 条合并结果")
        elif intent in ("summary", "analytical") and len(detected_entities) <= 1:
            # 聚合/分析类查询：宽召回策略（top_k提升 + 不设company过滤）
            span_ret.add_metadata({"aggregation_mode": True, "intent": intent})
            # 第一轮：宽搜，收集相关文档
            chunks = retriever.retrieve(
                query=resolved_query,
                top_k_final=max(20, strategy["top_k_final"] * 2),
                use_reranker=strategy["use_reranker"],
                strict_filters=None,
                score_threshold=strategy["score_threshold"] * 0.5,
                intent=intent,
                with_parent=True,
            )
            # 如果结果太少，再补一轮同义查询
            if len(chunks) < strategy["top_k_final"]:
                expanded = expand_financial_query(resolved_query)
                if len(expanded) > 1:
                    extra_results = retriever.retrieve(
                        query=expanded[1],
                        top_k_final=max(15, strategy["top_k_final"]),
                        use_reranker=strategy["use_reranker"],
                        strict_filters=None,
                        score_threshold=strategy["score_threshold"] * 0.5,
                        intent=intent,
                        with_parent=True,
                    )
                    seen = set(c.child_chunk.parent_id for c in chunks)
                    for r in extra_results:
                        pid = r.child_chunk.parent_id
                        if pid not in seen:
                            seen.add(pid)
                            chunks.append(r)
                    chunks.sort(key=lambda x: x.child_chunk.score, reverse=True)
                    chunks = chunks[:strategy["top_k_final"]]
                    logger.info(f"      🔄 扩展检索追加: {len(extra_results)} 条 → 合并后 {len(chunks)} 条")
        else:
            # 单实体/无实体检索（原有逻辑）
            strict_filters = {}
            if company:
                strict_filters["company"] = company
            retrieve_kwargs = dict(
                query=resolved_query,
                top_k_final=5 if is_lightweight else strategy["top_k_final"],
                use_reranker=strategy["use_reranker"],
                strict_filters=strict_filters if strict_filters else None,
                score_threshold=strategy["score_threshold"],
                intent=intent,
                with_parent=True,
            )
            if is_lightweight:
                retrieve_kwargs["top_k_bm25"] = 0
            chunks = retriever.retrieve(**retrieve_kwargs)

        span_ret.add_outputs({
            "num_chunks": len(chunks),
            "strategy": intent,
            "top_k": strategy["top_k_final"],
            "filters": strict_filters if strict_filters else {"multi_entity": detected_entities},
        })

    # 4. Build context
    with trace("context_building", run_type="chain") as span_ctx:
        context_parts = []
        citations = []
        max_rrf_score = 0.0
        for i, r in enumerate(chunks, 1):
            c = r.child_chunk
            parent_text = r.parent_chunk.text if r.parent_chunk else c.text
            max_rrf_score = max(max_rrf_score, c.score)
            context_parts.append(
                f"[来源{i}] ({c.company}, {c.year}, {c.doc_type}):\n{parent_text}\n"
            )
            citations.append({
                "index": i,
                "text": parent_text,
                "company": c.company,
                "year": c.year,
                "doc_type": c.doc_type,
                "source_file": c.payload.get("source_file", ""),
                "doc_id": c.payload.get("source_file", "").replace(".md", ""),
                "page": c.payload.get("page", 1),
                "score": c.score,
            })
        context = "\n---\n".join(context_parts)
        # 上下文截断：安全模式上下文减半（小模型 CPU 推理慢）
        CONTEXT_MAX_CHARS = 1500 if mode == "safe" else 3000
        if len(context) > CONTEXT_MAX_CHARS:
            truncated_parts = []
            for part in context_parts:
                candidate = "\n---\n".join(truncated_parts + [part])
                if len(candidate) > CONTEXT_MAX_CHARS and truncated_parts:
                    break
                truncated_parts.append(part)
            context = "\n---\n".join(truncated_parts)
            logger.info(f"   ✂️ 上下文截断: {len(context)} 字（原 {sum(len(p) for p in context_parts)} 字）")
        span_ctx.add_outputs({
            "num_citations": len(citations),
            "source_docs": list(set(c["doc_id"] for c in citations)),
            "source_types": list(set(c["doc_type"] for c in citations)),
            "context_length": len(context),
            "max_rrf_score": max_rrf_score,
        })

    # 5. Generate answer（含对话历史注入 + 拒答检查）
    REJECT_SCORE_THRESHOLD = 0.005  # RRF分数阈值（原始MIN_RESULT_SCORE值）
    REJECT_MIN_CHUNKS = 1
    rejection = None

    # 条件1: 最高RRF分数过低
    if max_rrf_score < REJECT_SCORE_THRESHOLD:
        rejection = f"未检索到与问题相关的内容（检索相关性评分{max_rrf_score:.4f}低于阈值）"
    # 条件2: 几乎没有有效结果
    elif len([c for c in citations if c.get("score", 0) > 0]) <= REJECT_MIN_CHUNKS:
        rejection = f"仅找到{len(citations)}条弱相关结果，不足以回答该问题"

    # 条件3: 查询提到具体公司但检索结果中未覆盖
    if not rejection:
        query_entities = [e for e in KNOWN_ENTITIES if e in resolved_query]
        if query_entities:
            retrieved_companies = set(
                normalize_company(c.get("company", ""))
                for c in citations if c.get("company")
            )
            query_normalized = [normalize_company(e) for e in query_entities]
            if not retrieved_companies.intersection(query_normalized):
                rejection = f"检索结果中未包含关于{'/'.join(query_entities)}的有效信息"

    if rejection:
        logger.warning(f"🚫 拒答: {rejection}")
        result = {
            "answer": f"抱歉，我无法回答这个问题。{rejection}。请尝试换个方式提问，或确认该信息是否在已上传的文档中。",
            "citations": citations,
            "intent": intent,
            "context": context,
            "elapsed_sec": time.perf_counter() - t0,
            "rejected": True,
            "rejection_reason": rejection,
        }
        cached = None
    else:
        # 5. Generate answer（含对话历史注入）
        with trace("generate", run_type="llm") as span_gen:
            llm = _get_llm(mode=mode)
            system_prompt = """你是一个专业的金融分析师。请基于检索到的资料回答用户的问题。

🚨 **强制引用规则（必须遵守）：**
1. **每个数值必须紧跟来源标注**，使用 `[来源N]` 格式，例如：`营业收入为405.29亿元[来源1]`
2. **百分数、倍数、增长率等所有数字型数据都必须标注来源**
3. **不可在一个句尾统一标注**，必须每个数值单独标注
4. 如果同一数值出现在多个来源中，标注最新/最可靠的那个

示例：
✅ 正确：贵州茅台2025年营业总收入为1741.44亿元[来源1]，同比增长15.8%[来源1]
❌ 错误：贵州茅台2025年营业总收入为1741.44亿元，同比增长15.8%[来源1]
❌ 错误：贵州茅台2025年营业总收入为1741.44亿元，以上数据来自[来源1]

要求：
1. 直接回答问题，给出具体数值或分析结论
2. 引用来源（[来源1], [来源2]）— **每个数值单独标注**
3. 如果数据不完整，说明哪些数据缺失
4. 语言简洁专业，适合资本市场从业者阅读
5. 不要编造数据

⚠️ 重要原则：
- 如果检索到的是券商研报内容，优先引用研报中的观点、预测和评级
- 如果检索到的是财务报告，优先采用合并利润表的数据
- 如果同一信息出现在多个来源中，交叉验证后引用最可靠的那个"""

            # 注入历史对话（用于多轮上下文、指代消解）
            current_turn = {
                "role": "user",
                "content": f"## 问题\n{resolved_query}\n\n## 参考资料\n{context}\n\n请基于以上资料回答问题。",
            }
            messages = [{"role": "system", "content": system_prompt}, *history_msgs, current_turn]
            llm_output = llm.chat(messages, temperature=0.1, max_tokens=4096)
            span_gen.add_outputs({
                "answer_length": len(llm_output),
                "model": "deepseek-chat",
                "has_history": bool(history_msgs),
            })

            # 6. Citation verification — 根据意图类型自动判断
            verify_mode = strategy.get("verify", "skip")
            if verify_mode != "skip":
                with trace("citation_verify", run_type="chain") as span_verify:
                    from backend.services.citation_verifier import verify_answer, add_verification_footer
                    verify_result = verify_answer(llm_output, citations, show_all=(verify_mode == "full"))
                    if not verify_result["passed"]:
                        logger.warning(
                            f"⚠️ CitationVerifier: {verify_result['failed']}/{verify_result['total_cited']} failed, "
                            f"{len(verify_result['issues'])} issues total"
                        )
                        for issue in verify_result["issues"]:
                            logger.warning(f"  [{issue['type']}] {issue['description'][:120]}")
                        llm_output = add_verification_footer(llm_output, verify_result)
                    span_verify.add_outputs({
                        "verify_passed": verify_result["passed"],
                        "total_cited": verify_result["total_cited"],
                        "failed": verify_result["failed"],
                        "num_issues": len(verify_result["issues"]),
                        "verify_mode": verify_mode,
                    })
            else:
                verify_result = {
                    "passed": True, "total_cited": 0,
                    "verified": 0, "failed": 0,
                    "issues": [], "details": [],
                    "message": "引用验证已跳过（computational意图）",
                }

            elapsed = time.perf_counter() - t0

            result = {
                "answer": llm_output,
                "citations": citations,
                "intent": intent,
                "context": context,
                "elapsed_sec": round(elapsed, 1),
                "verification": {
                    "passed": verify_result["passed"],
                    "total_cited": verify_result["total_cited"],
                    "failed": verify_result["failed"],
                    "num_issues": len(verify_result["issues"]),
                },
            }

        # 写入查询缓存
        query_cache.set(qkey, result)

    return result


# ── SSE 流式生成 ─────────────────────────────────────────

async def rag_stream(
    query: str,
    top_k: int = 8,
    session_id: str | None = None,
    mode: str = "pro",
) -> AsyncGenerator[str, None]:
    """
    流式 RAG 查询。
    通过 SSE 协议推送：thought → search → answer(逐字) → done
    """
    t0 = time.perf_counter()

    try:
        # ⭐ 查询缓存检查（Layer 4）
        from backend.services.cache import query_cache, make_key
        qkey = f"q:{make_key(query=query, top_k=top_k, reranker=False, mode=mode)}"
        cached = query_cache.get(qkey)
        if cached is not None:
            logger.info(f"⚡ 查询缓存命中(stream): \"{query[:40]}...\"")
            yield _sse("thought", "缓存命中，直接返回结果")
            yield _sse("answer", cached["answer"], {
                "citations": cached.get("citations", []),
                "intent": cached.get("intent", ""),
                "elapsed_sec": 0.0,
            })
            yield _sse("done", "", {"elapsed_sec": 0.0, "from_cache": True})
            return

        # ⭐ 预算守卫（Cache miss → 即将调用 LLM，先检查预算）
        from backend.services.budget_guard import check_budget
        budget = check_budget(session_id or "default")
        if budget["action"] == "reject":
            logger.warning(f"⛔ 预算守卫拦截(stream): {budget['message']}")
            yield _sse("error", f"❌ 预算已耗尽，无法继续回答。{budget.get('message', '')}", {"budget_blocked": True})
            return
        if budget["action"] in ("warn", "cache_only"):
            logger.info(f"⚠️ 预算警告(stream): {budget['message']}")
            if budget["action"] == "cache_only":
                pass  # 流式模式暂不支持 cache_only 降级，仅记录

        # 1. Query Rewriting — 默认跳过 LLM 改写（用关键词规则替代）
        skip_rewrite = os.environ.get("SKIP_QUERY_REWRITE", "1") != "0"
        history_msgs = _load_history(session_id) if session_id else []
        if skip_rewrite and history_msgs:
            skip_rewrite = False  # 有历史对话 → 启用 LLM 改写（指代消解）

        yield _sse("thought", "正在理解问题并提取关键信息...")

        if skip_rewrite:
            # 关键词规则路径（零LLM成本）
            expanded = expand_financial_query(query)
            resolved_query = expanded[0]
            company = ""
            for kw in KNOWN_ENTITIES:
                if kw in query:
                    company = kw
                    break
            if company:
                company = normalize_company(company)
            year = ""
            ym = re.search(r"(20[0-9]{2})", query)
            if ym:
                year = ym.group(1)
            # 简单意图检测（基于关键词）
            intent = "factual"
            comp_kw = ["对比", "比较", "vs", "VS", "不同", "区别", "哪个更"]
            summ_kw = ["总结", "概括", "列举", "哪些", "几个主要", "归纳", "综述", "汇总"]
            reason_kw = ["为什么", "原因", "说明什么", "驱动", "逻辑", "意味着"]
            calc_kw = ["计算一下", "计算得出", "推算", "估算", "同比变化", "变化幅度", "增长率", "增减速"]
            if any(kw in query for kw in comp_kw):
                intent = "comparative"
            elif any(kw in query for kw in summ_kw):
                intent = "summary"
            elif any(kw in query for kw in reason_kw):
                intent = "analytical"
            elif any(kw in query for kw in calc_kw):
                intent = "computational"
        else:
            kb = _get_kb()
            rewriter = _get_rewriter()
            rewrite_result = rewriter.rewrite(query, history_msgs)
            resolved_query = rewrite_result.get("resolved_query", query)
            raw_company = rewrite_result.get("company", "")
            year = rewrite_result.get("year", "")

            # 通过 KB 标准化公司名（含 LLM 提取失败的兜底）
            company = ""
            if raw_company:
                matched = kb.search_company(raw_company)
                if matched:
                    company = raw_company
                    logger.info(f"   🏢 KB匹配公司(LLM): {company}")
                else:
                    company = raw_company
            if not company:
                text_extracted = kb.extract_company_from_text(query)
                if text_extracted:
                    company = text_extracted
                    logger.info(f"   🏢 KB提取公司(正则): {company}")

            # Intent Routing（LLM 版）
            router = _get_router()
            intent_result = router.route(resolved_query)
            intent = intent_result.get("intent", "factual")

        # 映射 KB 标准名 → Qdrant Payload 公司名
        # 注意：从 queries 提取的公司名已通过 normalize_company() 转换为规范名，
        # 因此不需要额外映射。只有当 Qdrant payload 的公司名存储格式不同时才需映射。
        _KB_TO_QDRANT_COMPANY = {
            "特变电工": "特变电工股份",     # Qdrant payload 为 "特变电工股份"
        }
        if company in _KB_TO_QDRANT_COMPANY:
            logger.info(f"   🏢 公司名映射: \"{company}\" → \"{_KB_TO_QDRANT_COMPANY[company]}\"")
            company = _KB_TO_QDRANT_COMPANY[company]

        yield _sse("thought", f"已补全查询: {resolved_query}")
        yield _sse("thought", f"意图识别: {intent}")

        # 3. Retrieval（多阶段 + 软过滤 + 意图控制）
        yield _sse("search", "正在检索知识库...")

        retriever = _get_retriever(mode=mode)
        
        # 构建严格过滤条件
        strict_filters = {}
        if company:
            strict_filters["company"] = company
        
        # 意图→检索策略映射（含验证模式）
        intent_strategies = {
            "factual":       {"top_k_final": 8,  "use_reranker": False, "score_threshold": 0.1,  "verify": "light"},
            "analytical":    {"top_k_final": 12, "use_reranker": True,  "score_threshold": 0.1,  "verify": "full"},
            "comparative":   {"top_k_final": 15, "use_reranker": True,  "score_threshold": 0.1,  "verify": "full"},
            "summary":       {"top_k_final": 10, "use_reranker": True,  "score_threshold": 0.1,  "verify": "light"},
            "computational": {"top_k_final": 8,  "use_reranker": False, "score_threshold": 0.1,  "verify": "skip"},
        }
        strategy = intent_strategies.get(intent, intent_strategies["factual"])

        # ── 多实体检测（流式版，与rag_query保持一致） ──
        detected_entities = [e for e in KNOWN_ENTITIES if e in resolved_query]

        if len(detected_entities) >= 2:
            comparison_noise = ["对比", "比较", "vs", "VS", "不同", "区别", "哪个", "更", "分别",
                                "哪一个", "差异", "孰高孰低", "怎么", "和", "与", "跟", "及"]
            clean_base = resolved_query
            for noise in comparison_noise:
                clean_base = clean_base.replace(noise, "").strip()
            for ent in detected_entities:
                clean_base = clean_base.replace(ent, "").strip()
            clean_base = re.sub(r'[,，。？、\s]+', ' ', clean_base).strip()

            all_chunks = []
            seen_parents = set()
            from threading import Lock
            merge_lock = Lock()
            from concurrent.futures import ThreadPoolExecutor, as_completed

            def _merge_stream_results(entity_results):
                nonlocal all_chunks, seen_parents
                with merge_lock:
                    for r in entity_results:
                        pid = r.child_chunk.parent_id
                        if pid not in seen_parents:
                            seen_parents.add(pid)
                            all_chunks.append(r)

            with ThreadPoolExecutor(max_workers=len(detected_entities)) as executor:
                futures = []
                for entity in detected_entities:
                    sub_query = clean_base
                    for other in detected_entities:
                        if other != entity:
                            sub_query = sub_query.replace(other, "").strip()
                    sub_query = f"{entity} {sub_query}"
                    sub_query = re.sub(r'\s+', ' ', sub_query).strip()
                    canonical = normalize_company(entity)
                    future = executor.submit(
                        retriever.retrieve,
                        query=sub_query,
                        top_k_final=max(8, strategy["top_k_final"] // len(detected_entities)),
                        use_reranker=strategy["use_reranker"],
                        strict_filters={"company": canonical},
                        score_threshold=0.0,
                        intent=intent,
                        with_parent=True,
                    )
                    futures.append(future)
                for future in as_completed(futures):
                    try:
                        entity_results = future.result()
                        _merge_stream_results(entity_results)
                    except Exception as e:
                        logger.warning(f"   ⚠️ 实体检索失败: {e}")

            all_chunks.sort(key=lambda x: x.child_chunk.score, reverse=True)
            chunks = all_chunks[:strategy["top_k_final"]]
            logger.info(f"   🔀 多实体检索(流式): {len(detected_entities)} 个实体 → {len(chunks)} 条合并结果")
        else:
            # ── 单实体/无实体检索（原有流式逻辑） ──
            comparison_kw = ["对比", "比较", "vs", "VS", "不同", "区别", "哪个更", "差异"]
            is_lightweight = (
                intent == "factual"
                and len([e for e in KNOWN_ENTITIES if e in resolved_query]) <= 1
                and not any(kw in resolved_query for kw in comparison_kw)
            )
            retrieve_kwargs = dict(
                query=resolved_query,
                top_k_final=5 if is_lightweight else strategy["top_k_final"],
                use_reranker=strategy["use_reranker"],
                strict_filters=strict_filters if strict_filters else None,
                score_threshold=strategy["score_threshold"],
                intent=intent,
                with_parent=True,
            )
            if is_lightweight:
                retrieve_kwargs["top_k_bm25"] = 0
            chunks = retriever.retrieve(**retrieve_kwargs)

        # 财务感知增强（同 graph.py node_retrieve_factual 的逻辑）
        FINANCIAL_TERM_TO_STATEMENT = {
            "净利润": "合并利润表",
            "营业收入": "合并利润表",
            "营业成本": "合并利润表",
            "毛利率": "合并利润表",
            "营业利润": "合并利润表",
            "归母净利润": "合并利润表",
            "资产": "合并资产负债表",
            "负债": "合并资产负债表",
            "现金流": "合并现金流量表",
            "经营性现金流": "合并现金流量表",
            "净资产": "合并资产负债表",
            "ROE": "合并利润表",
        }
        should_augment = any(term in query for term in FINANCIAL_TERM_TO_STATEMENT)
        if should_augment and len(chunks) < 6:
            target_statements = set()
            for term, stmt in FINANCIAL_TERM_TO_STATEMENT.items():
                if term in query:
                    target_statements.add(stmt)
            for stmt in target_statements:
                try:
                    extra = retriever.retrieve(
                        query=stmt,
                        top_k_final=5,
                        use_reranker=False,
                        filters=strict_filters or None,
                        with_parent=True,
                    )
                    # 去重合并
                    existing_ids = {r.child_chunk.point_id for r in chunks}
                    for e in extra:
                        if e.child_chunk.point_id not in existing_ids:
                            chunks.append(e)
                            existing_ids.add(e.child_chunk.point_id)
                except Exception as e:
                    logger.warning(f"   财务增强检索失败: {e}")
            logger.info(f"   📋 财务增强后: {len(chunks)} 条结果")

        yield _sse("search", f"检索到 {len(chunks)} 条相关结果")

        # 4. Build context（含去重）
        seen_ids = set()
        context_parts = []
        citations = []
        for i, r in enumerate(chunks, 1):
            c = r.child_chunk
            if c.point_id in seen_ids:
                continue
            seen_ids.add(c.point_id)
            parent_text = r.parent_chunk.text if r.parent_chunk else c.text
            context_parts.append(f"[来源{i}] ({c.company}, {c.year}, {c.doc_type}):\n{parent_text}")
            citations.append({
                "index": i,
                "company": c.company,
                "year": c.year,
                "doc_type": c.doc_type,
                "source_file": c.payload.get("source_file", ""),
                "doc_id": c.payload.get("source_file", "").replace(".md", ""),
                "text": parent_text,
                "page": c.payload.get("page", 1),
            })
        context = "\n---\n".join(context_parts)
        # 上下文截断：安全模式上下文减半（小模型 CPU 推理慢）
        CONTEXT_MAX_CHARS = 1500 if mode == "safe" else 3000
        if len(context) > CONTEXT_MAX_CHARS:
            truncated_parts = []
            for part in context_parts:
                candidate = "\n---\n".join(truncated_parts + [part])
                if len(candidate) > CONTEXT_MAX_CHARS and truncated_parts:
                    break
                truncated_parts.append(part)
            context = "\n---\n".join(truncated_parts)
            logger.info(f"   ✂️ 上下文截断(stream): {len(context)} 字（原 {sum(len(p) for p in context_parts)} 字）")

        # 5. Stream LLM answer — token-by-token（含对话历史注入）
        yield _sse("answer", "", {
            "citations": citations,
            "intent": intent,
        })

        llm = _get_llm(mode=mode)
        system_prompt = """你是一个专业的金融分析师。请基于检索到的资料回答用户的问题。

🚨 **强制引用规则（必须遵守）：**
1. **每个数值必须紧跟来源标注**，使用 `[来源N]` 格式，例如：`营业收入为405.29亿元[来源1]`
2. **百分数、倍数、增长率等所有数字型数据都必须标注来源**
3. **不可在一个句尾统一标注**，必须每个数值单独标注
4. 如果同一数值出现在多个来源中，标注最新/最可靠的那个

示例：
✅ 正确：贵州茅台2025年营业总收入为1741.44亿元[来源1]，同比增长15.8%[来源1]
❌ 错误：贵州茅台2025年营业总收入为1741.44亿元，同比增长15.8%[来源1]
❌ 错误：贵州茅台2025年营业总收入为1741.44亿元，以上数据来自[来源1]

要求：
1. 直接回答问题，给出具体数值或分析结论
2. 引用来源（[来源1], [来源2]）— **每个数值单独标注**
3. 如果数据不完整，说明哪些数据缺失
4. 语言简洁专业，适合资本市场从业者阅读
5. 不要编造数据

⚠️ 重要原则：
- 如果检索到的是券商研报内容，优先引用研报中的观点、预测和评级
- 如果检索到的是财务报告，优先采用合并利润表的数据
- 如果同一信息出现在多个来源中，交叉验证后引用最可靠的那个"""

        # 注入历史对话（用于多轮上下文、指代消解）
        history_msgs = _load_history(session_id) if session_id else []
        current_turn = {
            "role": "user",
            "content": f"## 问题\n{resolved_query}\n\n## 参考资料\n{context}\n\n请基于以上资料回答问题。",
        }
        messages = [{"role": "system", "content": system_prompt}, *history_msgs, current_turn]

        # Stream tokens one by one with timeout
        full_answer = ""
        LLM_TIMEOUT = 600 if mode == "safe" else 120
        try:
            async with asyncio.timeout(LLM_TIMEOUT):
                sslm_kwargs = dict(temperature=0.1, max_tokens=4096)
                if mode == "safe":
                    sslm_kwargs["timeout"] = 300  # Ollama CPU 慢，给足时间
                async for token in llm.chat_stream(messages, **sslm_kwargs):
                    full_answer += token
                    yield _sse("delta", token)
        except asyncio.TimeoutError:
            err_msg = f"LLM 生成超时（超过{LLM_TIMEOUT}秒）"
            logger.error(f"  {err_msg}")
            yield _sse("error", err_msg)

        elapsed = time.perf_counter() - t0

        # ⭐ 上报本次 LLM 调用的 token/费用追踪
        try:
            from backend.services.cost_tracker import track_call
            # 估算 token（中文字符约 1.5-2 token/字，保守估 3 char/token）
            prompt_chars = sum(len(m.get("content", "")) for m in messages)
            prompt_tokens = max(100, prompt_chars // 2)
            completion_tokens = max(10, len(full_answer) // 2)
            track_call(
                session_id=session_id or "default",
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                model="deepseek-chat" if mode == "pro" else f"ollama:{settings.SAFE_LLM_MODEL}",
                caller="rag_stream",
            )
        except Exception as e:
            logger.warning(f"⚠️ cost_tracker 上报失败 (非致命): {e}")

        # Citation verification — 根据意图类型自动判断
        verify_mode = strategy.get("verify", "skip")
        if verify_mode != "skip":
            try:
                from backend.services.citation_verifier import verify_answer
                verify_result = verify_answer(full_answer, citations, show_all=(verify_mode == "full"))
                if not verify_result["passed"]:
                    logger.warning(
                        f"⚠️ CitationVerifier: {verify_result['failed']}/{verify_result['total_cited']} failed, "
                        f"{len(verify_result['issues'])} issues total"
                    )
            except Exception as e:
                logger.warning(f"CitationVerifier error (non-fatal): {e}")
                verify_result = {"passed": True, "total_cited": 0, "verified": 0, "failed": 0, "issues": [], "details": []}
        else:
            verify_result = {
                "passed": True, "total_cited": 0,
                "verified": 0, "failed": 0,
                "issues": [], "details": [],
                "message": "引用验证已跳过（computational意图）",
            }

        # Done — send final answer with metadata
        yield _sse("answer", full_answer, {
            "citations": citations,
            "intent": intent,
            "elapsed_sec": round(elapsed, 1),
            "verification": {
                "passed": verify_result["passed"],
                "total_cited": verify_result["total_cited"],
                "failed": verify_result["failed"],
                "num_issues": len(verify_result["issues"]),
            },
        })
        yield _sse("done", "", {"elapsed_sec": round(elapsed, 1)})

        # 写入查询缓存
        try:
            query_cache.set(qkey, {
                "answer": full_answer,
                "citations": citations,
                "intent": intent,
                "context": context,
                "elapsed_sec": round(elapsed, 1),
            })
        except Exception as cache_err:
            logger.warning(f"⚠️ 查询缓存写入失败: {cache_err}")

    except Exception as e:
        logger.error(f"RAG stream error: {e}")
        yield _sse("error", f"处理请求时出错: {str(e)}")


def _sse(event_type: str, content: str = "", data: Optional[dict] = None) -> str:
    """生成 SSE 格式消息。"""
    payload = {"type": event_type, "content": content}
    if data:
        payload["data"] = data
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


import asyncio  # noqa: E402 — 延迟导入避免循环
