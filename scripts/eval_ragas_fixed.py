#!/usr/bin/env python3
"""
finRAG RAGAS 评估（修复版）
==========================
绕过 ragas.evaluate() 的 NaN bug，直接调用 metric._single_turn_ascore
"""
import asyncio
import json
import logging
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR))
logger = logging.getLogger("eval_ragas_fixed")
logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")

# ── 配置 ──────────────────────────────────────────────────────────
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
DEEPSEEK_BASE_URL = "https://api.deepseek.com"

if not DEEPSEEK_API_KEY:
    logger.error("DEEPSEEK_API_KEY 未设置")
    sys.exit(1)

# ── 加载原始结果 ──────────────────────────────────────────────────
raw_path = BASE_DIR / "benchmark" / "ragas_raw.json"
with open(raw_path) as f:
    raw = json.load(f)
results = raw["results"]
logger.info(f"📋 加载原始结果: {len(results)} 题")


async def score_all():
    from openai import OpenAI
    from ragas.llms import llm_factory
    from ragas.dataset_schema import SingleTurnSample
    from ragas.metrics._faithfulness import Faithfulness
    from ragas.metrics._context_recall import ContextRecall
    from ragas.run_config import RunConfig

    client = OpenAI(
        api_key=DEEPSEEK_API_KEY,
        base_url=DEEPSEEK_BASE_URL,
    )
    llm = llm_factory("deepseek-chat", client=client)
    rc = RunConfig(timeout=60)

    f_metric = Faithfulness()
    cr_metric = ContextRecall()
    f_metric.llm = llm
    cr_metric.llm = llm
    f_metric.init(rc)
    cr_metric.init(rc)

    per_question = []
    total_f, total_cr = 0.0, 0.0
    count_f, count_cr = 0, 0

    t0 = time.perf_counter()
    for i, r in enumerate(results):
        ctx = r["contexts"] if r["contexts"] else [""]
        sample = SingleTurnSample(
            user_input=r["question"],
            response=r["answer"],
            retrieved_contexts=ctx,
            reference=r["ground_truth"] or r["question"],
        )
        try:
            f_score = await f_metric._single_turn_ascore(sample, [])
        except Exception as e:
            f_score = None
            logger.warning(f"   [{i+1}] faithfulness 失败: {e}")

        try:
            cr_score = await cr_metric._single_turn_ascore(sample, [])
        except Exception as e:
            cr_score = None
            logger.warning(f"   [{i+1}] context_recall 失败: {e}")

        import math
        f_val = float(f_score) if f_score is not None and not (isinstance(f_score, float) and math.isnan(f_score)) else None
        cr_val = float(cr_score) if cr_score is not None and not (isinstance(cr_score, float) and math.isnan(cr_score)) else None

        per_question.append({
            "id": r.get("id", f"Q{i+1:03d}"),
            "question": r["question"][:60],
            "difficulty": r.get("difficulty", ""),
            "doc_type": r.get("doc_type", ""),
            "elapsed_sec": r.get("elapsed_sec", 0),
            "faithfulness": f_val,
            "context_recall": cr_val,
        })

        if f_val is not None:
            total_f += f_val
            count_f += 1
        if cr_val is not None:
            total_cr += cr_val
            count_cr += 1

        if (i + 1) % 10 == 0:
            elapsed = time.perf_counter() - t0
            logger.info(f"   [{i+1}/{len(results)}] 已用 {elapsed:.0f}s")

    elapsed_total = time.perf_counter() - t0

    aggregate = {}
    if count_f:
        aggregate["faithfulness"] = round(total_f / count_f, 4)
    if count_cr:
        aggregate["context_recall"] = round(total_cr / count_cr, 4)

    output = {
        "meta": {
            "name": "finRAG RAGAS Evaluation Report v3",
            "total": len(results),
            "elapsed_sec": round(elapsed_total, 1),
            "date": time.strftime("%Y-%m-%d %H:%M"),
            "judge_model": "deepseek-chat",
            "metrics": list(aggregate.keys()),
        },
        "aggregate_scores": aggregate,
        "per_question_scores": per_question,
    }

    out_path = BASE_DIR / "benchmark" / "ragas_results_v3.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    logger.info(f"💾 结果保存: {out_path}")
    logger.info(f"⏱️  总耗时: {elapsed_total:.0f}s")
    logger.info(f"📊 汇总: {json.dumps(aggregate, ensure_ascii=False)}")

    # 打印按 doc_type 和 difficulty 的分组
    from collections import defaultdict
    by_type = defaultdict(list)
    by_diff = defaultdict(list)
    for q in per_question:
        if q["faithfulness"] is not None:
            by_type[q["doc_type"]].append(q["faithfulness"])
            by_diff[q["difficulty"]].append(q["faithfulness"])

    logger.info("\n📊 按文档类型 (faithfulness):")
    for dt, vals in sorted(by_type.items()):
        logger.info(f"   {dt}: {sum(vals)/len(vals):.4f} ({len(vals)}题)")

    logger.info("\n📊 按难度 (faithfulness):")
    for d, vals in sorted(by_diff.items()):
        logger.info(f"   {d}: {sum(vals)/len(vals):.4f} ({len(vals)}题)")


if __name__ == "__main__":
    asyncio.run(score_all())
