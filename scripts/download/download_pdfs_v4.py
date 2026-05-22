#!/usr/bin/env python3
"""
Phase 3: Aggressive search for remaining missing document types.
Also try directly downloading known PDFs from government sites.
"""
import json, os, re, subprocess, sys, time, urllib.request, ssl

ssl._create_default_https_context = ssl._create_unverified_context
RAW = os.path.expanduser("~/finrag/data/raw")
os.makedirs(RAW, exist_ok=True)

def curl(url, fname, ref="https://www.eastmoney.com/"):
    path = os.path.join(RAW, fname)
    if os.path.exists(path) and os.path.getsize(path) > 5000:
        return os.path.getsize(path)
    r = subprocess.run([
        "curl", "-sL", "--max-time", "30",
        "-H", "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "-H", f"Referer: {ref}",
        "-o", path, url
    ], capture_output=True, timeout=35)
    if os.path.exists(path):
        sz = os.path.getsize(path)
        if sz >= 100:
            return sz
        else:
            os.remove(path)
            return -1
    return -1

def em_search(stock, page=1, sz=30):
    url = f"http://np-anotice-stock.eastmoney.com/api/security/ann?sr=-1&page_size={sz}&page_index={page}&ann_type=SHA&stock_list={stock}&f_node=0&s_node=0"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Referer": "http://data.eastmoney.com/"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())['data']['list']
    except:
        return []

def em_dl(art_code, fname):
    return curl(f"https://pdf.dfcfw.com/pdf/H2_{art_code}_1.pdf", fname)

def safename(s):
    return re.sub(r'[\\/:*?"<>|]', '_', s)[:45]

def find_and_dl_broad(stock, company, keywords, doc_type, max_per_type=2, max_pages=5):
    """Broader search - matches any of the keywords"""
    downloaded = []
    for page in range(1, max_pages+1):
        items = em_search(stock, page, 30)
        if not items:
            break
        for item in items:
            title = item.get('title_ch', '') or ''
            art = item.get('art_code', '')
            dt = item.get('notice_date', '')
            if not art or not title:
                continue
            if any(kw.lower() in title.lower() for kw in keywords):
                # Skip very short titles or page footers
                if len(title) < 25:
                    continue
                # Skip duplicate-looking content
                fname = f"{doc_type[:8]}_{company}_{dt[:10]}_{safename(title)[:35]}.pdf"
                sz = em_dl(art, fname)
                if sz > 0:
                    downloaded.append({
                        'url': f"https://pdf.dfcfw.com/pdf/H2_{art}_1.pdf",
                        'title': title, 'doc_type': doc_type, 'source': 'eastmoney'
                    })
                    print(f"  ✓ {doc_type}: {title[:60]}")
                if len(downloaded) >= max_per_type:
                    break
                time.sleep(0.2)
        if len(downloaded) >= max_per_type:
            break
    return downloaded

print("="*70)
print("PHASE 3: Aggressive search for missing document types")
print("="*70)

all_results = []

# ==== MISSING TYPE 1: 重大资产重组报告书 ====
# Try more stocks with broader keywords
print("\n--- 重大资产重组报告书 ---")
reorg_stocks = [
    ("600019", "宝钢股份"), ("600104", "上汽集团"), ("601390", "中国中铁"),
    ("000002", "万科A"), ("600030", "中信证券"), ("601668", "中国建筑"),
    ("000725", "京东方A"), ("600050", "中国联通"), ("601857", "中国石油"),
    ("000100", "TCL科技"), ("600690", "海尔智家"), ("000651", "格力电器"),
]
for stock, company in reorg_stocks:
    res = find_and_dl_broad(stock, company, 
        ["重大资产重组", "资产重组", "重组报告", "重组预案", "收购报告书"],
        "重大资产重组报告书", max_per_type=1, max_pages=3)
    all_results.extend(res)
    if len([r for r in all_results if r['doc_type']=='重大资产重组报告书']) >= 2:
        print("  Found 2, stopping search")
        break
    time.sleep(0.2)

# ==== MISSING TYPE 2: 基金招募说明书 ====
print("\n--- 基金招募说明书 ---")
fund_stocks = [
    ("000001", "平安银行"), ("600036", "招商银行"), ("601166", "兴业银行"),
    ("600016", "民生银行"), ("601009", "南京银行"), ("600015", "华夏银行"),
    ("601398", "工商银行"), ("601939", "建设银行"), ("601988", "中国银行"),
    ("600519", "贵州茅台"), ("601318", "中国平安"),
]
for stock, company in fund_stocks:
    res = find_and_dl_broad(stock, company,
        ["基金招募说明书", "招募说明书", "基金合同", "基金产品"],
        "基金招募说明书", max_per_type=1, max_pages=3)
    all_results.extend(res)
    if len([r for r in all_results if r['doc_type']=='基金招募说明书']) >= 1:
        print("  Found 1, stopping search")
        break
    time.sleep(0.2)

# ==== MISSING TYPE 3: 信用评级报告 ====
print("\n--- 信用评级报告 ---")
rating_stocks = [
    ("600036", "招商银行"), ("601398", "工商银行"), ("601939", "建设银行"),
    ("600519", "贵州茅台"), ("000333", "美的集团"), ("601318", "中国平安"),
    ("601857", "中国石油"), ("600028", "中国石化"), ("601088", "中国神华"),
    ("000002", "万科A"), ("600900", "长江电力"),
]
for stock, company in rating_stocks:
    res = find_and_dl_broad(stock, company,
        ["信用评级", "评级报告", "跟踪评级", "信用等级"],
        "信用评级报告", max_per_type=1, max_pages=3)
    all_results.extend(res)
    if len([r for r in all_results if r['doc_type']=='信用评级报告']) >= 2:
        print("  Found 2, stopping search")
        break
    time.sleep(0.2)

# ==== MISSING TYPE 4: 股权激励计划 ====
print("\n--- 股权激励计划 ---")
incentive_stocks = [
    ("000333", "美的集团"), ("002415", "海康威视"), ("002475", "立讯精密"),
    ("300750", "宁德时代"), ("000651", "格力电器"), ("600690", "海尔智家"),
    ("000725", "京东方A"), ("601012", "隆基绿能"), ("600585", "海螺水泥"),
    ("002304", "洋河股份"), ("000568", "泸州老窖"),
]
for stock, company in incentive_stocks:
    res = find_and_dl_broad(stock, company,
        ["股权激励", "限制性股票", "股票期权激励", "激励计划"],
        "股权激励计划", max_per_type=1, max_pages=3)
    all_results.extend(res)
    if len([r for r in all_results if r['doc_type']=='股权激励计划']) >= 2:
        print("  Found 2, stopping search")
        break
    time.sleep(0.2)

# ==== MISSING TYPE 5: 配股说明书 ====
print("\n--- 配股说明书 ---")
rights_stocks = [
    ("600030", "中信证券"), ("000776", "广发证券"), ("601688", "华泰证券"),
    ("000166", "申万宏源"), ("600061", "国投资本"), ("601211", "国泰君安"),
    ("601375", "中原证券"), ("601878", "浙商证券"), ("600999", "招商证券"),
    ("000858", "五粮液"), ("600519", "贵州茅台"),
]
for stock, company in rights_stocks:
    res = find_and_dl_broad(stock, company,
        ["配股说明书", "配股", "配股发行"],
        "配股说明书", max_per_type=1, max_pages=3)
    all_results.extend(res)
    if len([r for r in all_results if r['doc_type']=='配股说明书']) >= 1:
        print("  Found 1, stopping search")
        break
    time.sleep(0.2)

# ==== TYPE 16 (repeat): 可转换债券募集说明书 ====
print("\n--- 可转换债券募集说明书 (additional) ---")
cb_stocks = [
    ("601012", "隆基绿能"), ("600036", "招商银行"), ("000333", "美的集团"),
    ("601318", "中国平安"), ("600900", "长江电力"), ("000002", "万科A"),
    ("000725", "京东方A"), ("600019", "宝钢股份"), ("600104", "上汽集团"),
]
for stock, company in cb_stocks:
    res = find_and_dl_broad(stock, company,
        ["可转换债券募集说明书", "可转债募集说明书", "公开发行可转换公司债券"],
        "可转换债券募集说明书", max_per_type=1, max_pages=4)
    all_results.extend(res)
    if len([r for r in all_results if r['doc_type']=='可转换债券募集说明书']) >= 2:
        print("  Found 2, stopping search")
        break
    time.sleep(0.2)

# Save
existing = []
if os.path.exists(os.path.join(RAW, 'download_results.json')):
    with open(os.path.join(RAW, 'download_results.json')) as f:
        existing = json.load(f).get('DOWNLOAD_URLS', [])

combined = existing + all_results
with open(os.path.join(RAW, 'download_results.json'), 'w', encoding='utf-8') as f:
    json.dump({'DOWNLOAD_URLS': combined}, f, ensure_ascii=False, indent=2)

print(f"\nPhase 3 new downloads: {len(all_results)}")
print(f"Total: {len(combined)}")

print("\nAll PDF files:")
for f in sorted(os.listdir(RAW)):
    if f.endswith('.pdf') and os.path.getsize(os.path.join(RAW, f)) > 100:
        sz = os.path.getsize(os.path.join(RAW, f))
        print(f"  {sz//1024:>5d}KB  {f[:65]}")
