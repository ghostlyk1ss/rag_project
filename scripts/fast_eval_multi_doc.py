#!/usr/bin/env python3
"""
快速验证多文档检索优化 — 仅跑低分题 + 跨文档题
"""
import json, logging, os, sys, time, math
from pathlib import Path
from dotenv import load_dotenv
load_dotenv()
BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR))
logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")
logger = logging.getLogger("fast_eval")

from backend.services.rag_engine import rag_query

# ── 目标题目（低分题 + 跨文档题） ─────────────────────────────
TEST_QUESTIONS = [
    # 券商研报低分题
    {"id": "Q_corp1", "question": "徐工机械(000425)2025年海外收入是多少？同比增长多少？占总收入比例约多少？"},
    {"id": "Q_corp2", "question": "皖仪科技(688600)2025年归母净利润变化情况如何？"},
    {"id": "Q_corp3", "question": "同享科技(920167)2026年Q1归母净利润环比和同比变化如何？"},
    {"id": "Q_corp4", "question": "新华医疗(600587)2025年海外市场收入增长情况如何？"},
    {"id": "Q_corp5", "question": "远兴能源纯碱业务2025年销量和收入是多少？"},
    # 跨文档/多实体题（应该是优化的重点）
    {"id": "Q_multi1", "question": "徐工机械和皖仪科技2025年归母净利润都实现增长，背后的驱动逻辑有何不同？"},
    {"id": "Q_multi2", "question": "贵州茅台(每股分红27.993元)、招商银行(每股分红2.016元)、中国平安(末期1.75元)中，哪个股息率可能最高？"},
    {"id": "Q_multi3", "question": "从各公司研报看，2025-2026年哪些行业呈现明显的'困境反转'特征？"},
    {"id": "Q_multi4", "question": "招商银行和平安银行2025年的营收和利润表现对比如何？"},
    {"id": "Q_multi5", "question": "对比五粮液和贵州茅台2025年的盈利能力"},
]

results = []
for t in TEST_QUESTIONS:
    logger.info(f"\n{'='*60}")
    logger.info(f"[{t['id']}] {t['question']}")
    logger.info(f"{'='*60}")
    t0 = time.perf_counter()
    try:
        r = rag_query(t["question"], top_k=8, stream=False)
        elapsed = time.perf_counter() - t0
        answer = r.get("answer", "")
        citations = r.get("citations", [])
        contexts = [c["text"] for c in citations]
        # 统计公司来源
        companies = set()
        for c in citations:
            if c.get("company"):
                companies.add(c["company"])
        logger.info(f"  ✅ {elapsed:.1f}s | contexts={len(contexts)} | 公司分布={companies}")
        logger.info(f"  回答前200字: {answer[:200]}")

        results.append({
            "id": t["id"],
            "question": t["question"],
            "answer": answer,
            "contexts": contexts,
            "companies": list(companies),
            "elapsed_sec": round(elapsed, 1),
        })
    except Exception as e:
        elapsed = time.perf_counter() - t0
        logger.error(f"  ❌ {elapsed:.1f}s | 错误: {e}")
        results.append({
            "id": t["id"],
            "question": t["question"],
            "answer": f"[ERROR] {e}",
            "contexts": [],
            "companies": [],
            "elapsed_sec": round(elapsed, 1),
        })

# 保存
out = {
    "meta": {"total": len(results), "date": time.strftime("%Y-%m-%d %H:%M")},
    "results": results,
}
out_path = BASE_DIR / "benchmark" / "fast_eval_results.json"
with open(out_path, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=2)
logger.info(f"\n💾 结果保存: {out_path}")

# 汇总
for r in results:
    companies = r.get("companies", [])
    ctx = len(r["contexts"])
    emoji = "✅" if len(r["answer"]) > 50 else "⚠️"
    print(f"  {emoji} {r['id']}: {r['elapsed_sec']:>5.1f}s | {ctx} contexts | {companies}")
