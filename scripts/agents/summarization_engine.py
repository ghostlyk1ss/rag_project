"""
finRAG Agent — Map-Reduce 查询聚焦摘要引擎
============================================

对"总结 XX 年报核心内容"、"概括券商对 X 的主要观点"
等需要多文档归纳的问题，执行 Map-Reduce 三步流程：

Map:   宽召回检索 → 按公司/文档类型分组 → LLM 提取每组要点
Reduce: 合并去重 → LLM 生成结构化摘要 → 标注来源

用法:
    from scripts.agents.summarization_engine import SummarizationEngine
    engine = SummarizationEngine()
    result = engine.summarize(query, retriever)
"""

import json
import logging
import time
from collections import defaultdict

from scripts.agents.llm import get_llm

logger = logging.getLogger("agent.summarization")

# ── Map: 每组的提取 Prompt ──────────────────────────────────────
MAP_SYSTEM_PROMPT = """你是一个金融分析师。以下是检索到的部分文档内容，请从中提取
与用户问题相关的{{num}}个关键要点。

要求：
1. 每个要点必须能从提供的文本中找到依据
2. 标注每个要点对应的来源索引（[来源i]）
3. 要点要精炼（< 50 字）
4. 如果该组文档中没有相关信息，输出空列表

输出 JSON: {{"points": [{{"point": "要点描述", "sources": ["来源1", "来源2"]}}]}}"""

MAP_USER_PROMPT = """用户问题：{query}

以下是从{group_name}检索到的内容：
{context}

请提取{group_name}中与问题相关的关键要点："""

# ── Reduce: 合并 Prompt ─────────────────────────────────────────
REDUCE_SYSTEM_PROMPT = """你是一个金融分析师。请将以下多组提取的要点合并为一份
结构化摘要。

要求：
1. 合并相同/相似的要点，去重
2. 保留每个要点的来源标注
3. 按主题分组（如有多个主题）
4. 每个主题下要点按重要性排序
5. 如果某个要点只出现一次但很重要，保留
6. 输出简洁的中文摘要，适合金融从业者阅读

输出 JSON格式:
{{
    "summary": "总体概括（1-2句话）",
    "themes": [
        {{
            "theme": "主题名称",
            "points": [
                {{"point": "要点描述", "sources": ["来源1", "来源2"]}}
            ]
        }}
    ]
}}"""

REDUCE_USER_PROMPT = """用户问题：{query}

以下是从各文档组中提取的要点，请合并去重并生成结构化摘要：

{all_points}"""


class SummarizationEngine:
    """Map-Reduce 查询聚焦摘要引擎。"""

    def __init__(self):
        self.llm = get_llm()

    def summarize(self, query: str, retriever, filters: dict = None) -> dict:
        """
        执行 Map-Reduce 摘要。

        参数:
            query: 用户问题
            retriever: HybridRetriever 实例
            filters: 可选的过滤条件

        返回:
            {
                "summary": "结构化摘要文本",
                "themes": [...],
                "raw_points": [...],
                "source_count": 5,
            }
        """
        t0 = time.perf_counter()
        logger.info(f"📋 Map-Reduce 摘要: \"{query}\"")

        # ── Step 1: Map - 宽召回检索 ──────────────────────────────
        # 摘要类查询需要更大范围的召回（宽 recall）
        chunks = retriever.retrieve(
            query=query,
            top_k_vector=30,       # 向量搜 30 条（比 factual 多）
            top_k_bm25=30,         # BM25 搜 30 条
            top_k_final=20,        # 取前 20 条
            use_reranker=False,    # 摘要不需要 reranker
            filters=filters or None,
            with_parent=True,
        )
        logger.info(f"   📡 检索: {len(chunks)} 条结果")

        if not chunks:
            return {"summary": "未检索到相关内容。", "themes": [], "raw_points": [], "source_count": 0}

        # ── Step 2: 按文档类型/公司分组 ───────────────────────────
        groups = defaultdict(list)
        for i, r in enumerate(chunks, 1):
            c = r.child_chunk
            parent_text = r.parent_chunk.text if r.parent_chunk else c.text
            # 组 key: 公司+文档类型（同一公司的研报/年报分在一起）
            group_key = f"{c.company} ({c.doc_type})"
            groups[group_key].append({
                "index": i,
                "text": parent_text[:1000],  # 截断避免 token 溢出
                "company": c.company,
                "doc_type": c.doc_type,
                "year": c.year,
            })

        # ── Step 3: Map - 每组 LLM 提取要点 ───────────────────────
        all_raw_points = []
        for group_name, items in groups.items():
            logger.info(f"   🔍 Map: {group_name} ({len(items)} 条)")

            # 构建组上下文
            context_parts = []
            for item in items:
                context_parts.append(f"[来源{item['index']}] ({item['company']}, {item['year']}): {item['text']}")
            context = "\n\n".join(context_parts)

            # LLM 提取要点
            try:
                prompt = MAP_USER_PROMPT.format(query=query, group_name=group_name, context=context)
                max_points = min(5, len(items) * 2)  # 动态调整
                system = MAP_SYSTEM_PROMPT.replace("{{num}}", str(max_points))

                messages = [
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ]
                result = self.llm.chat_json(messages, temperature=0.1, max_tokens=2048)
                points = result.get("points", [])

                for p in points:
                    p["_group"] = group_name
                    all_raw_points.append(p)
                logger.info(f"      ✅ 提取 {len(points)} 个要点")
            except Exception as e:
                logger.warning(f"      ❌ 提取失败: {e}")

            time.sleep(0.3)  # 速率限制

        if not all_raw_points:
            return {"summary": "未能从检索内容中提取有效要点。", "themes": [], "raw_points": [], "source_count": len(chunks)}

        # ── Step 4: Reduce - LLM 合并去重 ─────────────────────────
        logger.info(f"   🔄 Reduce: 合并 {len(all_raw_points)} 个要点...")
        points_text = json.dumps([{
            "point": p["point"],
            "sources": p.get("sources", [p.get("_group", "?")]),
        } for p in all_raw_points], ensure_ascii=False, indent=2)

        try:
            reduce_messages = [
                {"role": "system", "content": REDUCE_SYSTEM_PROMPT},
                {"role": "user", "content": REDUCE_USER_PROMPT.format(
                    query=query, all_points=points_text
                )},
            ]
            result = self.llm.chat_json(reduce_messages, temperature=0.1, max_tokens=4096)
            summary_text = result.get("summary", "")
            themes = result.get("themes", [])
            logger.info(f"   ✅ Reduce: {len(themes)} 个主题")
        except Exception as e:
            logger.warning(f"   ❌ Reduce 失败: {e}")
            summary_text = ""
            themes = []

        elapsed = time.perf_counter() - t0
        logger.info(f"   📊 摘要完成 ({elapsed:.1f}s): {len(all_raw_points)} 原始要点 → {sum(1 for t in themes for _ in t.get('points',[]))} 最终要点")

        return {
            "summary": summary_text,
            "themes": themes,
            "raw_points": all_raw_points,
            "source_count": len(chunks),
        }
