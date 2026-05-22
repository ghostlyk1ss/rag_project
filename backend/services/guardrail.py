"""
finRAG — Hallucination Guardrail
=================================
Post-generation numerical consistency check.
Extracts numbers from the answer and verifies they exist in the context.
"""

import re
import logging

logger = logging.getLogger("guardrail")


class HallucinationGuardrail:
    """Post-generation numerical consistency check with unit conversion."""

    UNIT_TO_YUAN = {
        "万亿元": 1e12, "亿元": 1e8, "万元": 1e4,
        "百万元": 1e6, "元": 1.0, "亿": 1e8, "万": 1e4,
    }

    @staticmethod
    def _extract_numbers(text: str) -> list[dict]:
        """Extract numbers and normalize to 元 for comparison.

        Returns:
            List of dicts: {value, unit, canonical (float, in 元), raw_text}
        """
        results = []
        patterns = [
            r'([\d,]+\.?[\d,]*)\s*(万亿元|亿元|万元|百万元|元)',
            r'([\d,]+\.?[\d,]*)\s*(亿|万)',
            r'([\d,]+\.?[\d,]*)\s*(%|个百分点|倍)',
        ]
        for pat in patterns:
            for m in re.finditer(pat, text):
                raw_val = m.group(1).replace(",", "")
                unit = m.group(2) if len(m.groups()) > 1 else ""
                try:
                    v = float(raw_val)
                    scale = HallucinationGuardrail.UNIT_TO_YUAN.get(unit, 1.0)
                    results.append({
                        "value": raw_val,
                        "unit": unit,
                        "canonical": round(v * scale, 2),
                        "raw_text": m.group(0).strip(),
                    })
                except ValueError:
                    continue
        return results

    @staticmethod
    def check_consistency(answer: str, context: str) -> dict:
        """Check if numerical values in the answer are consistent with context.
        Handles unit conversions (亿元 ↔ 万元 ↔ 百万元)."""
        answer_nums = HallucinationGuardrail._extract_numbers(answer)
        context_nums = HallucinationGuardrail._extract_numbers(context)

        # Build canonical value set from context (all in 元)
        context_canon = {n["canonical"] for n in context_nums}

        # Also collect raw text for backup check
        context_raw_texts = set()
        for n in context_nums:
            if n["raw_text"]:
                context_raw_texts.add(n["raw_text"])

        issues = []
        for num in answer_nums:
            c = num["canonical"]

            # Check 1: exact canonical match
            if c in context_canon:
                continue

            # Check 2: within 1% tolerance (rounding in unit conversion)
            if any(abs(c - cc) / max(abs(c), 1.0) < 0.01 for cc in context_canon):
                continue

            # Check 3: raw number value appears somewhere in context
            if num["value"] in context:
                continue

            # Check 4: raw text appears in context
            if num["raw_text"] and num["raw_text"] in context:
                continue

            issues.append({
                "value": num["value"],
                "unit": num["unit"],
                "canonical": c,
                "context_snippet": num["raw_text"],
                "reason": f"数值{num['raw_text']}（{c:.0f}元）未在参考资料中找到等价表述",
            })

        return {
            "passed": len(issues) == 0,
            "total_checked": len(answer_nums),
            "issues": issues,
        }
