#!/usr/bin/env python3
"""
finRAG — Phase 2b: 财务指标结构化提取
======================================
对所有已有的 parent chunk 进行离线扫描，用正则提取财务指标
（营业收入、净利润、毛利率等），更新 Qdrant payload。

这是数值计算节点的离线预处理步骤。
提取的结构化数据供 graph.py 的 computational 路由使用：
LLM 不再从原始文本盲算，而是直接引用结构化指标。

用法:
    python scripts/extract_financial_metrics.py              # 全量提取
    python scripts/extract_financial_metrics.py --force      # 覆盖已有
    python scripts/extract_financial_metrics.py --dry-run    # 预览
"""

import argparse
import json
import logging
import os
import re
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Optional

logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")
logger = logging.getLogger("extract_financial_metrics")

BATCH_SIZE = 100  # Qdrant scroll + update 批量大小


# ══════════════════════════════════════════════════════════════════
#  财务指标正则模式
# ══════════════════════════════════════════════════════════════════

# 常见财务指标名称（按重要性排序）
METRIC_NAMES = [
    # 利润表核心
    "营业总收入", "营业收入", "营业总成本", "营业成本",
    "营业利润", "利润总额", "净利润",
    "归属于母公司所有者的净利润", "归母净利润", "少数股东损益",
    "综合收益总额",
    # 费用
    "销售费用", "管理费用", "研发费用", "财务费用",
    # 投资收益/减值
    "投资收益", "资产减值损失", "信用减值损失", "公允价值变动收益",
    # 资产负债表
    "资产总计", "负债合计", "所有者权益合计", "归属于母公司所有者权益合计",
    "流动资产合计", "非流动资产合计",
    "流动负债合计", "非流动负债合计",
    "货币资金", "应收账款", "存货", "固定资产", "无形资产",
    "短期借款", "长期借款", "应付账款",
    "股本", "资本公积", "盈余公积", "未分配利润",
    # 现金流量表
    "经营活动产生的现金流量净额", "投资活动产生的现金流量净额",
    "筹资活动产生的现金流量净额",
    "现金及现金等价物净增加额",
    "资产负债率", "流动比率", "速动比率",
    "总资产周转率", "存货周转率",
]

# 构建指标名正则片段（格式安全的）
_METRIC_NAMES_PATTERN = "|".join(re.escape(n) for n in METRIC_NAMES)

# 数值正则模式：匹配 "指标名 数值单位" 格式
# 支持：405.29亿元  -45.6亿元  40,529,259,919.13  93.17亿  15.34%  2.5倍
NUM_PATTERN = re.compile(
    r'(-?\d{1,3}(?:,\d{3})*|-?\d+(?:\.\d+)?)\s*'
    r'(万亿元|亿元|万元|元|亿|万|%|％|倍|元/股|元\s*/\s*股)?'
)

# 严格模式：指标名之后紧跟数值
STRICT_LINE_REGEX = re.compile(
    r'(?:其中[：:]?\s*)?'
    r'(' + _METRIC_NAMES_PATTERN + r')'
    r'[：:\s]+'
    r'(-?\d{1,3}(?:,\d{3})*|-?\d+(?:\.\d+)?)'
    r'\s*(万亿元|亿元|万元|元|亿|万|%|％|倍|元/股)?'
)

# 宽松模式：同一行内指标名和数值之间可能隔了其他文字
LOOSE_LINE_REGEX = re.compile(
    r'(' + _METRIC_NAMES_PATTERN + r')'
    r'.{0,30}?'
    r'(-?\d{1,3}(?:,\d{3})*|-?\d+(?:\.\d+)?)'
    r'\s*(万亿元|亿元|万元|元|亿|万|%|％|倍|元/股)?'
)

# 数值提取（行中任何位置）
FIND_NUM_REGEX = re.compile(
    r'(\d{1,3}(?:,\d{3})*|\d+(?:\.\d+)?)\s*'
    r'(万亿元|亿元|万元|元|亿|万|%|％|倍|元/股)?'
)


def parse_numeric(value_str: str, unit: str = "") -> dict:
    """将字符串数值 + 单位转换为标准浮点数（单位：元）。"""
    try:
        clean = value_str.replace(",", "").strip()
        value = float(clean)
    except (ValueError, TypeError):
        return {}

    unit = (unit or "").strip()
    result = {"raw_value": value, "raw_unit": unit}

    if unit in ("万亿元",):
        result["value"] = value * 1e12
        result["unit"] = "元"
    elif unit in ("亿元", "亿"):
        result["value"] = value * 1e8
        result["unit"] = "元"
    elif unit in ("万元", "万"):
        result["value"] = value * 1e4
        result["unit"] = "元"
    elif unit in ("%", "％"):
        result["value"] = value
        result["unit"] = "%"
    elif unit in ("元/股", "元 / 股"):
        result["value"] = value
        result["unit"] = "元/股"
    elif unit in ("倍",):
        result["value"] = value
        result["unit"] = "倍"
    elif unit == "元" or not unit:
        result["value"] = value
        result["unit"] = "元"
    else:
        result["value"] = value
        result["unit"] = unit

    return result


def extract_metrics_from_text(text: str) -> list[dict]:
    """
    从一段文本中提取所有可识别的财务指标。

    返回:
        [{
            "metric": "营业收入",
            "value": 40529000000.0,   # 统一为元
            "unit": "元",
            "raw_text": "营业收入 405.29亿元",
            "confidence": "high",      # high / medium / low
        }, ...]
    """
    results = []
    seen = set()  # 去重: (metric, value_rounded)

    lines = text.split("\n")

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue

        # 1) 严格匹配（指标名 + 数值相邻）
        for m in STRICT_LINE_REGEX.finditer(stripped):
            metric = m.group(1)
            value_raw = m.group(2)
            unit = m.group(3) or ""

            parsed = parse_numeric(value_raw, unit)
            if not parsed:
                continue

            dedup_key = (metric, round(parsed.get("value", 0), 2))
            if dedup_key in seen:
                continue
            seen.add(dedup_key)

            results.append({
                "metric": metric,
                "value": parsed.get("value", 0),
                "unit": parsed.get("unit", ""),
                "raw_text": m.group(0)[:100],
                "confidence": "high",
            })

            if len(results) >= 50:
                break
        if len(results) >= 50:
            break

    # 如果严格匹配太少，尝试宽松匹配
    if len(results) < 5:
        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue
            if len(results) >= 50:
                break
            for m in LOOSE_LINE_REGEX.finditer(stripped):
                metric = m.group(1)
                value_raw = m.group(2)
                unit = m.group(3) or ""

                parsed = parse_numeric(value_raw, unit)
                if not parsed:
                    continue

                dedup_key = (metric, round(parsed.get("value", 0), 2))
                if dedup_key in seen:
                    continue
                seen.add(dedup_key)

                results.append({
                    "metric": metric,
                    "value": parsed.get("value", 0),
                    "unit": parsed.get("unit", ""),
                    "raw_text": m.group(0)[:100],
                    "confidence": "medium",
                })

                if len(results) >= 50:
                    break

    return results


def extract_year_from_text(text: str) -> Optional[int]:
    """从文本中提取最可能的年份。"""
    years = re.findall(r'(20[0-9]{2})年?', text)
    if not years:
        return None
    valid = [int(y) for y in years if 2020 <= int(y) <= 2030]
    if not valid:
        return None
    return Counter(valid).most_common(1)[0][0]


# ══════════════════════════════════════════════════════════════════
#  Qdrant 交互
# ══════════════════════════════════════════════════════════════════

def scroll_parent_chunks(config, dry_run: bool = False) -> list[dict]:
    """滚动读取所有 parent chunk。"""
    from qdrant_client import QdrantClient, models

    client = QdrantClient(path=str(config.QDRANT_DB_PATH))
    col_name = config.QDRANT_COLLECTION

    # 检查集合
    try:
        info = client.get_collection(col_name)
        total = info.points_count
        logger.info(f"📦 集合 '{col_name}': {total} 个点")
    except Exception as e:
        logger.error(f"无法获取集合信息: {e}")
        client.close()
        return []

    # 滚动读取 parent chunks
    all_parents = []
    offset_ptr = None
    while True:
        try:
            result = client.scroll(
                collection_name=col_name,
                limit=BATCH_SIZE,
                with_payload=True,
                with_vectors=False,
                scroll_filter=models.Filter(
                    must=[
                        models.FieldCondition(
                            key="is_parent",
                            match=models.MatchValue(value=True),
                        ),
                    ]
                ),
                offset=offset_ptr,
            )
        except Exception as e:
            logger.error(f"滚动读取失败: {e}")
            break

        points = result[0] if isinstance(result, tuple) else result.points
        if not points:
            break

        for pt in points:
            all_parents.append({
                "id": pt.id,
                "text": pt.payload.get("text", ""),
                "payload": pt.payload,
            })

        # 分页
        if isinstance(result, tuple):
            offset_val = result[-1] if len(result) > 1 else None
        else:
            offset_val = result.next_page_offset
        if offset_val is None or offset_val == offset_ptr:
            break
        offset_ptr = offset_val

        if dry_run and len(all_parents) >= BATCH_SIZE:
            logger.info(f"  DRY-RUN: 仅预览前 {BATCH_SIZE} 个")
            break

    client.close()
    logger.info(f"  读取到 {len(all_parents)} 个 parent chunk")
    return all_parents


def update_metrics_in_qdrant(updates: list[dict], config) -> int:
    """批量更新 Qdrant payload。"""
    from qdrant_client import QdrantClient

    client = QdrantClient(path=str(config.QDRANT_DB_PATH))
    col_name = config.QDRANT_COLLECTION

    updated = 0
    batch_count = len(updates)
    for i in range(0, batch_count, BATCH_SIZE):
        batch = updates[i:i + BATCH_SIZE]
        try:
            # 批量更新：所有 point IDs 一次提交
            ids = [item["id"] for item in batch]
            first_item = batch[0]
            client.set_payload(
                collection_name=col_name,
                payload={"financial_data": first_item["metrics"]},
                points=ids,
            )
            # 如果每项 metrics 不同，需逐点设置（但 same payload structure for all）
            # 这里 metrics 数组是按 chunk 内容提取的，每个 chunk 不同
            # 所以回退到逐点（但用同一 batch 连接减少开销）
            for item in batch:
                try:
                    client.set_payload(
                        collection_name=col_name,
                        payload={"financial_data": item["metrics"]},
                        points=[item["id"]],
                    )
                    updated += 1
                except Exception as e:
                    logger.warning(f"  更新失败 (id={item['id']}): {e}")
        except Exception as e:
            logger.error(f"  批次更新失败: {e}")

        if (i + BATCH_SIZE) % (BATCH_SIZE * 5) == 0 or (i + BATCH_SIZE) >= batch_count:
            logger.info(f"  更新进度: {min(i + BATCH_SIZE, batch_count)}/{batch_count}")

    client.close()
    return updated


# ══════════════════════════════════════════════════════════════════
#  主流程
# ══════════════════════════════════════════════════════════════════

def process_all(mode: str = "pro", force: bool = False, dry_run: bool = False):
    """全量提取并更新财务指标。"""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from scripts.ingestion_v2 import Config

    cfg = Config()

    # 双模式: 设置对应的集合名
    if mode == "safe":
        cfg.QDRANT_COLLECTION = "finrag_safe"
    else:
        cfg.QDRANT_COLLECTION = "finrag_pro"

    logger.info(f"{'='*60}")
    logger.info(f"  finRAG 财务指标结构化提取 (Phase 2b)")
    logger.info(f"  模式: {mode} → 目标集合: {cfg.QDRANT_COLLECTION}")
    logger.info(f"  数据库: {cfg.QDRANT_DB_PATH}")
    if dry_run:
        logger.info(f"  🔍 DRY-RUN 模式（仅预览，不更新 Qdrant）")
    elif force:
        logger.info(f"  🔄 FORCE 模式（覆盖已有 financial_data）")
    else:
        logger.info(f"  ⏭️  跳过已有 financial_data 的 chunk")
    logger.info(f"{'='*60}\n")

    # 1) 读取所有 parent chunk
    parents = scroll_parent_chunks(cfg, dry_run=dry_run)
    if not parents:
        logger.warning("⚠️  没有找到 parent chunk")
        return

    # 2) 逐块提取指标
    updates = []
    total_metrics = 0
    skipped_payload = 0
    skipped_empty = 0

    for i, parent in enumerate(parents):
        text = parent["text"]
        payload = parent["payload"]

        # 跳过已有 financial_data 且非 force 模式
        if not force and payload.get("financial_data"):
            skipped_payload += 1
            continue

        if not text or len(text.strip()) < 100:
            skipped_empty += 1
            continue

        metrics = extract_metrics_from_text(text)

        # Dry-run: 打印前 10 个 chunk 的结果
        if dry_run and i < 10:
            company = payload.get("company", "?")
            section = payload.get("section", "?")
            logger.info(f"  [{i+1}] {company} | {str(section)[:40]} "
                        f"→ {len(metrics)} 个指标")
            for m in metrics[:5]:
                val_str = f"{m['value']:.2f}" if isinstance(m['value'], (int, float)) else str(m['value'])
                logger.info(f"       {m['metric']}: {val_str}{' ' + m['unit'] if m.get('unit') else ''} [{m['confidence']}]")
            if len(metrics) > 5:
                logger.info(f"       ... 还有 {len(metrics)-5} 个")

        if metrics:
            total_metrics += len(metrics)
            updates.append({
                "id": parent["id"],
                "metrics": metrics,
                "company": payload.get("company", "?"),
            })

        # 进度
        if (i + 1) % 500 == 0:
            logger.info(f"  进度: {i+1}/{len(parents)} chunks, "
                        f"累积 {total_metrics} 个指标")

    # 统计
    logger.info(f"\n{'='*60}")
    logger.info(f"  📊 提取统计")
    logger.info(f"  总 chunks: {len(parents)}")
    logger.info(f"  含指标 chunks: {len(updates)}")
    logger.info(f"  总指标数: {total_metrics}")
    logger.info(f"  跳过(已有payload): {skipped_payload}")
    logger.info(f"  跳过(文本太少): {skipped_empty}")
    logger.info(f"  平均指标/chunk: {total_metrics / max(len(updates), 1):.1f}")

    if dry_run:
        logger.info(f"{'='*60}\n")
        return

    # 3) 更新 Qdrant
    if not updates:
        logger.warning("⚠️  没有需要更新的指标")
        return

    logger.info(f"\n📝 更新 Qdrant payload ({len(updates)} chunks)...")
    t0 = time.perf_counter()
    updated = update_metrics_in_qdrant(updates, cfg)
    elapsed = time.perf_counter() - t0

    logger.info(f"\n{'='*60}")
    logger.info(f"  ✅ 完成！")
    logger.info(f"  更新 {updated}/{len(updates)} 个 chunk")
    logger.info(f"  耗时: {elapsed:.1f}s")
    logger.info(f"  提示: 后续重新构建 BM25 索引后生效")
    logger.info(f"{'='*60}\n")


# ══════════════════════════════════════════════════════════════════
#  CLI
# ══════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="finRAG 财务指标结构化提取",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""示例:
  python scripts/extract_financial_metrics.py            # 增量提取 (pro 模式)
  python scripts/extract_financial_metrics.py --force    # 覆盖已有
  python scripts/extract_financial_metrics.py --dry-run  # 预览
  python scripts/extract_financial_metrics.py --mode safe  # 安全模式
        """,
    )
    parser.add_argument("--mode", default="pro", choices=["pro", "safe"],
                        help="目标模式: pro (finrag_pro) / safe (finrag_safe)")
    parser.add_argument("--force", action="store_true",
                        help="覆盖已有的 financial_data payload")
    parser.add_argument("--dry-run", action="store_true",
                        help="预览提取结果，不更新 Qdrant")
    args = parser.parse_args()

    process_all(mode=args.mode, force=args.force, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
