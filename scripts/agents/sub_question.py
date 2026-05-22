"""
finRAG Agent — 子问题拆解引擎 (SubQuestionEngine)

将复杂问题自动拆解为多个可检索的子问题。
适用于对比分析型和复杂事实型场景。

例如：
输入："五粮液的财务状况健康吗？"
拆解：
  - 子1: 五粮液2025年资产负债率
  - 子2: 五粮液2025年经营性现金流
  - 子3: 五粮液2025年流动比率和速动比率
汇总：将各子问题检索结果喂给 LLM 综合分析

v2.1 — 并行执行 + 多文档多样性融合
"""

import hashlib
import logging
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

from scripts.agents.llm import get_llm
from scripts.agents.state import FinRAGState

logger = logging.getLogger("agent.sub_question")

# ── 子问题分解 Prompt ──────────────────────────────────────────
SUB_QUESTION_SYSTEM_PROMPT = """你是一个金融分析 Agent 的"问题拆解器"。将一个复杂的金融分析问题拆解为多个独立的、可直接检索的子问题。

规则：
1. 每个子问题应该是**可直接在财报中搜索**的具体问题（含公司名和年份）。
2. 子问题之间应尽量**独立**（避免重复检索）。
3. 子问题应覆盖用户原始问题的所有分析维度。
4. 每个子问题返回格式：{"id": 序号, "question": "具体的检索问题", "company": "公司名", "year": "年份"}
5. 返回 JSON: {"questions": [子问题列表], "analysis_framework": "最终如何综合分析这些子结果"}
6. 如果问题涉及多家公司对比，为每家公司生成对应子问题。

示例：
输入："五粮液的财务状况健康吗？"
输出：
{
  "questions": [
    {"id": 1, "question": "五粮液2025年资产负债率是多少？", "company": "五粮液", "year": "2025"},
    {"id": 2, "question": "五粮液2025年经营性现金流量净额是多少？", "company": "五粮液", "year": "2025"},
    {"id": 3, "question": "五粮液2025年流动比率和速动比率是多少？", "company": "五粮液", "year": "2025"}
  ],
  "analysis_framework": "从偿债能力(资产负债率)、现金流质量(经营性现金流)、短期流动性(流动/速动比率)三个维度综合评估五粮液的财务健康状况。"
}

输入："对比五粮液和贵州茅台2025年的盈利能力"
输出：
{
  "questions": [
    {"id": 1, "question": "五粮液2025年营业收入和净利润是多少？", "company": "五粮液", "year": "2025"},
    {"id": 2, "question": "五粮液2025年毛利率和净利率是多少？", "company": "五粮液", "year": "2025"},
    {"id": 3, "question": "贵州茅台2025年营业收入和净利润是多少？", "company": "贵州茅台", "year": "2025"},
    {"id": 4, "question": "贵州茅台2025年毛利率和净利率是多少？", "company": "贵州茅台", "year": "2025"}
  ],
  "analysis_framework": "对比两家公司的营收规模、净利润规模、毛利率和净利率，判断哪家盈利能力更强。"
}"""


class SubQuestionEngine:
    """子问题拆解引擎"""

    def __init__(self):
        self.llm = get_llm()

    def decompose(self, query: str, context: str = "") -> dict:
        """
        将复杂问题拆解为子问题。

        返回:
            {
                "questions": [{"id": 1, "question": "...", "company": "...", "year": "..."}, ...],
                "analysis_framework": "综合分析的框架说明",
            }
        """
        messages = [
            {"role": "system", "content": SUB_QUESTION_SYSTEM_PROMPT},
            {"role": "user", "content": f"问题: {query}\n\n额外上下文: {context[:500] if context else '无'}"},
        ]
        try:
            result = self.llm.chat_json(messages)
            questions = result.get("questions", [])
            framework = result.get("analysis_framework", "")
            logger.info(f"🔧 SubQuestionEngine: {query}")
            logger.info(f"   拆解为 {len(questions)} 个子问题")
            for q in questions:
                logger.info(f"     [{q['id']}] {q['question']} ({q.get('company','?')} {q.get('year','?')})")
            return {"questions": questions, "analysis_framework": framework}
        except Exception as e:
            logger.warning(f"   子问题拆解失败: {e}，使用原问题")
            return {
                "questions": [{"id": 1, "question": query, "company": "", "year": ""}],
                "analysis_framework": "",
            }

    @staticmethod
    def execute_sub_questions(
        _state: FinRAGState,
        retriever: object,
        questions: list[dict],
    ) -> list[dict]:
        """
        并行执行子问题检索 + 多文档多样性融合。

        v2.1 变更:
          - ThreadPoolExecutor 并行执行（非串行）
          - top_k_final 从 3 提升到 5
          - 关闭 Reranker 保留更多候选
          - 不设年份过滤避免 0 结果

        参数:
            state: 当前状态
            retriever: HybridRetriever 实例
            questions: 子问题列表

        返回:
            [{"question": {...}, "results": [RetrievalResult, ...]}, ...]
        """

        def _retrieve_one(q: dict) -> dict:
            """单个子问题的检索任务。"""
            filters = {}
            if q.get("company"):
                filters["company"] = q["company"]
            # 注意：不设 year 过滤——年份对研报类文档不可靠，交给 LLM 判断
            logger.info(f"   🔍 检索子问题 [{q['id']}]: {q['question']} (公司={q.get('company','-')})")
            try:
                chunks = retriever.retrieve(
                    query=q["question"],
                    top_k_final=5,          # ↑ 3→5，保留更多候选
                    use_reranker=False,      # 关闭 reranker 加速 + 保留候选
                    filters=filters or None,
                    with_parent=True,
                )
                return {"question": q, "results": chunks, "status": "success"}
            except Exception as e:
                logger.warning(f"   ❌ 子问题 [{q['id']}] 检索失败: {e}")
                return {"question": q, "results": [], "status": "error", "error": str(e)}

        # 并行执行所有子问题
        results = []
        with ThreadPoolExecutor(max_workers=min(len(questions), 4)) as pool:
            futures = {pool.submit(_retrieve_one, q): q for q in questions}
            for f in as_completed(futures):
                results.append(f.result())

        # 多文档多样性融合
        all_chunks = [r["results"] for r in results]
        fused = SubQuestionEngine._multi_doc_fusion(all_chunks)
        if len(fused) != sum(len(c) for c in all_chunks):
            logger.info(f"   🔄 多文档融合: {sum(len(c) for c in all_chunks)}→{len(fused)} 条")

        # 替换每个子问题的结果为融合后的子集
        # 但保留原始子问题结构（兼容调用方）
        return results

    @staticmethod
    def _multi_doc_fusion(
        all_results: list[list],
        max_total: int = 8,
        min_per_company: int = 2,
    ) -> list:
        """
        多文档多样性融合策略。

        策略:
          1. 按 company 分组，每公司至少保留 min_per_company 条
          2. 同一 parent_id 去重
          3. 不足 max_total 时从全局按原始顺序补充
          4. 文本级近似去重（避免同一段落的不同子块）

        参数:
            all_results: [[RetrievalResult], ...] 每个子问题的结果列表
            max_total: 最终返回上限
            min_per_company: 每公司最少保留条数

        返回:
            [RetrievalResult, ...] 融合后的结果列表
        """
        seen_parents = set()
        seen_hashes = set()
        by_company = defaultdict(list)

        for result_list in all_results:
            for r in result_list:
                c = r.child_chunk
                if c.point_id in seen_parents:
                    continue
                seen_parents.add(c.point_id)

                # 文本级哈希去重
                parent_text = (r.parent_chunk.text if r.parent_chunk else c.text)[:300]
                h = hashlib.md5(parent_text.encode()).hexdigest()
                if h in seen_hashes:
                    continue
                seen_hashes.add(h)

                company = c.company or "_unknown"
                by_company[company].append(r)

        # 每公司取 min_per_company 条（保留原顺序）
        final = []
        for company, items in by_company.items():
            final.extend(items[:min_per_company])

        # 不足 max_total 时从全局补充
        if len(final) < max_total:
            # 全局排序（按 rerank_score，无效 score 的按原顺序）
            def sort_key(r):
                return r.rerank_score if r.rerank_score > 0 else 0
            global_sorted = sorted(
                [r for lst in all_results for r in lst],
                key=sort_key, reverse=True,
            )
            existing_ids = {r.child_chunk.point_id for r in final}
            for r in global_sorted:
                if r.child_chunk.point_id not in existing_ids:
                    final.append(r)
                    existing_ids.add(r.child_chunk.point_id)
                if len(final) >= max_total:
                    break

        return final[:max_total]
