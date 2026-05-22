"""
finRAG Agent — 多文档主题聚合与观点溯源引擎
==============================================

对"券商对 X 的观点分析"、"各机构对 X 行业的判断"
等跨文档主题分析问题，执行观点聚合流水线：

1. 检索: 宽召回获取多份券商研报/评级报告
2. 提取: 每份文档 → LLM 提取结构化观点（投资主题/业绩预测/行业判断/风险）
3. 聚类: LLM 将相似观点归为同一主题
4. 矩阵: 按主题分组展示各机构观点，标注共识 vs 分歧

用法:
    from scripts.agents.theme_aggregator import ThemeAggregator
    engine = ThemeAggregator()
    result = engine.analyze("券商对天齐锂业的观点")
"""

import json
import logging
import time
from collections import defaultdict

from scripts.agents.llm import get_llm

logger = logging.getLogger("agent.theme_aggregator")

# ══════════════════════════════════════════════════════════════════
#  Prompt 模板
# ══════════════════════════════════════════════════════════════════

# Step 1: 单文档观点提取
EXTRACT_SYSTEM_PROMPT = """你是一个金融研报分析助手。分析以下研究报告，
提取关键观点信息。只输出 JSON。

输出格式：
{
    "source": "报告标题",
    "investment_theme": "核心投资观点/评级（1句话）",
    "financial_prediction": "财务预测数据（收入/利润预测值，若有）",
    "industry_judgment": "行业判断/展望（1句话，若有）",
    "risk_factors": "风险提示（1句话，若有，否则空字符串）",
    "key_metrics": ["关键指标1", "关键指标2"],
    "target_price": "目标价（若有，否则空字符串）",
    "rating": "评级（买入/增持/中性/减持，若有）"
}"""

EXTRACT_USER_PROMPT = """报告来源：{source}

报告内容（前 4000 字）：
{text}

请提取该报告的关键观点信息："""

# Step 2: 跨文档主题聚类
CLUSTER_SYSTEM_PROMPT = """你是一个金融研报分析专家。请将以下多份研报的
观点按主题进行聚类。

要求：
1. 识别 3-5 个核心主题（如：业绩预测、行业前景、估值评级、风险提示、战略布局）
2. 每个主题下列出各机构/研报的观点
3. 标注哪些观点是 consensus（共识）还是 divergence（分歧）
4. 如果同一主题下有冲突观点，明确指出来源

输出 JSON：
{
    "overall_summary": "整体观点概览（1-2句话）",
    "themes": [
        {
            "theme": "主题名称",
            "consensus_level": "high/medium/low/none",
            "summary": "该主题下的观点概况",
            "viewpoints": [
                {
                    "source": "报告来源简称",
                    "view": "观点描述",
                    "stance": "positive/neutral/negative",
                    "confidence": "high/medium/low"
                }
            ]
        }
    ]
}"""

CLUSTER_USER_PROMPT = """分析主题：{query}

以下是从 {num_sources} 份报告中提取的观点，请按主题聚类分析：

{all_extractions}"""


class ThemeAggregator:
    """多文档主题聚合与观点溯源引擎。"""

    def __init__(self):
        self.llm = get_llm()

    def analyze(
        self,
        query: str,
        retriever,
        filters: dict = None,
        max_sources: int = 15,
    ) -> dict:
        """
        执行多文档主题聚合分析。

        参数:
            query: 用户问题
            retriever: HybridRetriever 实例
            filters: 可选过滤条件（如 company="天齐锂业"）
            max_sources: 最大分析文档数

        返回:
            {
                "overall_summary": "整体概览",
                "themes": [...],          # 按主题分组的观点
                "num_sources": 10,         # 分析文档数
                "source_list": [...],      # 来源文档列表
                "matrix_text": "...",      # 文本格式的矩阵
            }
        """
        t0 = time.perf_counter()
        logger.info(f"📊 主题聚合分析: \"{query}\"")

        # ── Step 1: 宽召回检索 ──────────────────────────────
        chunks = retriever.retrieve(
            query=query,
            top_k_vector=40,
            top_k_bm25=40,
            top_k_final=max_sources,
            use_reranker=False,
            filters=filters or None,
            with_parent=True,
        )
        logger.info(f"   📡 检索: {len(chunks)} 条")

        if not chunks:
            return {
                "overall_summary": "未检索到相关文档。",
                "themes": [],
                "num_sources": 0,
                "source_list": [],
                "matrix_text": "未检索到相关文档。",
            }

        # ── Step 2: 按文档去重聚合（同一文档可能有多个 chunk） ──
        doc_map = {}  # source_file → {company, year, text, source}
        for r in chunks:
            c = r.child_chunk
            parent_text = r.parent_chunk.text if r.parent_chunk else c.text
            key = c.payload.get("source_file", f"{c.company}_{c.year}_{c.doc_type}_{c.payload.get('section','')}")

            if key not in doc_map:
                doc_map[key] = {
                    "company": c.company,
                    "year": c.year,
                    "doc_type": c.doc_type,
                    "source": c.payload.get("source", ""),
                    "text_parts": [],
                }
            doc_map[key]["text_parts"].append(parent_text[:2000])

        docs = list(doc_map.values())[:max_sources]
        logger.info(f"   📄 {len(docs)} 个独立文档")

        # ── Step 3: 逐文档提取结构化观点 ────────────────────────
        extractions = []
        for i, doc in enumerate(docs):
            # 合并文本片段
            combined = "\n".join(doc["text_parts"])[:4000]
            source_label = f"{doc['source']} {doc['company']}（{doc['doc_type']}, {doc['year']}）"

            logger.info(f"   🔍 [{i+1}/{len(docs)}] {source_label[:50]}...")
            try:
                prompt = EXTRACT_USER_PROMPT.format(
                    source=source_label,
                    text=combined,
                )
                messages = [
                    {"role": "system", "content": EXTRACT_SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ]
                result = self.llm.chat_json(messages, temperature=0.1, max_tokens=1024)
                result["_source_label"] = source_label
                result["_company"] = doc["company"]
                extractions.append(result)
                logger.info(f"      ✅ 提取完成: {result.get('investment_theme','')[:50]}")
            except Exception as e:
                logger.warning(f"      ❌ 提取失败: {e}")

            time.sleep(0.3)  # 速率限制

        if not extractions:
            return {
                "overall_summary": "未能从文档中提取有效观点。",
                "themes": [],
                "num_sources": len(docs),
                "source_list": [d["source"] for d in docs],
                "matrix_text": "未能提取有效观点。",
            }

        # ── Step 4: LLM 主题聚类 ────────────────────────────
        logger.info(f"   🔄 聚类: {len(extractions)} 份提取结果...")
        all_json = json.dumps(
            [{
                "source": e["_source_label"],
                "investment_theme": e.get("investment_theme", ""),
                "financial_prediction": e.get("financial_prediction", ""),
                "industry_judgment": e.get("industry_judgment", ""),
                "risk_factors": e.get("risk_factors", ""),
                "rating": e.get("rating", ""),
                "target_price": e.get("target_price", ""),
            } for e in extractions],
            ensure_ascii=False, indent=2,
        )

        try:
            cluster_messages = [
                {"role": "system", "content": CLUSTER_SYSTEM_PROMPT},
                {"role": "user", "content": CLUSTER_USER_PROMPT.format(
                    query=query,
                    num_sources=len(extractions),
                    all_extractions=all_json,
                )},
            ]
            cluster_result = self.llm.chat_json(
                cluster_messages, temperature=0.1, max_tokens=4096
            )
            overall_summary = cluster_result.get("overall_summary", "")
            themes = cluster_result.get("themes", [])
            logger.info(f"   ✅ 聚类: {len(themes)} 个主题")
        except Exception as e:
            logger.warning(f"   ❌ 聚类失败: {e}")
            overall_summary = ""
            themes = []

        # ── Step 5: 构建文本矩阵 ────────────────────────────
        matrix_lines = [f"【观点矩阵】{overall_summary}\n"]
        for t in themes:
            theme = t.get("theme", "")
            consensus = t.get("consensus_level", "unknown")
            emoji = {"high": "✅", "medium": "🟡", "low": "🔴", "none": "⚪"}.get(consensus, "❓")
            summary = t.get("summary", "")
            matrix_lines.append(f"\n## {emoji} {theme}")
            matrix_lines.append(f"  共识度: {consensus} | {summary}")

            for v in t.get("viewpoints", []):
                source = v.get("source", "?")
                view = v.get("view", "")
                stance = v.get("stance", "")
                confidence = v.get("confidence", "")
                stance_emoji = {"positive": "📈", "neutral": "➡️", "negative": "📉"}.get(stance, "❓")
                matrix_lines.append(f"  {stance_emoji} [{source}] {view} ({confidence})")

        matrix_text = "\n".join(matrix_lines)

        elapsed = time.perf_counter() - t0
        logger.info(f"   📊 分析完成 ({elapsed:.1f}s): {len(extractions)} 来源 → {len(themes)} 主题")

        return {
            "overall_summary": overall_summary,
            "themes": themes,
            "num_sources": len(docs),
            "source_list": [
                f"{d['source']} {d['company']} ({d['doc_type']}, {d['year']})"
                for d in docs
            ],
            "matrix_text": matrix_text,
            "_extractions": extractions,
        }
