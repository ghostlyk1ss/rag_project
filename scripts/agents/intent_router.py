"""
finRAG Agent — 意图识别与路由 (IntentRouter)

将用户问题分为三类：
- factual: 事实检索型 — 直接走 Hybrid RAG
- comparative: 对比分析型 — 拆解子问题分别检索再汇总
- computational: 数值计算型 — 检索数据后用 CodeInterpreter

用法:
    from scripts.agents.intent_router import IntentRouter
    router = IntentRouter()
    intent = router.route("五粮液2025年营收是多少？")
    # → {"intent": "factual", "confidence": 0.95, ...}
"""

import logging
import re
from typing import Optional

from scripts.agents.llm import get_llm

logger = logging.getLogger("agent.intent_router")

# ── 意图分类 Prompt ────────────────────────────────────────────
INTENT_SYSTEM_PROMPT = """你是一个金融问答系统的"意图分类器"。分析用户的问题，将其归为以下三类之一：

1. factual (事实检索型):
   - 直接查找某个数值、事实、陈述
   - 问题中含有：多少、怎么样、是什么、是否有、列出
   - 例如："五粮液2025年营收是多少？"、"贵州茅台的分红率是多少？"

2. comparative (对比分析型):
   - 对比两个或多个实体/公司的指标
   - 问题中含有：对比、比较、哪个更好、差异、区别、vs、和...相比
   - 例如："对比五粮液和贵州茅台的盈利能力"、"哪家ROE更高？"

3. computational (数值计算型):
   - 需要做数学计算、增长率、占比等
| 问题中含有：复合增长、年均增长、占比、比例、计算、同比、环比、增长率、增速、差额、合计、年化
|  - 例如："五粮液过去三年营收的复合增长率"、"毛利率变化趋势"、"2025年营收同比增速"

4. summarization (摘要总结型):
   - 需要对多篇文档/多个 section 进行归纳总结
   - 问题中含有：总结、概括、归纳、综述、要点、核心观点、主要发现、摘要、概述、主题
   - 例如："总结五粮液2025年报的核心内容"、"概括券商对天齐锂业的主要观点"
   - 注意：如果问题同时可被 factual 覆盖，优先 factual 除非明确要求"总结"

返回 JSON: {"intent": "factual|comparative|computational|summarization|theme_analysis", "confidence": 0.95, "reason": "简短理由"}

注意：
- 如果有重叠，选最主导的意图
- 不确定时优先选 factual
- confidence 0.0-1.0
- theme_analysis 只在你检测到用户明确要求跨文档/多机构观点分析时才返回"""


class IntentRouter:
    """意图路由器"""

    def __init__(self):
        self.llm = get_llm()

    def _quick_rule_based(self, query: str) -> Optional[str]:
        """快速规则匹配，避免不必要的 LLM 调用。"""
        q = query.lower()

        # 对比分析关键词（优先级最高）
        comparative_patterns = [
            r"对比", r"比较", r"vs", r"与.*?相比", r"哪个.*(?:好|高|强|优秀)",
            r"差异", r"区别", r"和.*的.*分别", r"两者",
        ]
        for pat in comparative_patterns:
            if re.search(pat, q):
                return "comparative"

        # 数值计算关键词
        computational_patterns = [
            r"复合增长", r"年均增长", r"增长率", r"占比", r"比例",
            r"计算", r"平均", r"同比", r"环比", r"增速",
            r"变化.*趋势", r"预测", r"预估", r"年化",
            r"差额", r"合计", r"总和", r"加权",
            r"比较.*数值", r"增长.*多少", r"减少.*多少",
        ]
        for pat in computational_patterns:
            if re.search(pat, q):
                return "computational"

        # 多实体检测（非对比句式的多公司问题 → 用独立检索）
        if self._is_multi_entity(query):
            return "multi_entity"

        # 观点分析关键词
        theme_patterns = [
            r"观点分析", r"主题分析", r"各机构", r"各券商",
            r"多方观点", r"分歧", r"共识",
            r".*对.*的.*看法", r".*对.*的.*判断",
            r"行业判断", r"怎么看.*行业", r"怎么看.*板块",
        ]
        for pat in theme_patterns:
            if re.search(pat, q):
                return "theme_analysis"

        # 摘要总结关键词
        summarization_patterns = [
            r"总结", r"概括", r"归纳", r"综述", r"要点",
            r"核心观点", r"主要发现", r"摘要", r"概述",
            r"主题", r"主要.*内容", r"核心.*内容",
        ]
        for pat in summarization_patterns:
            if re.search(pat, q):
                return "summarization"

        # 事实检索（大部分问题）
        factual_patterns = [
            r"多少", r"怎么", r"什么", r"如何", r"是否", r"列举",
            r"列出", r"有没有", r"哪些", r"谁", r"何时",
        ]
        for pat in factual_patterns:
            if re.search(pat, q):
                return "factual"

        return None

    def route(self, query: str, context: str = "") -> dict:
        """
        识别意图。

        参数:
            query: 用户问题
            context: 可选的上下文信息

        返回:
            {"intent": "factual|comparative|computational|summarization|theme_analysis", "confidence": 0.95, "reason": "...", "entities": [...]}
        """
        # 先规则匹配（快，不花 token）
        rule_intent = self._quick_rule_based(query)
        if rule_intent:
            logger.info(f"🔍 IntentRouter: \"{query}\" → {rule_intent} (规则匹配)")
            return {
                "intent": rule_intent,
                "confidence": 0.8,
                "reason": f"规则匹配：检测到关键词",
                "entities": self._extract_entities(query),
            }

        # LLM 分类（更准确）
        messages = [
            {"role": "system", "content": INTENT_SYSTEM_PROMPT},
            {"role": "user", "content": f"问题: {query}\n\n上下文: {context[:500] if context else '无'}"},
        ]
        try:
            result = self.llm.chat_json(messages)
            intent = result.get("intent", "factual")
            confidence = float(result.get("confidence", 0.5))
            reason = result.get("reason", "")
            logger.info(f"🔍 IntentRouter: \"{query}\" → {intent} (LLM, {confidence:.2f})")
            return {
                "intent": intent,
                "confidence": confidence,
                "reason": reason,
                "entities": self._extract_entities(query),
            }
        except Exception as e:
            logger.warning(f"  意图分类失败: {e}，默认 factual")
            return {
                "intent": "factual",
                "confidence": 0.5,
                "reason": "LLM 分类失败，回退到 factual",
                "entities": self._extract_entities(query),
            }

    def _extract_entities(self, query: str) -> list[str]:
        """从查询中提取可能的公司/实体名。"""
        found = set()

        # 1. 从 KB 提取已知公司
        try:
            from scripts.kb_meta import get_kb_index
            kb = get_kb_index()
            for stock in kb.all_stocks:
                if isinstance(stock, str) and len(stock) >= 2 and stock in query:
                    found.add(stock)
            # 别名匹配
            for standard, aliases in kb._company_aliases.items():
                for alias in aliases:
                    if alias != standard and alias in query:
                        found.add(standard)
        except Exception:
            pass

        # 2. 兜底：硬编码常见公司（无论 KB 是否可用都检查）
        known_companies = ["五粮液", "贵州茅台", "宁德时代", "比亚迪", "腾讯", "阿里巴巴",
                          "泸州老窖", "洋河股份", "山西汾酒", "伊利股份",
                          "招商银行", "平安银行", "中国平安", "中芯国际", "徐工机械",
                          "皖仪科技", "中矿资源", "天齐锂业", "福斯特", "同享科技",
                          "新华医疗", "裕同科技", "中科创达", "甘李药业", "海螺水泥",
                          "牧原股份", "安图生物", "翔宇医疗", "奥浦迈", "赛恩斯",
                          "潮宏基", "新泉股份", "远兴能源", "中金公司", "蘅东光",
                          "北方长龙", "广东鸿特", "陕西能源", "海尔智家", "联讯仪器",
                          "长裕集团"]
        for name in known_companies:
            if name in query:
                found.add(name)

        return list(found)

    def _is_multi_entity(self, query: str) -> bool:
        """检测查询是否涉及多个实体/公司。"""
        entities = self._extract_entities(query)
        # 过滤空字符串和无效实体
        valid = [e for e in entities if e and len(e) >= 2]
        return len(valid) >= 2
