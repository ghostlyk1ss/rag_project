"""
finRAG — Citation Verifier
===========================
引用验证系统：每个数值必须标明来源段，输出前做数据一致性检验。

三层验证：
1. 解析标注 — 提取 数值[来源N] 映射（支持严格+宽松两种模式）
2. 逐值验证 — 每个数值在其声称的来源中确实存在
3. 结构校验 — 跨文档一致性、加减法、占比、趋势

设计原则：
- 宽松解析：接受 [来源N] 在句尾统一标注（最常见格式）
- 严格验证：每个数值必须至少在一个关联来源中存在
- 非侵入式：通过即不打扰，有疑点才加脚注
"""

import math
import os
import re
import logging
from typing import Any

logger = logging.getLogger("citation_verifier")

# ── 数值提取与单位换算 ─────────────────────────────────────

UNIT_TO_YUAN = {
    "万亿元": 1e12, "亿元": 1e8, "万元": 1e4,
    "百万元": 1e6, "元": 1.0, "亿": 1e8, "万": 1e4,
}

_RATIO_UNITS = frozenset({"%", "个百分点", "倍", "倍"})


def _canonical(value_str: str, unit: str) -> float | None:
    """将数值规格化为 元 或 无单位 的浮点数。"""
    try:
        v = float(value_str.replace(",", ""))
        if unit in _RATIO_UNITS:
            return v
        scale = UNIT_TO_YUAN.get(unit, 1.0)
        return round(v * scale, 2)
    except (ValueError, TypeError):
        return None


def _norm_text(text: str) -> str:
    """标准化文本用于比较（去空格、全角→半角）。"""
    t = text.replace(" ", "").replace("\u3000", "").replace("\n", "")
    result = []
    for c in t:
        if "０" <= c <= "９":
            result.append(chr(ord(c) - 0xFEE0))
        elif "Ａ" <= c <= "Ｚ":
            result.append(chr(ord(c) - 0xFEE0))
        elif "ａ" <= c <= "ｚ":
            result.append(chr(ord(c) - 0xFEE0))
        else:
            result.append(c)
    return "".join(result)


# ── 数值提取 ──────────────────────────────────────────────

_NUM_RE = re.compile(
    r"([\d,]+\.?[\d,]*)\s*"
    r"(万亿元|亿元|万元|百万元|元|%|个百分点|倍)?"
)
_STRICT_SOURCE_RE = re.compile(r"\[来源(\d+)\]")
_SENT_SPLIT_RE = re.compile(r"(?<=[。！？；\n])")


def _is_year(s: str) -> bool:
    """过滤年份数字如 2025"""
    return bool(re.match(r"^20\d{2}$", s))


def _is_source_idx(s: str, surround: str, pos: int) -> bool:
    """判断数字是否来源索引的一部分"""
    # 检查前后文
    before = surround[max(0, pos - 5):pos]
    if "来源" in before or "来源" in surround[max(0, pos - 1):pos + 3]:
        return True
    return False


def _extract_all_numbers(text: str) -> list[dict]:
    """提取文本中所有数值（含位置信息），排除来源标签内的数字。"""
    # 先屏蔽 [来源N] 标签内的数字，防止 1 被提取为数值
    masked = _STRICT_SOURCE_RE.sub("[来源#]", text)
    results = []
    for m in _NUM_RE.finditer(masked):
        val_str = m.group(1)
        unit = m.group(2) or ""
        try:
            vf = float(val_str.replace(",", ""))
        except ValueError:
            continue
        if vf < 0.001:
            continue
        if _is_year(val_str):
            continue
        results.append({
            "value": val_str.replace(",", ""),
            "unit": unit,
            "canonical": _canonical(val_str, unit),
            "raw_text": m.group(0).strip(),
            "pos": m.start(),
            "end": m.end(),
        })
    return results


def _extract_source_tags(text: str) -> list[dict]:
    """提取文本中所有 [来源N] 标签。"""
    results = []
    for m in _STRICT_SOURCE_RE.finditer(text):
        results.append({
            "idx": int(m.group(1)),
            "pos": m.start(),
            "end": m.end(),
        })
    return results


def parse_cited_values(answer: str) -> list[dict]:
    """
    从答案中提取所有带 [来源N] 标注的数值。

    两遍解析：
    Pass 1 — 严格相邻：数值紧挨 [来源N]（< 20字符），如 `405.29亿元[来源1]`
    Pass 2 — 句级别：对句尾 [来源N]，关联句内所有数值

    返回: [{value, unit, source_idx, canonical, raw_text}, ...]
    """
    results = []
    seen = set()

    all_nums = _extract_all_numbers(answer)
    all_tags = _extract_source_tags(answer)

    if not all_tags:
        return results

    # ── Pass 1: 严格相邻 ──────────────────────────────────
    for tag in all_tags:
        tag_idx = tag["idx"]
        # 找标签前最近的数值
        candidates = [
            n for n in all_nums
            if n["end"] <= tag["pos"] and tag["pos"] - n["end"] < 200
        ]
        if not candidates:
            continue
        nearest = max(candidates, key=lambda n: n["end"])
        dist = tag["pos"] - nearest["end"]
        if dist < 20:
            _add_num(results, seen, nearest, tag_idx)

    # ── Pass 2: 句级别关联 ────────────────────────────────
    # 对句尾的 [来源N]，关联句内所有还未被关联的数值
    sentences = _SENT_SPLIT_RE.split(answer)
    sent_start = 0
    for sent in sentences:
        sent_end = sent_start + len(sent)
        if not sent.strip():
            sent_start = sent_end
            continue

        sent_tags = [t for t in all_tags if sent_start <= t["pos"] < sent_end]
        sent_nums = [n for n in all_nums if sent_start <= n["pos"] < sent_end]

        if not sent_tags or not sent_nums:
            sent_start = sent_end
            continue

        # 判断是否连续多标签（[来源1][来源2]）
        tags_sorted = sorted(sent_tags, key=lambda t: t["pos"])
        is_consecutive = all(
            tags_sorted[i + 1]["pos"] - tags_sorted[i]["end"] < 10
            for i in range(len(tags_sorted) - 1)
        )

        if is_consecutive and len(tags_sorted) >= 2:
            # 连续多标签 → 从后往前分配
            nums_desc = sorted(sent_nums, key=lambda n: -n["pos"])
            tags_desc = sorted(tags_sorted, key=lambda t: -t["pos"])
            for num, tag in zip(nums_desc, tags_desc):
                _add_num(results, seen, num, tag["idx"])
        else:
            # 单标签或非连续 → 句尾标签关联所有数值
            for tag in tags_sorted:
                # 检查是否在句尾
                tail = sent[tag["end"] - sent_start:]
                if len(tail.strip()) < 100:
                    # 句尾标签，关联所有句内数值
                    for num in sent_nums:
                        _add_num(results, seen, num, tag["idx"])
                else:
                    # 句中标签，关联最近的数值
                    nearest = min(sent_nums, key=lambda n: abs(tag["pos"] - n["pos"]))
                    _add_num(results, seen, nearest, tag["idx"])

        sent_start = sent_end

    return results


def _add_num(results: list, seen: set, num: dict, src_idx: int):
    """添加一个数值→来源关联。"""
    # 过滤来源索引号本身
    if num["unit"] == "" and num["pos"] is not None:
        # 检查是否可能是来源索引号的一部分
        pass

    cannon = num["canonical"]
    if cannon is None:
        return
    dedup_key = (cannon, num["unit"], src_idx)
    if dedup_key in seen:
        return
    seen.add(dedup_key)
    results.append({
        "value": num["value"],
        "unit": num["unit"],
        "source_idx": src_idx,
        "canonical": cannon,
        "raw_text": num["raw_text"],
    })


def parse_naked_numbers(text: str) -> list[dict]:
    """提取文本中所有数值（无来源标注解析，仅提取）。"""
    return [n for n in _extract_all_numbers(text) if n["canonical"] is not None]


# ── 阶段2：逐值来源验证 ────────────────────────────────────


def _value_in_source(value: str, unit: str, source_text: str) -> bool:
    """
    判断一个数值是否在来源段中出现。
    多种匹配策略：
      1. 精确数值字串匹配
      2. 数值+单位组合
      3. 规格化数值 ±0.5% 容忍度
    """
    norm_val = value.replace(",", "")
    norm_src = _norm_text(source_text)

    # 策略1: 字串匹配
    if norm_val in norm_src:
        return True

    # 策略2: 数值+单位组合
    combos = [f"{norm_val}{unit}", f"{unit}{norm_val}"]
    for c in combos:
        if c in norm_src or c in source_text:
            return True

    # 策略3: 规格化比较
    target_canon = _canonical(value, unit)
    if target_canon is None:
        return False

    src_nums = parse_naked_numbers(source_text)
    for sn in src_nums:
        if sn["canonical"] is None:
            continue
        if unit in _RATIO_UNITS and sn["unit"] in _RATIO_UNITS:
            if abs(sn["canonical"] - target_canon) / max(abs(target_canon), 0.01) < 0.005:
                return True
            continue
        if unit not in _RATIO_UNITS and sn["unit"] not in _RATIO_UNITS:
            if abs(sn["canonical"] - target_canon) / max(abs(target_canon), 1.0) < 0.005:
                return True

    return False


def _get_source_display_name(src: dict) -> str:
    """从 citation dict 构造可读的文档显示名。

    原始 source_file 类似:
      "中邮证券_国内海外销售增长共振创新管线迈入兑现期_AP202605011821928230.md"
    提取后:
      "中邮证券 国内海外销售增长共振创新管线迈入兑现期"
    """
    company = src.get("company", "")
    doc_type = src.get("doc_type", "")
    source_file = src.get("source_file", "")

    if not source_file:
        # fallback: company + doc_type
        parts = [p for p in [company, doc_type] if p]
        return " ".join(parts) if parts else "未知来源"

    # 去掉 .md 和尾部的 _AP{时间戳}
    basename = os.path.basename(source_file)
    report_name = re.sub(r"_AP\d+\.md$", "", basename)
    report_name = report_name.replace(".md", "")
    report_name = report_name.replace("_", " ").strip()

    if report_name and report_name != company:
        return f"{company} {doc_type}（{report_name}）"
    return f"{company} {doc_type}" if company else report_name or doc_type


def verify_each_citation(
    cited_values: list[dict],
    citations: list[dict],
) -> list[dict]:
    """
    对每个带标注的数值，验证它在所声称的来源段中确实存在。
    返回: [{value, unit, source_idx, verified, issue_reason, ...}]
    """
    results = []
    for cv in cited_values:
        idx = cv["source_idx"]
        if idx < 1 or idx > len(citations):
            results.append({
                **cv,
                "company": "",
                "doc_id": "",
                "verified": False,
                "issue_reason": f"[来源{idx}]索引越界（共{len(citations)}个来源）",
            })
            continue

        src = citations[idx - 1]
        src_text = src.get("text", "")
        found = _value_in_source(cv["value"], cv["unit"], src_text)
        results.append({
            **cv,
            "company": src.get("company", ""),
            "doc_id": src.get("doc_id", ""),
            "verified": found,
            "issue_reason": "" if found else (
                f"数值{cv['raw_text']}在[来源{idx}]（{_get_source_display_name(src)}）中未找到"
            ),
        })
    return results


# ── 阶段3：结构一致性检验 ──────────────────────────────────


def check_cross_document_consistency(
    verification_results: list[dict],
    citations: list[dict],
) -> list[dict]:
    """
    跨文档一致性：同一指标在不同文档中不应有异常差异。

    分组策略：
      1. 先按单位类型分组（百分比/金额/其他）— 不同单位永不互相比较
      2. 同单位类型内按数量级分桶（金额按亿级/万级，百分比按个位）
    """
    issues = []

    # 按单位类型分组
    groups_by_unit: dict[str, list[dict]] = {}
    for vr in verification_results:
        if not vr["verified"]:
            continue
        unit_type = _unit_type(vr["unit"])
        groups_by_unit.setdefault(unit_type, []).append(vr)

    for utype, vals in groups_by_unit.items():
        # 同类型内按数量级分桶
        buckets: dict[float, list[dict]] = {}
        for vr in vals:
            c = vr["canonical"]
            if c == 0:
                continue
            bucket_key = _bucket_key(c, utype)
            buckets.setdefault(bucket_key, []).append(vr)

        for _bk, bucket in buckets.items():
            if len(bucket) < 3:
                continue
            bucket_sorted = sorted(bucket, key=lambda x: x["canonical"])
            min_v, max_v = bucket_sorted[0]["canonical"], bucket_sorted[-1]["canonical"]
            if min_v > 0 and (max_v - min_v) / min_v > 0.05:
                sources = [
                    f"{v['raw_text']}[来源{v['source_idx']}] ({v['company']})"
                    for v in bucket_sorted
                ]
                issues.append({
                    "type": "cross_document_inconsistency",
                    "description": (
                        f"同一指标在不同文档中存在差异（最大差异{((max_v-min_v)/min_v*100):.1f}%）："
                        + "; ".join(sources)
                    ),
                    "values": [v["canonical"] for v in bucket_sorted],
                    "sources": [f"来源{v['source_idx']}" for v in bucket_sorted],
                })

    return issues


def _unit_type(unit: str) -> str:
    """判断单位类型：ratio / money / other"""
    if unit in _RATIO_UNITS:
        return "ratio"
    if unit in ("元", "亿元", "万元", "百万元", "亿", "万", "万亿元"):
        return "money"
    return "other"


def _bucket_key(value: float, unit_type: str) -> float:
    """根据单位类型生成分桶 key。

    ratio: 百分比数值直接四舍五入到个位
    money: 按数量级分桶（亿级/万级/元级）
    other: 按数量级取前3位有效数字
    """
    if unit_type == "ratio":
        return round(value, 1)  # 33.06 → 33.1
    elif unit_type == "money":
        # 按 10 的幂次分桶
        if value >= 1e8:  # 亿级 → 按亿
            return round(value / 1e8, 1) * 1e8
        elif value >= 1e4:  # 万级 → 按万
            return round(value / 1e4, 1) * 1e4
        else:
            return round(value, 0)
    else:
        order = 10 ** max(0, int(math.log10(abs(value))) - 2) if value >= 1 else 0.001
        return round(value / order, 0) * order if order > 0 else round(value, 2)


def check_yoy_consistency(
    cited_values: list[dict],
    citations: list[dict],
) -> list[dict]:
    """
    同比增速一致性验证。

    原则：
    1. 只检查**相邻**数值对（按数值大小排序）—— 避免跨年错配
    2. 比率对任何相邻对都不吻合（>10%差异）→ 跳过该比率
       （防止毛利率/净利率等非增长率百分比被当作增长率验证）
    """
    issues = []
    ratios = [cv for cv in cited_values if cv["unit"] in _RATIO_UNITS]
    money_vals = [cv for cv in cited_values if cv["unit"] not in _RATIO_UNITS and cv["unit"] in UNIT_TO_YUAN]

    if len(ratios) < 1 or len(money_vals) < 2:
        return issues

    for r in ratios:
        if r["unit"] != "%":
            continue
        ratio_val = r["canonical"]
        if not (-100 < ratio_val < 1000):
            continue
        expected_ratio = 1.0 + ratio_val / 100.0

        r_company = _get_citation_company(r, citations)

        # 收集同一公司的 money 值，按数值排序
        company_money = [
            v for v in money_vals
            if _get_citation_company(v, citations) == r_company
        ]
        if len(company_money) < 2:
            continue
        company_money.sort(key=lambda x: x["canonical"])

        # 只检查相邻对，并判断该比率是否为增长率（匹配至少一对）
        matched_any = False
        for k in range(len(company_money) - 1):
            v1, v2 = company_money[k], company_money[k + 1]
            for a, b in [(v1["canonical"], v2["canonical"]), (v2["canonical"], v1["canonical"])]:
                if b == 0:
                    continue
                actual_ratio = a / b
                diff = abs(actual_ratio - expected_ratio) / expected_ratio
                if diff < 0.02:
                    matched_any = True
                    break  # 精准匹配，继续检查下一对
                elif diff < 0.1:
                    issues.append({
                        "type": "yoy_inconsistency",
                        "description": (
                            f"增速{ratio_val}%标注于[来源{r['source_idx']}]，"
                            f"但对应数值{v1['raw_text']}[来源{v1['source_idx']}]与"
                            f"{v2['raw_text']}[来源{v2['source_idx']}]的比例为{actual_ratio:.4f}"
                            f"（预期{expected_ratio:.4f}）"
                        ),
                    })
                    matched_any = True
                    break  # 有偏差但也算匹配上了

        if not matched_any:
            logger.debug(
                f"   跳过比率 {ratio_val}%（不匹配任何相邻数值对，可能非增长率）"
            )

    return issues


def _get_citation_company(cv: dict, citations: list[dict]) -> str:
    """从 cited_value 的 source_idx 获取公司名。"""
    idx = cv.get("source_idx", 0)
    if 1 <= idx <= len(citations):
        return citations[idx - 1].get("company", "")
    return ""


# ── 主入口 ──────────────────────────────────────────────────


def verify_answer(
    answer: str,
    citations: list[dict],
    show_all: bool = False,
) -> dict:
    """
    完整引用验证流水线。
    """
    cited_values = parse_cited_values(answer)
    if not cited_values:
        return {
            "passed": True,
            "total_cited": 0,
            "verified": 0,
            "failed": 0,
            "issues": [],
            "details": [],
            "message": "答案中未检测到带来源标注的数值，跳过引用验证",
        }

    verification = verify_each_citation(cited_values, citations)
    failed = [v for v in verification if not v["verified"]]
    verified_ok = [v for v in verification if v["verified"]]

    issues = []
    for f in failed:
        issues.append({
            "type": "citation_mismatch",
            "level": "error",
            "description": f["issue_reason"],
            "value": f["raw_text"],
            "source_idx": f["source_idx"],
            "company": f["company"],
        })

    issues.extend(check_cross_document_consistency(verification, citations))
    issues.extend(check_yoy_consistency(cited_values, citations))

    return {
        "passed": len(failed) == 0,
        "total_cited": len(cited_values),
        "verified": len(verified_ok),
        "failed": len(failed),
        "issues": issues,
        "details": verification if show_all else [],
        "message": (
            f"✅ 全部{len(cited_values)}个数值引用已验证通过"
            if len(failed) == 0
            else f"⚠️ {len(cited_values)}个数值中{len(failed)}个未通过来源验证"
        ),
    }


def add_verification_footer(
    answer: str,
    verifier_result: dict,
) -> str:
    """
    根据验证结果，在答案后追加验证脚注。
    全部通过 → 不加脚注；有问题 → 追加详细说明
    """
    if verifier_result["passed"]:
        return answer if answer else ""

    issues = verifier_result["issues"]
    severe = [i for i in issues if i.get("type") in (
        "citation_mismatch", "cross_document_inconsistency", "yoy_inconsistency"
    )]
    minor = [i for i in issues if i.get("type") in ("arithmetic_minor", "ratio_inconsistency")]

    footer_parts = []
    if severe:
        footer_parts.append("⚠️ **数据验证提醒**：以下数值可能存在问题：")
        for i, s in enumerate(severe[:3], 1):
            footer_parts.append(f"{i}. {s['description']}")
        if len(severe) > 3:
            footer_parts.append(f"   ...另有{len(severe)-3}项需关注")

    if minor:
        if not footer_parts:
            footer_parts.append("ℹ️ **数据校验提示**：")
        footer_parts.append(f"（{len(minor)}项数值存在微小差异，可能由四舍五入导致）")

    if not footer_parts:
        return answer if answer else ""

    footer = "\n\n---\n" + "\n".join(footer_parts)

    if answer:
        return answer + footer
    return footer
