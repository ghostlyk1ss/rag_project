#!/usr/bin/env python3
"""
Phase 4: Try alternative sources and additional stock codes for remaining types.
Also clean up small/irrelevant files.
"""
import json, os, re, subprocess, sys, time, urllib.request, ssl

ssl._create_default_https_context = ssl._create_unverified_context
RAW = os.path.expanduser("~/finrag/data/raw")
os.makedirs(RAW, exist_ok=True)

def curl(url, fname, ref="https://www.eastmoney.com/"):
    path = os.path.join(RAW, fname)
    if os.path.exists(path) and os.path.getsize(path) > 100:
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

print("="*70)
print("PHASE 4: Alternative sources and corrections")
print("="*70)

all_results = []

# ==== Try cninfo.com.cn API again with different approach ====
print("\n--- cninfo.com.cn API attempt ---")
# Use requests-like approach with curl
cookie_cmd = "curl -sL -c /tmp/cninfo_cookies.txt https://www.cninfo.com.cn -o /dev/null"
subprocess.run(cookie_cmd, shell=True, capture_output=True, timeout=15)

# Try searching for specific document types
for keyword, doc_type in [
    ("招股说明书", "招股说明书"),
    ("重大资产重组", "重大资产重组报告书"),
    ("基金招募说明书", "基金招募说明书"),
    ("信用评级报告", "信用评级报告"),
    ("配股说明书", "配股说明书"),
]:
    result = subprocess.run([
        "curl", "-sL", "--max-time", "15",
        "https://www.cninfo.com.cn/new/fulltextSearch/full",
        "-H", "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "-H", "Content-Type: application/x-www-form-urlencoded;charset=UTF-8",
        "-H", "X-Requested-With: XMLHttpRequest",
        "-H", "Origin: https://www.cninfo.com.cn",
        "-H", "Referer: https://www.cninfo.com.cn/new/commonUrl?url=disclosure/list/notice",
        "-b", "/tmp/cninfo_cookies.txt",
        "--data", f"stock=&plate=&category=&trade=&column=&keyword={keyword}&pageNum=1&pageSize=5&tabName=fulltext&seDate=&sortName=createdate&sortType=desc&isHLtitle=true"
    ], capture_output=True, timeout=20)
    
    data = result.stdout.decode('utf-8', errors='ignore')
    try:
        j = json.loads(data)
        if 'classifiedAnnouncements' in j:
            items = j['classifiedAnnouncements']
            if isinstance(items, list):
                print(f"  {doc_type}: found {len(items)} items")
                for a in items[:3]:
                    print(f"    {a.get('announcementTitle','')[:60]}")
            elif isinstance(items, dict):
                for k, v in items.items():
                    if isinstance(v, list):
                        print(f"  {doc_type} ({k}): {len(v)} items")
                        for a in v[:2]:
                            print(f"    {a.get('announcementTitle','')[:60]}")
        else:
            print(f"  {doc_type}: {str(j)[:200]}")
    except:
        print(f"  {doc_type}: API returned non-JSON ({data[:200]})")
    time.sleep(0.5)

# ==== Search more stocks for missing types ====
# 重大资产重组 - try stocks that underwent major restructuring
print("\n--- 重大资产重组报告书 (expanded) ---")
reorg_search = [
    # Recent major M&A cases in China
    ("000876", "新希望", ["重大资产重组", "资产重组", "重组报告"]),
    ("000002", "万科", ["重大资产重组"]),
    ("000725", "京东方", ["重大资产重组", "资产重组"]),
    ("600019", "宝钢", ["重大资产重组", "换股吸收合并"]),
    ("601727", "上海电气", ["重大资产重组"]),
    ("600010", "包钢股份", ["重大资产重组"]),
    ("600115", "中国东航", ["重大资产重组"]),
    ("601111", "中国国航", ["重大资产重组"]),
    ("601668", "中国建筑", ["重大资产重组"]),
    ("600585", "海螺水泥", ["重大资产重组"]),
    ("000100", "TCL科技", ["重大资产重组"]),
    ("601618", "中国中冶", ["重大资产重组"]),
    ("600030", "中信证券", ["重大资产重组", "重组"]),
    ("600036", "招商银行", ["重大资产重组"]),
    ("601318", "中国平安", ["重大资产重组"]),
    ("000858", "五粮液", ["重大资产重组"]),
    ("600900", "长江电力", ["重大资产重组", "重组"]),
]
for stock, company, keywords in reorg_search:
    items = em_search(stock, 1, 30)
    found = False
    for item in items:
        title = item.get('title_ch', '') or ''
        art = item.get('art_code', '')
        dt = item.get('notice_date', '')
        if any(kw.lower() in title.lower() for kw in keywords):
            if len(title) < 25:
                continue
            fname = f"重大资产重组_{company}_{dt[:10]}_{safename(title)[:35]}.pdf"
            sz = em_dl(art, fname)
            if sz > 0:
                all_results.append({
                    'url': f"https://pdf.dfcfw.com/pdf/H2_{art}_1.pdf",
                    'title': title, 'doc_type': '重大资产重组报告书', 'source': 'eastmoney'
                })
                print(f"  ✓ {company}: {title[:60]}")
                found = True
                break
            time.sleep(0.2)
    if found:
        if len([r for r in all_results if r['doc_type']=='重大资产重组报告书']) >= 2:
            break
    time.sleep(0.2)

# 信用评级报告 - try bond-related stocks
print("\n--- 信用评级报告 (expanded) ---")
for stock, company in [
    ("600036", "招商银行"), ("601398", "工商银行"), ("600028", "中国石化"),
    ("601857", "中国石油"), ("601088", "中国神华"), ("600900", "长江电力"),
    ("000002", "万科A"), ("600019", "宝钢股份"), ("601668", "中国建筑"),
    ("600585", "海螺水泥"), ("601390", "中国中铁"), ("601618", "中国中冶"),
    ("600104", "上汽集团"), ("000333", "美的集团"), ("600690", "海尔智家"),
]:
    items = em_search(stock, 1, 30)
    for item in items:
        title = item.get('title_ch', '') or ''
        art = item.get('art_code', '')
        dt = item.get('notice_date', '')
        if any(kw in title.lower() for kw in ['信用评级', '评级报告', '跟踪评级', '信用等级']):
            if len(title) < 25:
                continue
            fname = f"信用评级_{company}_{dt[:10]}_{safename(title)[:35]}.pdf"
            sz = em_dl(art, fname)
            if sz > 0:
                all_results.append({
                    'url': f"https://pdf.dfcfw.com/pdf/H2_{art}_1.pdf",
                    'title': title, 'doc_type': '信用评级报告', 'source': 'eastmoney'
                })
                print(f"  ✓ {company}: {title[:60]}")
                if len([r for r in all_results if r['doc_type']=='信用评级报告']) >= 2:
                    break
                time.sleep(0.2)
        time.sleep(0.1)
    if len([r for r in all_results if r['doc_type']=='信用评级报告']) >= 2:
        break

# 配股说明书
print("\n--- 配股说明书 (expanded) ---")
for stock, company in [
    ("000776", "广发证券"), ("601688", "华泰证券"), ("000166", "申万宏源"),
    ("601375", "中原证券"), ("601878", "浙商证券"), ("600999", "招商证券"),
    ("601236", "红塔证券"), ("601066", "中信建投"), ("601162", "天风证券"),
    ("002736", "国信证券"), ("000750", "国海证券"),
]:
    items = em_search(stock, 1, 30)
    for item in items:
        title = item.get('title_ch', '') or ''
        art = item.get('art_code', '')
        dt = item.get('notice_date', '')
        if any(kw in title.lower() for kw in ['配股说明书', '配股发行', '配股']):
            if len(title) < 25:
                continue
            # Skip non-substantive announcements
            if any(x in title.lower() for x in ['提示', '公告', '结果', '股份变动']):
                if '配股说明书' not in title and '配股发行' not in title:
                    continue
            fname = f"配股说明书_{company}_{dt[:10]}_{safename(title)[:35]}.pdf"
            sz = em_dl(art, fname)
            if sz > 0:
                all_results.append({
                    'url': f"https://pdf.dfcfw.com/pdf/H2_{art}_1.pdf",
                    'title': title, 'doc_type': '配股说明书', 'source': 'eastmoney'
                })
                print(f"  ✓ {company}: {title[:60]}")
                break
            time.sleep(0.2)
    if len([r for r in all_results if r['doc_type']=='配股说明书']) >= 1:
        break
    time.sleep(0.2)

# ==== Try downloading directly from known government PDF URLs ====
print("\n--- Government and regulatory PDFs ---")

# Try to find and download PDFs from various Chinese government sites
# National Bureau of Statistics - try the EasyData API or direct PDF links
gov_urls = [
    # 2023年国民经济和社会发展统计公报 (PDF version from stats.gov.cn)
    ("https://www.stats.gov.cn/sj/zxfb/202302/t20230228_1919005.html", "stats_communique_2023.html", ""),
    
    # PBOC - Financial Stability Report (中国金融稳定报告)
    ("http://www.pbc.gov.cn/goutongjiaoliu/113456/113469/5651227/index.html", "pboc_stability_report.html", ""),
    
    # 2024 statistical communique
    ("https://www.stats.gov.cn/sj/zxfb/202502/t20250228_1959047.html", "stats_communique_2024.html", ""),
]

for url, fname, _ in gov_urls:
    curl(url, fname, "https://www.stats.gov.cn/")
    path = os.path.join(RAW, fname)
    if os.path.exists(path):
        with open(path, 'r', encoding='utf-8', errors='ignore') as f:
            html = f.read()
        # Look for links to documents
        links = re.findall(r'href=[\'"]([^\'"]*\.(pdf|doc|docx|zip|xls))[\'"]', html, re.IGNORECASE)
        links2 = re.findall(r'href=[\'"]([^\'"]*(?:download|附|附件|xz)[^\'"]*)[\'"]', html)
        all_links = [l[0] for l in links]
        for l in links2:
            if isinstance(l, tuple):
                all_links.append(l[0])
            else:
                all_links.append(l)
        for i, link in enumerate(all_links[:5]):
            if not link.startswith('http'):
                if link.startswith('//'):
                    link = 'https:' + link
                elif link.startswith('/'):
                    link = 'https://www.stats.gov.cn' + link
                else:
                    link = urllib.parse.urljoin(url, link)
            ext = link.split('.')[-1].lower() if '.' in link else 'pdf'
            if ext in ['pdf', 'doc', 'docx']:
                curl(link, f"stats_download_{i}.{ext}", "https://www.stats.gov.cn/")
        os.remove(path)

# Save results
existing = []
if os.path.exists(os.path.join(RAW, 'download_results.json')):
    with open(os.path.join(RAW, 'download_results.json')) as f:
        existing = json.load(f).get('DOWNLOAD_URLS', [])

combined = existing + all_results
# Deduplicate by URL
seen_urls = set()
deduped = []
for item in combined:
    if item['url'] not in seen_urls:
        seen_urls.add(item['url'])
        deduped.append(item)

with open(os.path.join(RAW, 'download_results.json'), 'w', encoding='utf-8') as f:
    json.dump({'DOWNLOAD_URLS': deduped}, f, ensure_ascii=False, indent=2)

print(f"\nPhase 4 new: {len(all_results)}")
print(f"Total unique: {len(deduped)}")

# Count by doc_type
type_counts = {}
for d in deduped:
    t = d['doc_type']
    type_counts[t] = type_counts.get(t, 0) + 1
print("\nBy type:")
for t, c in sorted(type_counts.items()):
    print(f"  {t}: {c}")
