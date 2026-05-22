#!/usr/bin/env python3
"""Deep-dive extraction of numerical facts from key documents."""
import fitz, os, re, json

RAW = os.path.expanduser("~/finrag/data/raw")
RESULTS = []

def extract_pages(path, pages):
    """Extract text from specific 0-indexed page numbers."""
    try:
        doc = fitz.open(path)
        texts = []
        for p in pages:
            if p < len(doc):
                texts.append(doc[p].get_text())
        doc.close()
        return "\n".join(texts)
    except Exception as e:
        return f"[ERROR: {e}]"

def search_pages(path, term, max_results=5):
    """Search for a term and return page numbers + surrounding context."""
    try:
        doc = fitz.open(path)
        results = []
        for i, page in enumerate(doc):
            text = page.get_text()
            if term in text:
                lines = [l.strip() for l in text.split('\n') if l.strip()]
                contexts = [l for l in lines if term in l]
                results.append({"page": i+1, "context": contexts[:3]})
                if len(results) >= max_results:
                    break
        doc.close()
        return results
    except Exception as e:
        return []

def find_numbers(text, context_keywords=None):
    """Find numbers with context in text."""
    lines = text.split('\n')
    results = []
    for line in lines:
        line = line.strip()
        if not line or len(line) < 10:
            continue
        # Look for financial numbers
        if re.search(r'[万亿亿元%倍]', line):
            results.append(line[:200])
        elif re.search(r'\d+\.?\d*\s*[万亿亿元%倍]', line):
            results.append(line[:200])
    return results[:20]

# ============ 1. 招股说明书 - 联讯仪器 ============
print("=== 联讯仪器 招股说明书 ===")
f = os.path.join(RAW, "zhaogu_联讯仪器_招股说明书.pdf")
doc = fitz.open(f)
print(f"Total pages: {len(doc)}")
# Read TOC / first few pages of content
for p in [3, 4, 5, 10, 20, 30, 50, 100]:
    if p < len(doc):
        text = doc[p].get_text()
        lines = [l.strip() for l in text.split('\n') if l.strip() and len(l.strip())>10]
        print(f"  Page {p+1}: {' | '.join(lines[:5])}")
doc.close()

# Search for financial data
for term in ["营业收入", "净利润", "总资产", "增长率", "主营业务收入", "2024", "2023", "2022"]:
    res = search_pages(f, term, 3)
    for r in res:
        for ctx in r["context"]:
            if re.search(r'[\d,]+\.?\d*', ctx):
                print(f"  [P{r['page']}] {ctx[:180]}")

print()

# ============ 2. 招股说明书 - 长裕集团 ============
print("=== 长裕集团 招股说明书 ===")
f = os.path.join(RAW, "zhaogu_长裕集团_招股说明书.pdf")
doc = fitz.open(f)
print(f"Total pages: {len(doc)}")
for p in [3, 4, 5, 10, 20, 30]:
    if p < len(doc):
        text = doc[p].get_text()
        lines = [l.strip() for l in text.split('\n') if l.strip() and len(l.strip())>10]
        print(f"  Page {p+1}: {' | '.join(lines[:5])}")
doc.close()

for term in ["营业收入", "净利润", "总资产", "增长率", "主营业务收入", "2024", "2023", "2022", "氧氯化锆", "特种尼龙"]:
    res = search_pages(f, term, 3)
    for r in res:
        for ctx in r["context"]:
            if re.search(r'[\d,]+\.?\d*', ctx):
                print(f"  [P{r['page']}] {ctx[:180]}")

print()

# ============ 3. 招商银行年度报告 ============
print("=== 招商银行 2025年度报告 ===")
f = os.path.join(RAW, "年度报告_招商银行_2026-03-28 00:00:00_招商银行_招商银行股份有限公司2025年度报告.pdf")
doc = fitz.open(f)
print(f"Total pages: {len(doc)}")
# Read TOC page more carefully
for p in [0, 1, 2, 3, 4, 5]:
    if p < len(doc):
        text = doc[p].get_text()
        lines = [l.strip() for l in text.split('\n') if l.strip() and len(l.strip())>10]
        print(f"  Page {p+1}: {' | '.join(lines[:8])}")
doc.close()

for term in ["营业收入", "净利润", "总资产", "不良贷款", "拨备覆盖率", "净息差", "2025年"]:
    res = search_pages(f, term, 4)
    for r in res:
        for ctx in r["context"]:
            if re.search(r'[\d,]+\.?\d*', ctx) and len(ctx) < 200:
                print(f"  [P{r['page']}] {ctx[:180]}")

print()

# ============ 4. 五粮液年度报告 ============
print("=== 五粮液 2025年度报告 ===")
f = os.path.join(RAW, "wuliangye_2025_annual_report.pdf")
doc = fitz.open(f)
print(f"Total pages: {len(doc)}")
# Read key pages
for p in [0, 1, 2, 5, 6, 7, 8]:
    if p < len(doc):
        text = doc[p].get_text()
        lines = [l.strip() for l in text.split('\n') if l.strip() and len(l.strip())>5]
        print(f"  Page {p+1}: {' | '.join(lines[:8])}")
doc.close()

for term in ["营业收入", "净利润", "总资产", "增长率", "2025年"]:
    res = search_pages(f, term, 4)
    for r in res:
        for ctx in r["context"]:
            if re.search(r'[\d,]+\.?\d*', ctx):
                print(f"  [P{r['page']}] {ctx[:180]}")

print()

# ============ 5. 茅台年度报告 ============
print("=== 茅台 2025年度报告 ===")
f = os.path.join(RAW, "maotai_2025_annual_report.pdf")
doc = fitz.open(f)
print(f"Total pages: {len(doc)}")
for p in [0, 1, 2, 5, 6, 7, 8]:
    if p < len(doc):
        text = doc[p].get_text()
        lines = [l.strip() for l in text.split('\n') if l.strip() and len(l.strip())>5]
        print(f"  Page {p+1}: {' | '.join(lines[:8])}")
doc.close()

for term in ["营业收入", "净利润", "总资产", "增长率", "2025年"]:
    res = search_pages(f, term, 4)
    for r in res:
        for ctx in r["context"]:
            if re.search(r'[\d,]+\.?\d*', ctx):
                print(f"  [P{r['page']}] {ctx[:180]}")

print()

# ============ 6. 重大资产重组 ============
print("=== 北方长龙 重大资产重组报告书 ===")
f = os.path.join(RAW, "chongzu_重大资产重组报告书_草案.pdf")
doc = fitz.open(f)
print(f"Total pages: {len(doc)}")
for p in [3, 4, 5, 10, 20]:
    if p < len(doc):
        text = doc[p].get_text()
        lines = [l.strip() for l in text.split('\n') if l.strip() and len(l.strip())>10]
        print(f"  Page {p+1}: {' | '.join(lines[:5])}")
doc.close()

for term in ["交易价格", "本次交易", "营业收入", "净利润", "总资产", "评估"]:
    res = search_pages(f, term, 3)
    for r in res:
        for ctx in r["context"]:
            if re.search(r'[\d,]+\.?\d*', ctx):
                print(f"  [P{r['page']}] {ctx[:180]}")

print()

# ============ 7. 中芯国际重组 ============
print("=== 中芯国际 资产重组报告书 ===")
f = os.path.join(RAW, "chongzu_中芯国际_资产重组报告书.pdf")
doc = fitz.open(f)
print(f"Total pages: {len(doc)}")
for p in [3, 4, 5, 10, 20]:
    if p < len(doc):
        text = doc[p].get_text()
        lines = [l.strip() for l in text.split('\n') if l.strip() and len(l.strip())>10]
        print(f"  Page {p+1}: {' | '.join(lines[:5])}")
doc.close()

for term in ["交易价格", "发行股份", "营业收入", "净利润", "标的资产"]:
    res = search_pages(f, term, 4)
    for r in res:
        for ctx in r["context"]:
            if re.search(r'[\d,]+\.?\d*', ctx):
                print(f"  [P{r['page']}] {ctx[:180]}")

print()

# ============ 8. 广东鸿特配股说明书 ============
print("=== 广东鸿特 配股说明书 ===")
f = os.path.join(RAW, "peigu_广东鸿特_配股说明书_申报稿.pdf")
doc = fitz.open(f)
print(f"Total pages: {len(doc)}")
for p in [3, 4, 5, 10, 20]:
    if p < len(doc):
        text = doc[p].get_text()
        lines = [l.strip() for l in text.split('\n') if l.strip() and len(l.strip())>10]
        print(f"  Page {p+1}: {' | '.join(lines[:5])}")
doc.close()

for term in ["营业收入", "净利润", "总资产", "配股", "募集资金"]:
    res = search_pages(f, term, 3)
    for r in res:
        for ctx in r["context"]:
            if re.search(r'[\d,]+\.?\d*', ctx):
                print(f"  [P{r['page']}] {ctx[:180]}")

print()

# ============ 9. 信用评级报告 ============
print("=== 陕西能源 信用评级报告 ===")
f = os.path.join(RAW, "credit_rating_陕西能源_2026年度信用评级报告.pdf")
doc = fitz.open(f)
print(f"Total pages: {len(doc)}")
for p in [0, 1, 2, 3, 4]:
    if p < len(doc):
        text = doc[p].get_text()
        lines = [l.strip() for l in text.split('\n') if l.strip() and len(l.strip())>10]
        print(f"  Page {p+1}: {' | '.join(lines[:5])}")
doc.close()

for term in ["营业收入", "净利润", "总资产", "评级", "2025", "2024"]:
    res = search_pages(f, term, 3)
    for r in res:
        for ctx in r["context"]:
            if re.search(r'[\d,]+\.?\d*', ctx):
                print(f"  [P{r['page']}] {ctx[:160]}")

print()

# ============ 10. Research Reports - Deep Dive ============
print("\n=========== RESEARCH REPORTS DEEP DIVE ===========\n")

reports = [
    ("联储证券_中矿资源.pdf", "联储证券", "中矿资源"),
    ("太平洋证券_徐工机械.pdf", "太平洋证券", "徐工机械"),
    ("山西证券_时尚珠宝.pdf", "山西证券", "潮宏基"),
    ("西南证券_新华医疗.pdf", "西南证券", "新华医疗"),
    ("西南证券_海外业绩靓丽新业务布局打开成长空间_AP202604301821872575.pdf", "西南证券", "裕同科技"),
    ("国信证券_全球化布局.pdf", "国信证券", "新泉股份"),
    ("国信证券_费用控制_业绩高增.pdf", "国信证券", "皖仪科技"),
    ("中银证券_福斯特_行业龙头.pdf", "中银证券", "福斯特"),
    ("中银证券_25年营收大幅增长AIOS应用场景快速拓展_AP202605011821916538.pdf", "中银证券", "中科创达"),
    ("信达证券_盈利能力拐点.pdf", "信达证券", "翔宇医疗"),
    ("信达证券_培养基业务持续强劲增长26Q1澎立生物正式并表_AP202605011821928046.pdf", "信达证券", "奥浦迈"),
    ("东吴证券_光通信行业红利.pdf", "东吴证券", "蘅东光"),
    ("东吴证券_同享科技_2025年报点评.pdf", "东吴证券", "同享科技"),
    ("东吴证券_2025年报2026一季报点评一季度归母净利同增81矿冶环保新材料双轮驱动_AP202605011821927967.pdf", "东吴证券", "赛恩斯"),
    ("华源证券_产品价格景气.pdf", "华源证券", "未知(化工)"),
    ("华源证券_扣非环比增长.pdf", "华源证券", "中金公司"),
    ("华源证券_流水线快速装机试剂增长有望提速_AP202605011821929771.pdf", "华源证券", "未知(医疗器械)"),
    ("中邮证券_Q1盈利能力改善.pdf", "中邮证券", "中国建材"),
    ("中邮证券_供给协同.pdf", "中邮证券", "海螺集团"),
    ("中邮证券_新战略行业.pdf", "中邮证券", "未知(机械)"),
    ("中邮证券_底部蓄力全链优势行稳致远_AP202605011821928223.pdf", "中邮证券", "未知(化工)"),
    ("中邮证券_生猪价格低迷致亏损屠宰业务盈利向好_AP202605011821928225.pdf", "中邮证券", "牧原股份"),
    ("中邮证券_锂矿业务有望26年下半年投产成长可期_AP202605011821928221.pdf", "中邮证券", "未知(钢铁)"),
    ("中邮证券_一季度业绩大增资源优势厚积薄发_AP202605011821928222.pdf", "中邮证券", "未知(有色)"),
    ("中邮证券_国内海外销售增长共振创新管线迈入兑现期_AP202605011821928230.pdf", "中邮证券", "未知(医药)"),
]

for rname, broker, company in reports:
    fpath = os.path.join(RAW, rname)
    if not os.path.exists(fpath):
        print(f"MISSING: {rname}")
        continue
    try:
        doc = fitz.open(fpath)
        text = ""
        for i in range(min(len(doc), 6)):
            text += doc[i].get_text()
        doc.close()
        
        lines = [l.strip() for l in text.split('\n') if l.strip() and len(l.strip())>5]
        print(f"\n--- {broker} | {company} ({rname[:40]}) ---")
        
        # Find key numbers
        for line in lines[:80]:
            if any(kw in line for kw in ["亿元", "万元", "百万", "亿", "元", "%", "倍", "买入", "增持", "目标价", "EPS", "PE", "营收", "利润"]):
                if re.search(r'[\d,]+\.?\d*', line):
                    print(f"  {line[:200]}")
    except Exception as e:
        print(f"  ERROR: {e}")

print("\n\nDONE")
