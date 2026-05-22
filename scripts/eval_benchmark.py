#!/usr/bin/env python3
"""
finRAG Benchmark v2.1 — 全量自动化评估
=======================================
基于 4 层评分模板（retrieval/grounding/answer_relevance/financial_accuracy）
使用 DeepSeek 作为 Judge LLM 逐题评分。

用法:
    python scripts/eval_benchmark.py                  # 全量评估
    python scripts/eval_benchmark.py --subset 10      # 仅测前10题（调试）
    python scripts/eval_benchmark.py --output results.json
"""
import argparse
import json
import logging
import os
import re
import sys
import time
from collections import defaultdict
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR))

logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")
logger = logging.getLogger("eval_benchmark")

# ── Judge LLM ──────────────────────────────────────────────────────
from langchain_openai import ChatOpenAI

DEEPSEEK_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")

judge_llm = ChatOpenAI(
    model="deepseek-chat",
    openai_api_key=DEEPSEEK_KEY,
    openai_api_base=DEEPSEEK_URL,
    temperature=0,
    max_tokens=1024,
)

# ── 评分提示模板 ─────────────────────────────────────────────────
SCORE_PROMPT = """你是一位专业的金融RAG系统评估专家。请根据以下维度对模型回答进行评分（1-5分）。

【问题】{question}
【标准答案】{expected_answer}
【检索上下文】{contexts}
【模型回答】{answer}

评分维度：
1. **retrieval**：检索到的上下文是否包含回答问题的必要信息？
   1分=完全不包含，5分=完整包含所有所需信息

2. **grounding**：模型回答是否完全基于检索到的上下文，无幻觉？
   1分=完全虚构，5分=所有信息都可从上下文中找到依据

3. **answer_relevance**：回答是否直接回应了问题，完整且准确？
   1分=完全不相关，5分=直接、完整、准确地回答问题

4. **financial_accuracy**：金融数据的精确性（数值、单位、百分比的准确性）
   1分=数据完全错误，5分=数据完全正确且格式规范

请严格输出 JSON 格式，不要有任何其他内容：
{{"retrieval": <1-5>, "grounding": <1-5>, "answer_relevance": <1-5>, "financial_accuracy": <1-5>, "reasoning": "<简要评语>"}}"""


def score_question(question: str, expected_answer: str, contexts: list[str], answer: str) -> dict:
    """用 DeepSeek Judge 对单题评分"""
    ctx_str = "\n---\n".join(contexts[:5]) if contexts else "(无检索结果)"
    prompt = SCORE_PROMPT.format(
        question=question,
        expected_answer=expected_answer,
        contexts=ctx_str[:4000],  # 限制上下文长度
        answer=answer[:2000],
    )
    try:
        resp = judge_llm.invoke(prompt)
        text = resp.content.strip()
        # 提取 JSON
        json_match = re.search(r'\{.*\}', text, re.DOTALL)
        if json_match:
            scores = json.loads(json_match.group())
            # 确保所有字段存在
            for k in ["retrieval", "grounding", "answer_relevance", "financial_accuracy"]:
                scores[k] = max(1, min(5, int(scores.get(k, 1))))
            scores.setdefault("reasoning", "")
            return scores
        else:
            logger.warning(f"   ⚠️  JSON 解析失败: {text[:100]}")
            return {"retrieval": 1, "grounding": 1, "answer_relevance": 1, "financial_accuracy": 1, "reasoning": "解析失败"}
    except Exception as e:
        logger.error(f"   ❌ Judge 评分错误: {e}")
        return {"retrieval": 1, "grounding": 1, "answer_relevance": 1, "financial_accuracy": 1, "reasoning": f"错误: {e}"}


def main():
    parser = argparse.ArgumentParser(description="finRAG Benchmark v2.1 自动化评估")
    parser.add_argument("--subset", type=int, default=None, help="仅测前 N 题")
    parser.add_argument("--output", type=str, default=str(BASE_DIR / "benchmark" / "eval_results.json"), help="结果输出路径")
    args = parser.parse_args()

    # ── 1. 加载基准测试集 ──────────────────────────────────────────
    benchmark_path = Path(os.environ.get("BENCHMARK_FILE", BASE_DIR / "benchmark" / "finrag_benchmark_v2.json"))
    with open(benchmark_path, encoding="utf-8") as f:
        bm = json.load(f)

    questions = bm["questions"]
    if args.subset:
        questions = questions[:args.subset]

    logger.info(f"📋 加载测试集: {len(questions)} 题（总计 {bm['total_questions']} 题）")

    # ── 2. 初始化 RAG 引擎 ─────────────────────────────────────────
    logger.info("🔧 初始化 RAG 引擎...")
    from backend.services.rag_engine import rag_query
    retry_count = 0
    while retry_count < 3:
        try:
            # 测试一下是否正常
            test = rag_query("测试", top_k=3, stream=False)
            logger.info(f"   ✅ RAG 引擎就绪 (contexts={len(test.get('citations',[]))})")
            break
        except Exception as e:
            retry_count += 1
            logger.warning(f"   ⚠️  初始化失败 (尝试 {retry_count}/3): {e}")
            if retry_count < 3:
                time.sleep(5)
    else:
        logger.error("❌ RAG 引擎初始化失败，退出")
        sys.exit(1)

    # ── 3. 逐题评测 ────────────────────────────────────────────────
    results = []
    errors = []
    t0 = time.perf_counter()

    for i, q in enumerate(questions):
        qid = q.get("id", f"Q{i+1:03d}")
        query = q["question"]
        expected = q.get("expected_answer", "")
        doc_type = q.get("doc_type", "未知")
        caps = q.get("capability_tags", [])

        logger.info(f"[{i+1}/{len(questions)}] {qid} [{doc_type}] {query[:50]}...")

        # 运行 RAG
        t_start = time.perf_counter()
        try:
            rag_result = rag_query(query, top_k=8, stream=False)
            elapsed = time.perf_counter() - t_start
            answer = rag_result.get("answer", "")
            citations = rag_result.get("citations", [])
            contexts = [c.get("text", "") for c in citations][:5]
            logger.info(f"   ✅ {elapsed:.1f}s | cites={len(citations)} | ans_len={len(answer)}")
        except Exception as e:
            elapsed = time.perf_counter() - t_start
            logger.error(f"   ❌ RAG 错误: {e}")
            errors.append({"id": qid, "error": str(e)})
            answer = f"[ERROR] {e}"
            contexts = []

        # Judge 评分
        scores = score_question(query, expected, contexts, answer)

        results.append({
            "id": qid,
            "doc_type": doc_type,
            "capability_tags": caps,
            "question": query,
            "expected_answer": expected,
            "answer": answer,
            "contexts": contexts,
            "elapsed_sec": round(elapsed, 1),
            **scores,
        })

        # 每10题输出一次进度摘要
        if (i + 1) % 10 == 0:
            recent = results[-10:]
            avg_r = sum(r["retrieval"] for r in recent) / len(recent)
            avg_g = sum(r["grounding"] for r in recent) / len(recent)
            avg_a = sum(r["answer_relevance"] for r in recent) / len(recent)
            avg_f = sum(r["financial_accuracy"] for r in recent) / len(recent)
            avg_all = (avg_r + avg_g + avg_a + avg_f) / 4
            logger.info(f"   📊 [{i+1}/{len(questions)}] 近10题均分: R={avg_r:.2f} G={avg_g:.2f} A={avg_a:.2f} F={avg_f:.2f} | 综合={avg_all:.2f}")

    total_time = time.perf_counter() - t0
    avg_time = total_time / len(results) if results else 0

    # ── 4. 汇总统计 ────────────────────────────────────────────────
    agg = {
        "retrieval": round(sum(r["retrieval"] for r in results) / len(results), 4) if results else 0,
        "grounding": round(sum(r["grounding"] for r in results) / len(results), 4) if results else 0,
        "answer_relevance": round(sum(r["answer_relevance"] for r in results) / len(results), 4) if results else 0,
        "financial_accuracy": round(sum(r["financial_accuracy"] for r in results) / len(results), 4) if results else 0,
    }
    agg["综合"] = round(sum(agg.values()) / 4, 4)

    # 按文档类型聚合
    by_type = defaultdict(list)
    for r in results:
        by_type[r["doc_type"]].append(r)
    type_scores = {}
    for dt, items in sorted(by_type.items()):
        type_scores[dt] = {
            "count": len(items),
            "retrieval": round(sum(r["retrieval"] for r in items) / len(items), 4),
            "grounding": round(sum(r["grounding"] for r in items) / len(items), 4),
            "answer_relevance": round(sum(r["answer_relevance"] for r in items) / len(items), 4),
            "financial_accuracy": round(sum(r["financial_accuracy"] for r in items) / len(items), 4),
            "综合": round(
                (sum(r["retrieval"] for r in items) + sum(r["grounding"] for r in items) +
                 sum(r["answer_relevance"] for r in items) + sum(r["financial_accuracy"] for r in items))
                / (len(items) * 4), 4),
        }

    # 按能力标签聚合
    by_cap = defaultdict(list)
    for r in results:
        for tag in r["capability_tags"]:
            by_cap[tag].append(r)
    cap_scores = {}
    for tag, items in sorted(by_cap.items()):
        cap_scores[tag] = {
            "count": len(items),
            "retrieval": round(sum(r["retrieval"] for r in items) / len(items), 4),
            "grounding": round(sum(r["grounding"] for r in items) / len(items), 4),
            "answer_relevance": round(sum(r["answer_relevance"] for r in items) / len(items), 4),
            "financial_accuracy": round(sum(r["financial_accuracy"] for r in items) / len(items), 4),
            "综合": round(
                (sum(r["retrieval"] for r in items) + sum(r["grounding"] for r in items) +
                 sum(r["answer_relevance"] for r in items) + sum(r["financial_accuracy"] for r in items))
                / (len(items) * 4), 4),
        }

    # ── 5. 输出报告 ────────────────────────────────────────────────
    output = {
        "meta": {
            "benchmark": "finRAG Benchmark v2.1",
            "version": "2.1",
            "total_questions": len(results),
            "errors": len(errors),
            "elapsed_sec": round(total_time, 1),
            "avg_per_question_sec": round(avg_time, 1),
            "date": time.strftime("%Y-%m-%d %H:%M"),
            "judge_model": "deepseek-chat",
        },
        "aggregate_scores": agg,
        "by_doc_type": type_scores,
        "by_capability": cap_scores,
        "low_score_questions": [
            {"id": r["id"], "doc_type": r["doc_type"], "question": r["question"][:60] + "...",
             "retrieval": r["retrieval"], "grounding": r["grounding"],
             "answer_relevance": r["answer_relevance"], "financial_accuracy": r["financial_accuracy"],
             "综合": round((r["retrieval"] + r["grounding"] + r["answer_relevance"] + r["financial_accuracy"]) / 4, 2)}
            for r in results if (r["retrieval"] + r["grounding"] + r["answer_relevance"] + r["financial_accuracy"]) / 4 < 3.0
        ],
        "per_question": results,
        "errors": errors,
    }

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    # ── 打印摘要 ──────────────────────────────────────────────────
    logger.info(f"\n{'='*60}")
    logger.info(f"  finRAG Benchmark v2.1 评估完成")
    logger.info(f"  总题数: {len(results)} | 错误: {len(errors)}")
    logger.info(f"  耗时: {total_time:.0f}s (每题平均 {avg_time:.1f}s)")
    logger.info(f"{'='*60}")
    logger.info(f"  综合得分: {agg['综合']:.2f}/5.00")
    logger.info(f"  📍 retrieval:       {agg['retrieval']:.2f}")
    logger.info(f"  📍 grounding:       {agg['grounding']:.2f}")
    logger.info(f"  📍 answer_relevance: {agg['answer_relevance']:.2f}")
    logger.info(f"  📍 financial_acc:    {agg['financial_accuracy']:.2f}")
    logger.info(f"{'='*60}")

    if type_scores:
        logger.info(f"\n📊 按文档类型:")
        for dt, sc in sorted(type_scores.items(), key=lambda x: -x[1]["综合"]):
            logger.info(f"  {dt:12s} ({sc['count']:2d}题) 综合={sc['综合']:.2f}  R={sc['retrieval']:.2f} G={sc['grounding']:.2f} A={sc['answer_relevance']:.2f} F={sc['financial_accuracy']:.2f}")

    if cap_scores:
        logger.info(f"\n📊 按能力标签:")
        for tag, sc in sorted(cap_scores.items(), key=lambda x: -x[1]["综合"]):
            logger.info(f"  {tag:15s} ({sc['count']:2d}题) 综合={sc['综合']:.2f}")

    if output["low_score_questions"]:
        logger.info(f"\n⚠️  低分题目 (综合<3.0): {len(output['low_score_questions'])} 题")
        for q in output["low_score_questions"][:10]:
            logger.info(f"  [{q['id']}] [{q['doc_type']}] {q['question'][:50]}... 综合={q['综合']:.2f}")

    logger.info(f"\n💾 完整报告保存: {args.output}")


if __name__ == "__main__":
    main()
