#!/usr/bin/env python3
"""Check remaining documents and collect final data."""
import fitz, os, re, json

RAW = os.path.expanduser("~/finrag/data/raw")

# Check remaining documents
remaining = [
    ("policy_能源白皮书.pdf",),
    ("股权激励计划_海尔智家_2026-04-28_海尔智家_北京市中伦律师事务所关于海尔智家股份有限公司注销2021年A.pdf",),
    ("年度报告_中国平安_2026-03-21 00:00:00_中国平安_平安银行股份有限公司2025年年度报告摘要.pdf",),
    ("年度报告_中国平安_2026-03-27 00:00:00_中国平安_中国平安2025年年度报告摘要.pdf",),
    ("年度报告_招商银行_2026-03-28 00:00:00_招商银行_招商银行股份有限公司2025年度报告摘要.pdf",),
]

for (fname,) in remaining:
    fpath = os.path.join(RAW, fname)
    if not os.path.exists(fpath):
        print(f"MISSING: {fname}")
        continue
    try:
        doc = fitz.open(fpath)
        text = ""
        for i in range(min(len(doc), 4)):
            text += doc[i].get_text()
        doc.close()
        lines = [l.strip() for l in text.split('\n') if l.strip() and len(l.strip())>5]
        print(f"\n=== {fname[:60]} ===")
        for line in lines[:20]:
            print(f"  {line[:150]}")
        # Search for key financial data
        for term in ["营业收入", "净利润", "总资产", "增长率", "不良"]:
            for i, line in enumerate(lines):
                if term in line and re.search(r'[\d,]+\.?\d*', line):
                    print(f"  >>> {line[:200]}")
    except Exception as e:
        print(f"ERROR {fname}: {e}")

print("\n\nDONE")
