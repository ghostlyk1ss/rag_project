#!/usr/bin/env python3
"""
finRAG Ragas 自动化评估
========================
用法: python scripts/eval_ragas.py [--subset N] [--output results.json]

流程:
  1. 加载黄金测试集
  2. 对每题运行 RAG pipeline → 获取 answer + contexts
  3. 用 DeepSeek 作为 Judge LLM 计算 Ragas 指标
  4. 输出评估报告

指标:
  - faithfulness: 回答是否忠实于检索到的上下文
  - context_recall: 检索到的上下文是否包含正确答案所需信息
  - answer_relevancy: 回答是否与问题相关
  - answer_correctness: 回答与 ground truth 的匹配程度 (需要额外LLM调用)
"""
import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

# 确保项目根目录在 sys.path
BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR))

logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")
logger = logging.getLogger("eval_ragas")

# ── 配置 ──────────────────────────────────────────────────────────
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")

parser = argparse.ArgumentParser(description="finRAG Ragas 评估")
parser.add_argument("--subset", type=int, default=None,
                    help="仅测试前 N 题（省 token）")
parser.add_argument("--output", type=str, default=str(BASE_DIR / "benchmark" / "ragas_results.json"),
                    help="结果输出路径")
parser.add_argument("--metric", type=str, default="faithfulness,context_recall",
                    help="评估指标，逗号分隔（默认: faithfulness,context_recall）")
args = parser.parse_args()

# ── 1. 加载黄金测试集 ──────────────────────────────────────────────
with open(BASE_DIR / "benchmark" / "golden_test_set.json") as f:
    golden = json.load(f)

questions = golden["questions"]
if args.subset:
    questions = questions[:args.subset]

logger.info(f"📋 加载黄金测试集: {len(questions)} 题（总{golden['meta']['total_questions']}题）")

# ── 2. 逐题运行 RAG Pipeline ──────────────────────────────────────
from backend.services.rag_engine import rag_query

results = []
errors = []
t0 = time.perf_counter()

for i, q in enumerate(questions):
    qid = q.get("id", f"Q{i+1:03d}")
    query = q["question"]
    ground_truth = q.get("gold_answer", "")
    
    logger.info(f"[{i+1}/{len(questions)}] {qid}: {query[:50]}...")
    
    try:
        t_start = time.perf_counter()
        rag_result = rag_query(query, top_k=8, stream=False)
        elapsed = time.perf_counter() - t_start
        
        answer = rag_result.get("answer", "")
        # 构建 contexts 列表（每个 citation 的文本）
        contexts = [c["text"] for c in rag_result.get("citations", [])]
        
        results.append({
            "id": qid,
            "question": query,
            "answer": answer,
            "contexts": contexts,
            "ground_truth": ground_truth,
            "reference_contexts": q.get("reference_contexts", []),
            "elapsed_sec": round(elapsed, 1),
            "doc_type": q.get("doc_type", ""),
            "difficulty": q.get("difficulty", ""),
        })
        
        # 进度指示
        logger.info(f"   ✅ {elapsed:.1f}s | contexts={len(contexts)} | answer_len={len(answer)}")
        
    except Exception as e:
        logger.error(f"   ❌ 错误: {e}")
        errors.append({"id": qid, "error": str(e)})
        results.append({
            "id": qid,
            "question": query,
            "answer": f"[ERROR] {e}",
            "contexts": [],
            "ground_truth": ground_truth,
            "elapsed_sec": 0,
            "doc_type": q.get("doc_type", ""),
            "difficulty": q.get("difficulty", ""),
        })

total_time = time.perf_counter() - t0
logger.info(f"\n⏱️  全部 RAG 查询完成: {len(results)} 题, 耗时 {total_time:.0f}s")
if errors:
    logger.warning(f"⚠️  {len(errors)} 个错误")

# ── 3. 保存原始结果 ──────────────────────────────────────────────
raw_output = {
    "meta": {
        "name": "finRAG Ragas Raw Results",
        "total": len(results),
        "elapsed_sec": round(total_time, 1),
        "errors": len(errors),
        "date": "2026-05-04",
    },
    "results": results
}
with open(str(BASE_DIR / "benchmark" / "ragas_raw.json"), "w") as f:
    json.dump(raw_output, f, ensure_ascii=False, indent=2)
logger.info(f"💾 原始结果已保存: benchmark/ragas_raw.json")

# ── 4. Ragas 评分 ─────────────────────────────────────────────────
# 配置 DeepSeek 作为 Judge LLM
from langchain_openai import ChatOpenAI
from ragas import evaluate
from ragas.metrics import (
    faithfulness,
    context_recall,
    answer_relevancy,
    answer_correctness,
)
from datasets import Dataset

judge_llm = ChatOpenAI(
    model="deepseek-chat",
    openai_api_key=DEEPSEEK_API_KEY,
    openai_api_base=DEEPSEEK_BASE_URL,
    temperature=0,
    max_tokens=1024,
)

# 选择指标
metric_map = {
    "faithfulness": faithfulness,
    "context_recall": context_recall,
    "answer_relevancy": answer_relevancy,
    "answer_correctness": answer_correctness,
}
selected_metrics = []
for name in args.metric.split(","):
    name = name.strip()
    if name in metric_map:
        selected_metrics.append(metric_map[name])
        print(f"   📐 指标: {name}")

if not selected_metrics:
    logger.warning("没有选择有效的指标，使用默认: faithfulness + context_recall")
    selected_metrics = [faithfulness, context_recall]

# 构建 HuggingFace Dataset
# Ragas 需要的列: question, answer, contexts, ground_truth, reference_contexts
ds_dict = {
    "question": [r["question"] for r in results],
    "answer": [r["answer"] for r in results],
    "contexts": [r["contexts"] for r in results],
    "ground_truth": [r["ground_truth"] for r in results],
    "reference_contexts": [r.get("reference_contexts", []) for r in results],
}
dataset = Dataset.from_dict(ds_dict)

logger.info(f"\n🔬 Ragas 评估中（Judge: DeepSeek-chat）...")
logger.info(f"   指标: {[m.name for m in selected_metrics]}")
logger.info(f"   注意: 每题每个指标调用 1 次 LLM = ~{len(results)*len(selected_metrics)} 次请求")

t_ragas = time.perf_counter()
try:
    score = evaluate(
        dataset,
        metrics=selected_metrics,
        llm=judge_llm,
    )
    ragas_elapsed = time.perf_counter() - t_ragas
    logger.info(f"✅ Ragas 评估完成 ({ragas_elapsed:.0f}s)")
    
    # 转换为可序列化格式
    scores = {}
    for m in selected_metrics:
        val = score[m.name]
        if isinstance(val, list):
            scores[m.name] = round(float(sum(val) / len(val)), 4)
        else:
            scores[m.name] = round(float(val), 4)
        logger.info(f"   {m.name}: {scores[m.name]:.4f}")
    
    # 保存详细结果
    output = {
        "meta": {
            "name": "finRAG Ragas Evaluation Report",
            "total": len(results),
            "subset": args.subset or golden['meta']['total_questions'],
            "elapsed_sec": round(total_time, 1),
            "ragas_elapsed_sec": round(ragas_elapsed, 1),
            "date": "2026-05-04",
            "judge_model": "deepseek-chat",
            "metrics": [m.name for m in selected_metrics],
        },
        "aggregate_scores": scores,
        "per_question_scores": [],
        "errors": errors,
    }
    
    # 按题号 + difficulty 组织
    for i, r in enumerate(results):
        q_scores = {}
        for m in selected_metrics:
            vals = score[m.name]
            q_scores[m.name] = round(float(vals[i]) if isinstance(vals, list) else float(vals), 4)
        output["per_question_scores"].append({
            "id": r["id"],
            "question": r["question"],
            "difficulty": r["difficulty"],
            "doc_type": r["doc_type"],
            "elapsed_sec": r["elapsed_sec"],
            **q_scores,
        })
    
    with open(args.output, "w") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    logger.info(f"💾 评估报告已保存: {args.output}")
    
except Exception as e:
    logger.error(f"❌ Ragas 评估失败: {e}")
    import traceback
    traceback.print_exc()
    
    # 保存部分结果
    partial = {
        "meta": {"error": str(e), "partial": True},
        "results": results
    }
    with open(str(BASE_DIR / "benchmark" / "ragas_partial.json"), "w") as f:
        json.dump(partial, f, ensure_ascii=False, indent=2)
