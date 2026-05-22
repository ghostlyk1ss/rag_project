"""
finRAG Agent — 查询转换层 (QueryRewriter)

功能:
1. 指代消解：补全代词/省略 -> 完整查询
2. 多路查询生成：拆成 3 个维度
3. HyDE：生成假设文档用于检索增强

用法:
    from scripts.agents.query_rewriter import QueryRewriter
    rewriter = QueryRewriter()
    result = rewriter.rewrite(query, history)
"""

import json
import logging
from typing import Optional

from scripts.agents.llm import get_llm

logger = logging.getLogger("agent.query_rewriter")

# ══════════════════════════════════════════════════════════════════════
#  金融同义词扩展（快速词典匹配，无需 LLM 调用）
# ══════════════════════════════════════════════════════════════════════

# ── 金融同义词扩展（快速词典匹配，无需 LLM 调用） ──────────────
# 用于 rag_engine.py 中查询扩展
FINANCIAL_SYNONYMS = {
    "营收": ["营业收入", "营业总收入", "收入"],
    "收入": ["营业收入", "营业总收入", "营收"],
    "赚": ["净利润", "利润", "归母净利润"],
    "净利": ["净利润", "归母净利润", "归属于上市公司股东的净利润"],
    "利润": ["净利润", "利润总额", "营业利润"],
    "增长": ["增加", "上升", "提高"],
    "下降": ["减少", "降低", "下滑"],
    "毛利率": ["营业毛利率", "毛利"],
    "ROE": ["净资产收益率", "加权平均净资产收益率"],
    "每股收益": ["基本每股收益", "EPS", "每股盈利"],
    "分红": ["现金分红", "股利", "利润分配"],
    "总资产": ["资产总计", "资产总额"],
    "总负债": ["负债合计", "负债总额"],
}


def expand_financial_query(query: str) -> list[str]:
    """基于词典的金融查询扩展。由 rag_engine.py 调用。"""
    expanded = [query]
    for term, synonyms in FINANCIAL_SYNONYMS.items():
        if term in query:
            for syn in synonyms[:2]:
                new_q = query.replace(term, syn)
                if new_q != query and new_q not in expanded:
                    expanded.append(new_q)
    return expanded[:3]


# ── 指代消解 Prompt ────────────────────────────────────────────
COREFERENCE_SYSTEM_PROMPT = """你是一个金融文档问答助手的"查询补全器"。你的任务是将用户的当前提问补全为完整、独立的问题。

规则：
1. 如果用户的问题本身就是完整的，直接原样返回。
2. 如果用户说了"它"、"其"、"这个"、"那个"、"该公司"、"它的营收"等代词，根据对话历史补全。
3. 如果用户省略了年份或公司名，根据上下文自动填补。
4. 如果有多个可能的指代对象，选择对话中最近提到的那个。
5. 返回 JSON: {"query": "补全后的问题", "company": "识别出公司名(未知就空字符串)", "year": "识别出年份(未知就空字符串)"}

示例：
用户: 五粮液2025年营收是多少？
助手: 回答...
用户: 它的净利润呢？
→ {"query": "五粮液2025年净利润是多少？", "company": "五粮液", "year": "2025"}

用户: 贵州茅台2024年ROE是多少？
助手: 回答...
用户: 那2023年呢？
→ {"query": "贵州茅台2023年ROE是多少？", "company": "贵州茅台", "year": "2023"}

用户: 对比五粮液和贵州茅台的盈利能力
助手: 回答...
用户: 它们的毛利率分别是多少？
→ {"query": "对比五粮液和贵州茅台2025年的毛利率分别是多少？", "company": "", "year": "2025"}

用户: 中邮证券对天齐锂业一季度业绩怎么看？
→ {"query": "中邮证券天齐锂业一季度业绩分析", "company": "天齐锂业", "year": "2026"}

用户: 华源证券怎么看新产业的增长？
→ {"query": "华源证券新产业增长分析", "company": "新产业", "year": ""}

用户: 央行最新的货币政策报告说了什么？
→ {"query": "中国人民银行货币政策执行报告最新内容要点", "company": "", "year": ""}"""

# ── 多路查询生成 Prompt ────────────────────────────────────────
MULTI_QUERY_SYSTEM_PROMPT = """你是金融 RAG 系统的"多路查询生成器"。将用户的查询拆解为 3 个互补的子查询，从不同维度提升检索召回率。

三路维度：
1. 关键词维度 (keyword)：保留原文中的核心数字、年份、公司名、财务指标名。适合 BM25 搜精确匹配。
2. 语义维度 (semantic)：用同义改写和扩展描述，适合向量检索搜语义相似。
3. 财务指标维度 (metric)：提取明确的财务指标名，格式化为标准的财务分析术语，适合精准匹配。

返回 JSON: {"queries": [{"type": "keyword", "query": "..."}, {"type": "semantic", "query": "..."}, {"type": "metric", "query": "..."}]}

示例：
输入："五粮液2025年毛利率"
输出：
{
  "queries": [
    {"type": "keyword", "query": "五粮液 2025 毛利率"},
    {"type": "semantic", "query": "宜宾五粮液股份有限公司2025年的营业毛利润占比情况"},
    {"type": "metric", "query": "五粮液 2025 毛利率 营业成本 营业收入 毛利"}
  ]
}

输入："对比五粮液和贵州茅台2025年ROE"
输出：
{
  "queries": [
    {"type": "keyword", "query": "五粮液 贵州茅台 2025 ROE 净资产收益率"},
    {"type": "semantic", "query": "五粮液和贵州茅台2025年的净资产收益率对比分析"},
    {"type": "metric", "query": "五粮液 贵州茅台 2025 净资产收益率 净利润 净资产 ROE"}
  ]
}"""

# ── HyDE Prompt ────────────────────────────────────────────────
HYDE_SYSTEM_PROMPT = """你是一个金融分析助手。给你一个问题，请生成一段"假设的财报摘要"作为回答。

这个摘要不需要完全准确，关键是包含该问题可能在财报中出现的相关段落风格和关键词。
这样可以帮助向量检索更准确地找到原文。

要求：
1. 使用专业的财报语言风格
2. 包含相关财务指标的中文名称和数字占位符
3. 虽然数据不真实，但语气和格式要像真实的财报段落
4. 长度在 100-300 字之间

返回纯文本（不要 JSON）。"""


class QueryRewriter:
    """查询转换器：指代消解 + 多路查询 + HyDE"""

    def __init__(self):
        self.llm = get_llm()

    def resolve_coreference(
        self, query: str, history: list[dict]
    ) -> tuple[str, str, str]:
        """
        指代消解。

        参数:
            query: 用户当前提问
            history: 对话历史 [{"role": ..., "content": ...}]

        返回:
            (补全后的查询, 公司名, 年份)
        """
        if not history:
            # 没有历史，尝试从问题中提取
            import re
            company = ""
            year = ""
            m = re.search(r"(20[0-9]{2})", query)
            if m:
                year = m.group(1)
            # 常见公司名
            for name in ["五粮液", "贵州茅台", "宁德时代", "比亚迪", "腾讯", "阿里巴巴"]:
                if name in query:
                    company = name
                    break
            return query, company, year

        # 有历史，用 LLM 做指代消解
        history_str = "\n".join(
            f"{'用户' if m['role'] == 'user' else '助手'}: {m['content'][:500]}"
            for m in history[-6:]  # 取最近 6 条
        )

        messages = [
            {"role": "system", "content": COREFERENCE_SYSTEM_PROMPT},
            {"role": "user", "content": f"对话历史:\n{history_str}\n\n当前提问: {query}"},
        ]
        try:
            result = self.llm.chat_json(messages)
            resolved = result.get("query", query)
            company = result.get("company", "")
            year = result.get("year", "")
            logger.info(f"  指代消解: \"{query}\" → \"{resolved}\" [{company}|{year}]")
            return resolved, company, year
        except Exception as e:
            logger.warning(f"  指代消解失败: {e}，使用原查询")
            return query, "", ""

    def generate_multi_queries(self, query: str) -> list[dict]:
        """
        多路查询生成。

        返回:
            [{"type": "keyword", "query": "..."}, ...]
        """
        messages = [
            {"role": "system", "content": MULTI_QUERY_SYSTEM_PROMPT},
            {"role": "user", "content": query},
        ]
        try:
            result = self.llm.chat_json(messages)
            queries = result.get("queries", [])
            if len(queries) < 3:
                # 降级
                queries = [
                    {"type": "keyword", "query": query},
                    {"type": "semantic", "query": query},
                    {"type": "metric", "query": query},
                ]
            logger.info(f"  多路查询: {[q['type'] for q in queries]}")
            return queries
        except Exception as e:
            logger.warning(f"  多路查询生成失败: {e}，使用原查询")
            return [{"type": "original", "query": query}]

    def generate_hyde(self, query: str) -> str:
        """
        生成 HyDE 假设文档。

        返回:
            假设文档文本
        """
        messages = [
            {"role": "system", "content": HYDE_SYSTEM_PROMPT},
            {"role": "user", "content": query},
        ]
        try:
            doc = self.llm.chat(messages, temperature=0.3)
            logger.info(f"  HyDE 生成 ({len(doc)} 字)")
            return doc
        except Exception as e:
            logger.warning(f"  HyDE 生成失败: {e}")
            return ""

    def rewrite(
        self, query: str, history: list[dict] = None
    ) -> dict:
        """
        完整查询转换流程。

        返回:
            {
                "resolved_query": str,      # 指代消解后的查询
                "company": str,              # 识别出的公司
                "year": str,                 # 识别出的年份
                "multi_queries": list,       # 多路查询
                "hyde_document": str,        # HyDE 文档
            }
        """
        history = history or []
        logger.info(f"🔧 QueryRewriter: \"{query}\"")

        # 1) 指代消解
        resolved, company, year = self.resolve_coreference(query, history)

        # 2) 多路查询
        multi_queries = self.generate_multi_queries(resolved)

        # 3) HyDE
        hyde = self.generate_hyde(resolved)

        result = {
            "resolved_query": resolved,
            "company": company,
            "year": year,
            "multi_queries": multi_queries,
            "hyde_document": hyde,
        }
        return result
