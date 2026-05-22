#!/usr/bin/env python3
"""
Phase 2: More targeted downloads for missing document types.
"""
import json, os, re, subprocess, sys, time, urllib.request, urllib.parse, ssl

ssl._create_default_https_context = ssl._create_unverified_context
RAW = os.path.expanduser("~/finrag/data/raw")
os.makedirs(RAW, exist_ok=True)

def curl(url, fname, ref="https://www.eastmoney.com/"):
    path = os.path.join(RAW, fname)
    if os.path.exists(path) and os.path.getsize(path) > 1000:
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
            print(f"  ✓ {fname[:55]:55s} {sz//1024:>5d}KB")
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
    return re.sub(r'[\\/:*?"<>|]', '_', s)[:50]

def find_and_dl(stock, company, keywords, doc_type, max_per_type=3, max_pages=5):
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
                if len(title) < 20:
                    continue
                fname = f"{doc_type[:8]}_{company}_{dt[:10]}_{safename(title)[:40]}.pdf"
                sz = em_dl(art, fname)
                if sz > 0:
                    downloaded.append({
                        'url': f"https://pdf.dfcfw.com/pdf/H2_{art}_1.pdf",
                        'title': title, 'doc_type': doc_type, 'source': 'eastmoney'
                    })
                if len(downloaded) >= max_per_type:
                    break
                time.sleep(0.2)
        if len(downloaded) >= max_per_type:
            break
    return downloaded

print("="*70)
print("PHASE 2: Targeted downloads for missing document types")
print("="*70)

all_results = []

# --- Search harder for missing types ---

# 1. 招股说明书 - Need to look at recently IPO'd companies
# These companies had IPOs in 2020-2023, their prospectuses should be available
print("\n--- 招股说明书 (expanded search) ---")
for stock, company in [("688981", "中芯国际"), ("688111", "金山办公"), ("300999", "金龙鱼"),
                        ("688036", "传音控股"), ("688396", "华润微"), ("688599", "天合光能")]:
    res = find_and_dl(stock, company, ["招股说明书", "招股说明"], "招股说明书", max_per_type=1, max_pages=3)
    all_results.extend(res)
    if res:
        break
    time.sleep(0.3)

# Also try - 科创板招股说明书
for stock, company in [("688981", "中芯国际"), ("688008", "澜起科技")]:
    res = find_and_dl(stock, company, ["招股说明书"], "招股说明书", max_per_type=1, max_pages=5)
    all_results.extend(res)
    if res:
        break
    time.sleep(0.3)

# 2. 重大资产重组报告书
print("\n--- 重大资产重组报告书 ---")
for stock, company in [("000002", "万科A"), ("600030", "中信证券"), ("601390", "中国中铁"),
                        ("600019", "宝钢股份"), ("600104", "上汽集团"), ("000333", "美的集团")]:
    res = find_and_dl(stock, company, ["重大资产重组", "资产重组"], "重大资产重组报告书", max_per_type=1, max_pages=3)
    all_results.extend(res)
    if len([r for r in all_results if r['doc_type']=='重大资产重组报告书']) >= 2:
        break
    time.sleep(0.3)

# 3. 基金招募说明书
print("\n--- 基金招募说明书 ---")
for stock, company in [("000001", "平安银行"), ("600036", "招商银行"), ("601166", "兴业银行")]:
    res = find_and_dl(stock, company, ["基金招募说明书", "招募说明书"], "基金招募说明书", max_per_type=1, max_pages=3)
    all_results.extend(res)
    if len([r for r in all_results if r['doc_type']=='基金招募说明书']) >= 1:
        break
    time.sleep(0.3)

# 4. 资产评估报告
print("\n--- 资产评估报告 ---")
for stock, company in [("600900", "长江电力"), ("600519", "贵州茅台"), ("000858", "五粮液"),
                        ("601088", "中国神华"), ("000002", "万科A")]:
    res = find_and_dl(stock, company, ["资产评估", "评估报告"], "资产评估报告", max_per_type=1, max_pages=3)
    all_results.extend(res)
    if len([r for r in all_results if r['doc_type']=='资产评估报告']) >= 2:
        break
    time.sleep(0.3)

# 5. 信用评级报告
print("\n--- 信用评级报告 ---")
for stock, company in [("600036", "招商银行"), ("601318", "中国平安"), ("600519", "贵州茅台"),
                        ("000333", "美的集团"), ("601398", "工商银行")]:
    res = find_and_dl(stock, company, ["信用评级", "评级报告", "跟踪评级"], "信用评级报告", max_per_type=1, max_pages=3)
    all_results.extend(res)
    if len([r for r in all_results if r['doc_type']=='信用评级报告']) >= 2:
        break
    time.sleep(0.3)

# 6. 股权激励计划
print("\n--- 股权激励计划 ---")
for stock, company in [("000333", "美的集团"), ("002415", "海康威视"), ("002475", "立讯精密"),
                        ("300750", "宁德时代"), ("000651", "格力电器")]:
    res = find_and_dl(stock, company, ["股权激励", "限制性股票", "股票期权"], "股权激励计划", max_per_type=1, max_pages=3)
    all_results.extend(res)
    if len([r for r in all_results if r['doc_type']=='股权激励计划']) >= 2:
        break
    time.sleep(0.3)

# 7. 配股说明书
print("\n--- 配股说明书 ---")
for stock, company in [("600030", "中信证券"), ("000776", "广发证券"), ("601688", "华泰证券"),
                        ("000166", "申万宏源"), ("600061", "国投资本")]:
    res = find_and_dl(stock, company, ["配股说明书", "配股"], "配股说明书", max_per_type=1, max_pages=3)
    all_results.extend(res)
    if len([r for r in all_results if r['doc_type']=='配股说明书']) >= 1:
        break
    time.sleep(0.3)

# 8. 审计报告
print("\n--- 审计报告 ---")
for stock, company in [("000858", "五粮液"), ("000333", "美的集团"), ("601318", "中国平安"),
                        ("600519", "贵州茅台"), ("000002", "万科A")]:
    res = find_and_dl(stock, company, ["审计报告"], "审计报告", max_per_type=1, max_pages=3)
    all_results.extend(res)
    if len([r for r in all_results if r['doc_type']=='审计报告']) >= 2:
        break
    time.sleep(0.3)

# 9. 可转换债券募集说明书 (another type - search for bond prospectuses)
print("\n--- 可转换债券募集说明书 (new search) ---")
for stock, company in [("600036", "招商银行"), ("601012", "隆基绿能"), ("000333", "美的集团"),
                        ("601318", "中国平安"), ("600900", "长江电力"), ("000002", "万科A")]:
    res = find_and_dl(stock, company, ["可转换债券募集说明书", "可转债募集说明书", "公开发行可转换"], 
                       "可转换债券募集说明书", max_per_type=1, max_pages=4)
    all_results.extend(res)
    if len([r for r in all_results if r['doc_type']=='可转换债券募集说明书']) >= 2:
        break
    time.sleep(0.3)

# Save results
existing = []
if os.path.exists(os.path.join(RAW, 'download_results.json')):
    with open(os.path.join(RAW, 'download_results.json')) as f:
        existing = json.load(f).get('DOWNLOAD_URLS', [])

combined = existing + all_results
with open(os.path.join(RAW, 'download_results.json'), 'w', encoding='utf-8') as f:
    json.dump({'DOWNLOAD_URLS': combined}, f, ensure_ascii=False, indent=2)

print(f"\n\nPhase 2 new downloads: {len(all_results)}")
print(f"Total: {len(combined)}")

print("\nAll PDF files in raw dir:")
for f in sorted(os.listdir(RAW)):
    if f.endswith('.pdf') and os.path.getsize(os.path.join(RAW, f)) > 100:
        sz = os.path.getsize(os.path.join(RAW, f))
        print(f"  {f[:65]:65s} {sz//1024:>5d}KB")
