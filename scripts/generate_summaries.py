"""
finRAG 摘要预处理 — 对券商研报/年报/信用评级做观点摘要
一次性离线计算，结果写入 data/parsed/_ai_summaries.md
然后由 index_summaries.py 写入向量库

用法: python scripts/generate_summaries.py
"""

import json, logging, re, time, sys, os
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")
logger = logging.getLogger("generate_summaries")

# ── Configuration ────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parents[1]
PARSED_DIR = BASE_DIR / "data" / "parsed"
SUMMARIES_FILE = PARSED_DIR / "_ai_summaries.md"
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_MODEL = "deepseek-chat"
DEEPSEEK_BASE = "https://api.deepseek.com"

# ── Which docs need what type of summary ─────────────────────────
def classify_doc(filename: str) -> str | None:
    """归类文档类型，None=不需要摘要"""
    # 排除非报告类文档
    skip_patterns = [
        "上海证券交易所", "交易规则", "易方达", "ETF",
        "问询函", "监管函", "法律意见书", "股权激励",
        "受托管理", "kezhuanzhai",
        "资产评估报告", "审计报告",
        "招股说明书", "zhaogu_", "重组报告", "chongzu_",
        "配股说明书", "peigu_", "政策", "policy_",
        "_ai_summaries",  # 防止循环
    ]
    for p in skip_patterns:
        if p in filename:
            return None

    # 券商研报（含"证券"的文档）
    if "证券" in filename and not any(k in filename for k in ["上海证券交易所", "易方达"]):
        return "research_report"
    
    # 信用评级
    if "信用" in filename or "credit_rating" in filename:
        return "credit_rating"
    
    # 年报（只选主年报，不摘要报）
    main_annuals = ["maotai_2025", "wuliangye_2025", "pingan_2025"]
    for m in main_annuals:
        if m in filename:
            return "annual_report"
    
    # 招商银行主年报
    if "招商银行" in filename and "2025年度报告" in filename and "摘要" not in filename:
        return "annual_report"
    
    # 央行报告
    if "PBOC" in filename or "Monetary" in filename:
        return "central_bank"
    
    return None


# ── LLM extraction prompts ──────────────────────────────────────
RESEARCH_PROMPT = """你是一个金融文档分析助手。分析以下券商研究报告，提取4个维度的摘要信息。

输出格式（JSON）：
{
  "investment_theme": "核心投资观点/主题（1-2句话）",
  "financial_highlights": "财务数据亮点（收入/利润/增长率，提取具体数值）",
  "industry_judgment": "行业判断/展望（如果有，1-2句话）",
  "overseas_info": "海外业务信息（如果有海外收入/扩张等，否则空字符串）"
}

报告内容：
{text}"""

RATING_PROMPT = """你是一个信用评级分析助手。分析以下信用评级报告，提取关键摘要。

输出格式（JSON）：
{
  "issuer": "发行主体",
  "rating": "评级结论（主体评级+债项评级+展望）",
  "key_financials": "关键财务指标（资产/收入/利润/负债率等，提取具体数值）",
  "key_strengths": "主要信用优势（1-2句话）",
  "key_concerns": "主要关注事项（1-2句话，如果没有则空字符串）"
}

报告内容：
{text}"""

ANNUAL_PROMPT = """你是一个财务分析助手。分析以下年度报告的关键财务指标摘要。

输出格式（JSON）：
{
  "revenue": "营业收入（提取具体数值和同比变化）",
  "net_profit": "归母净利润（提取具体数值和同比变化）",
  "dividend": "分红信息（每股分红金额，如果有）",
  "key_metrics": "其他关键指标（ROE、毛利率、每股收益等，提取具体数值）",
  "business_summary": "业务概况（1-2句话）"
}

报告内容（前5000字）：
{text}"""

CB_PROMPT = """你是一个宏观经济分析助手。分析以下央行货币政策报告，提取关键摘要。

输出格式（JSON）：
{
  "gdp_growth": "GDP增长数据（提取具体百分比）",
  "policy_stance": "货币政策基调（1句话）",
  "key_tools": "主要货币政策工具",
  "key_judgments": "核心判断/展望（1-2句话）"
}

报告内容（前4000字）：
{text}"""


# ── LLM call ─────────────────────────────────────────────────────
def call_llm(prompt: str, system_prompt: str = "你是一个专业的金融文档分析助手。请严格按照要求的JSON格式输出，不要包含markdown代码块标记。") -> dict:
    import urllib.request, urllib.error
    import ssl
    
    payload = json.dumps({
        "model": DEEPSEEK_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.1,
        "max_tokens": 800,
    }).encode()
    
    req = urllib.request.Request(
        f"{DEEPSEEK_BASE}/v1/chat/completions",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        },
    )
    ctx = ssl.create_default_context()
    for attempt in range(3):
        try:
            resp = urllib.request.urlopen(req, context=ctx, timeout=60)
            data = json.loads(resp.read())
            content = data["choices"][0]["message"]["content"]
            # Strip markdown code fences if present
            content = re.sub(r'^```(?:json)?\s*\n?', '', content.strip())
            content = re.sub(r'\n?```\s*$', '', content)
            return json.loads(content)
        except (json.JSONDecodeError, KeyError, urllib.error.URLError) as e:
            logger.warning(f"  LLM调用失败(尝试{attempt+1}): {e}")
            if attempt < 2:
                time.sleep(3)
    return {}


# ── Main ─────────────────────────────────────────────────────────
def main():
    md_files = sorted(PARSED_DIR.glob("*.md"))
    logger.info(f"共 {len(md_files)} 个待检查文件")
    
    summaries = []
    total_cost = 0
    
    for i, md_path in enumerate(md_files, 1):
        doc_type = classify_doc(md_path.name)
        if doc_type is None:
            logger.info(f"[{i:3d}/{len(md_files)}] ⏭️  {md_path.name} (跳过)")
            continue
        
        text = md_path.read_text(encoding="utf-8")
        # Limit text length to avoid excessive token usage
        text_sample = text[:5000]
        
        logger.info(f"[{i:3d}/{len(md_files)}] 🔄 {md_path.name} ({doc_type})")
        
        prompt_map = {
            "research_report": (RESEARCH_PROMPT, "research"),
            "credit_rating": (RATING_PROMPT, "rating"),
            "annual_report": (ANNUAL_PROMPT, "annual"),
            "central_bank": (CB_PROMPT, "central_bank"),
        }
        prompt_template, summary_type = prompt_map[doc_type]
        
        result = call_llm(prompt_template.replace("{text}", text_sample))
        
        if result:
            summary_text = json.dumps(result, ensure_ascii=False)
            total_cost += 1
            
            # Build natural language summary
            nl_parts = []
            if doc_type == "research_report":
                theme = result.get("investment_theme", "")
                fin = result.get("financial_highlights", "")
                industry = result.get("industry_judgment", "")
                overseas = result.get("overseas_info", "")
                if theme:
                    nl_parts.append(f"核心观点：{theme}")
                if fin:
                    nl_parts.append(f"财务数据：{fin}")
                if industry:
                    nl_parts.append(f"行业判断：{industry}")
                if overseas:
                    nl_parts.append(f"海外业务：{overseas}")
            
            elif doc_type == "credit_rating":
                for k, v in result.items():
                    if v:
                        nl_parts.append(f"{k}：{v}")
            
            elif doc_type == "annual_report":
                for k, v in result.items():
                    if v:
                        nl_parts.append(f"{k}：{v}")
            
            elif doc_type == "central_bank":
                for k, v in result.items():
                    if v:
                        nl_parts.append(f"{k}：{v}")
            
            nl_text = "\n".join(nl_parts)
            
            summaries.append({
                "source_file": md_path.stem,
                "doc_type": doc_type,
                "summary_type": summary_type,
                "natural_language": nl_text,
                "raw_json": summary_text,
            })
            logger.info(f"       ✅ {len(nl_parts)} 个字段")
        else:
            logger.warning(f"       ❌ 提取失败")
        
        # Rate limiting
        time.sleep(1)
    
    # Write to file
    if summaries:
        md_lines = ["# AI-Generated Report Summaries\n"]
        md_lines.append(f"_生成日期: {time.strftime('%Y-%m-%d %H:%M')}_\n")
        md_lines.append(f"_来源: {len(summaries)} 份文档_\n\n")
        md_lines.append("---\n\n")
        
        for s in summaries:
            md_lines.append(f"## {s['source_file']}\n")
            md_lines.append(f"- doc_type: {s['doc_type']}\n")
            md_lines.append(f"- summary_type: {s['summary_type']}\n\n")
            md_lines.append(s["natural_language"])
            md_lines.append("\n\n")
            md_lines.append(f"<!-- raw_json: {s['raw_json']} -->\n\n")
            md_lines.append("---\n\n")
        
        SUMMARIES_FILE.write_text("".join(md_lines), encoding="utf-8")
        logger.info(f"\n✅ 已保存: {SUMMARIES_FILE}")
        logger.info(f"   共 {len(summaries)} 份摘要 ({total_cost} 次LLM调用)")
    else:
        logger.warning("⚠️ 没有生成任何摘要")


if __name__ == "__main__":
    main()
