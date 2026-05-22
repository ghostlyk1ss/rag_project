#!/usr/bin/env python3
"""
Direct download approach for Chinese financial PDFs.
Uses eastmoney API + known direct URLs.
"""
import json, os, re, subprocess, sys, time, urllib.request, urllib.parse, ssl

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
            print(f"  ✓ {fname[:55]:55s} {sz//1024:>5d}KB")
            return sz
        else:
            os.remove(path)
            print(f"  ✗ {fname[:55]:55s} too small ({sz}B)")
            return -1
    print(f"  ✗ {fname[:55]:55s} failed")
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
    return re.sub(r'[\\/:*?"<>|]', '_', s)[:80]

def find_and_dl(stock, company, keywords, doc_type, max_per_type=3):
    """Search eastmoney for stock announcements matching keywords"""
    downloaded = []
    for page in [1, 2]:
        items = em_search(stock, page)
        for item in items:
            title = item.get('title_ch', '') or item.get('announcementTitle', '') or ''
            art = item.get('art_code', '')
            dt = item.get('notice_date', '')
            if not art or not title:
                continue
            if any(kw.lower() in title.lower() for kw in keywords):
                # Skip if title looks like a short notice (no colon separator, very short)
                if len(title) < 20:
                    continue
                fname = f"{doc_type[:8]}_{company}_{dt}_{safename(title)[:40]}.pdf"
                sz = em_dl(art, fname)
                if sz > 0:
                    downloaded.append({
                        'url': f"https://pdf.dfcfw.com/pdf/H2_{art}_1.pdf",
                        'title': title, 'doc_type': doc_type, 'source': 'eastmoney'
                    })
                if len(downloaded) >= max_per_type:
                    break
                time.sleep(0.3)
        if len(downloaded) >= max_per_type:
            break
    return downloaded

print("="*60)
print("Searching EastMoney for financial documents...")
print("="*60)

all_results = []

# 1. 招股说明书 (IPO prospectus) - look for recent IPOs
print("\n--- 1. 招股说明书 ---")
all_results.extend(find_and_dl("688981", "中芯国际", ["招股说明书"], "招股说明书"))
time.sleep(0.5)
all_results.extend(find_and_dl("688111", "金山办公", ["招股说明书"], "招股说明书"))
time.sleep(0.5)

# 2. 可转换公司债券募集说明书
print("\n--- 2. 可转换公司债券募集说明书 ---")
all_results.extend(find_and_dl("601012", "隆基绿能", ["可转换", "可转债", "募集说明书"], "可转债募集说明书"))
time.sleep(0.5)
all_results.extend(find_and_dl("600036", "招商银行", ["可转换", "可转债", "募集说明书"], "可转债募集说明书"))
time.sleep(0.5)

# 3. 重大资产重组报告书
print("\n--- 3. 重大资产重组报告书 ---")
all_results.extend(find_and_dl("000002", "万科A", ["重大资产重组"], "重大资产重组报告书"))
time.sleep(0.5)
all_results.extend(find_and_dl("600030", "中信证券", ["重大资产重组"], "重大资产重组报告书"))
time.sleep(0.5)

# 4. 基金招募说明书
print("\n--- 4. 基金招募说明书 ---")
all_results.extend(find_and_dl("000001", "平安银行", ["基金招募", "招募说明书"], "基金招募说明书"))
time.sleep(0.5)

# 5. 律师事务所法律意见书
print("\n--- 5. 法律意见书 ---")
all_results.extend(find_and_dl("601318", "中国平安", ["法律意见书"], "法律意见书"))
time.sleep(0.5)
all_results.extend(find_and_dl("600519", "贵州茅台", ["法律意见书"], "法律意见书"))
time.sleep(0.5)

# 6. 资产评估报告
print("\n--- 6. 资产评估报告 ---")
all_results.extend(find_and_dl("600900", "长江电力", ["资产评估"], "资产评估报告"))
time.sleep(0.5)
all_results.extend(find_and_dl("600519", "贵州茅台", ["资产评估"], "资产评估报告"))
time.sleep(0.5)

# 7. 信用评级报告
print("\n--- 7. 信用评级报告 ---")
all_results.extend(find_and_dl("600036", "招商银行", ["信用评级"], "信用评级报告"))
time.sleep(0.5)
all_results.extend(find_and_dl("601318", "中国平安", ["信用评级"], "信用评级报告"))
time.sleep(0.5)

# 8. 股权激励计划
print("\n--- 8. 股权激励计划 ---")
all_results.extend(find_and_dl("000333", "美的集团", ["股权激励"], "股权激励计划"))
time.sleep(0.5)
all_results.extend(find_and_dl("002415", "海康威视", ["股权激励"], "股权激励计划"))
time.sleep(0.5)

# 9. 配股说明书
print("\n--- 9. 配股说明书 ---")
all_results.extend(find_and_dl("600030", "中信证券", ["配股说明书"], "配股说明书"))
time.sleep(0.5)

# 10. 年度报告 (different companies)
print("\n--- 10. 年度报告 ---")
all_results.extend(find_and_dl("000333", "美的集团", ["年度报告"], "年度报告"))
time.sleep(0.5)
all_results.extend(find_and_dl("601318", "中国平安", ["年度报告"], "年度报告"))
time.sleep(0.5)
all_results.extend(find_and_dl("600036", "招商银行", ["年度报告"], "年度报告"))
time.sleep(0.5)
all_results.extend(find_and_dl("000001", "平安银行", ["年度报告"], "年度报告"))
time.sleep(0.5)

# 11. 审计报告
print("\n--- 11. 审计报告 ---")
all_results.extend(find_and_dl("000858", "五粮液", ["审计报告"], "审计报告"))
time.sleep(0.5)
all_results.extend(find_and_dl("000333", "美的集团", ["审计报告"], "审计报告"))
time.sleep(0.5)

# ---------- Direct URL downloads from known sources ----------
print("\n--- Direct Downloads from Known Sources ---")

# 12. 国家统计局统计公报 - try various URLs
print("\n--- 12. 统计公报 ---")
stats_urls = [
    ("https://www.stats.gov.cn/sj/zxfb/202302/t20230228_1919005.html", "stats_gb_2022.html"),
    ("https://www.stats.gov.cn/english/PressRelease/202302/t20230227_1919037.html", "stats_gb_2022_en.html"),
]
for url, fname in stats_urls:
    curl(url, fname)
    # Parse for PDF/doc links
    path = os.path.join(RAW, fname)
    if os.path.exists(path):
        with open(path, 'r', encoding='utf-8', errors='ignore') as f:
            html = f.read()
        links = re.findall(r'href=[\'"]([^\'"]*\.(pdf|doc|docx))[\'"]', html, re.IGNORECASE)
        for i, (link, _) in enumerate(links[:3]):
            if not link.startswith('http'):
                link = urllib.parse.urljoin(url, link)
            curl(link, f"stats_communique_2022_{i}.pdf")
        os.remove(path)

# 13. 产业政策文件
print("\n--- 13. 产业政策 ---")
# Try to find industry policy PDFs
policy_urls = [
    # State Council industry policy
    ("http://www.gov.cn/zhengce/content/202205/content_5603674.htm", "gov_policy_new_energy.html"),
]
for url, fname in policy_urls:
    curl(url, fname)
    path = os.path.join(RAW, fname)
    if os.path.exists(path):
        with open(path, 'r', encoding='utf-8', errors='ignore') as f:
            html = f.read()
        links = re.findall(r'href=[\'"]([^\'"]*\.(pdf|doc|docx))[\'"]', html, re.IGNORECASE)
        for i, (link, _) in enumerate(links[:3]):
            if not link.startswith('http'):
                link = urllib.parse.urljoin(url, link)
            curl(link, f"industry_policy_{i}.pdf")
        os.remove(path)

# 14. 金融监管规章制度 - CSRC
print("\n--- 14. 金融监管规章制度 ---")
# Try CSRC regulatory document pages
csrc_urls = [
    ("https://www.csrc.gov.cn/csrc/c100028/c1000394/content.shtml", "csrc_regulations.html"),
]
for url, fname in csrc_urls:
    curl(url, fname, "https://www.csrc.gov.cn/")
    path = os.path.join(RAW, fname)
    if os.path.exists(path):
        with open(path, 'r', encoding='utf-8', errors='ignore') as f:
            html = f.read()
        links = re.findall(r'href=[\'"]([^\'"]*\.pdf)[\'"]', html, re.IGNORECASE)
        for i, (link,) in enumerate(links[:5]):
            if not link.startswith('http'):
                link = urllib.parse.urljoin(url, link)
            curl(link, f"csrc_reg_{i}.pdf", "https://www.csrc.gov.cn/")
        os.remove(path)

# 15. 行业白皮书 - Try to find industry white papers
print("\n--- 15. 行业白皮书 ---")
# Try CBIRC annual report/white paper
cbirc_urls = [
    ("http://www.cbirc.gov.cn/cn/view/pages/ItemDetail.html?docId=990001", "cbirc_white_paper.html"),
]
for url, fname in cbirc_urls:
    curl(url, fname, "http://www.cbirc.gov.cn/")
    path = os.path.join(RAW, fname)
    if os.path.exists(path):
        with open(path, 'r', encoding='utf-8', errors='ignore') as f:
            html = f.read()
        links = re.findall(r'href=[\'"]([^\'"]*\.pdf)[\'"]', html, re.IGNORECASE)
        for i, (link,) in enumerate(links[:5]):
            if not link.startswith('http'):
                link = urllib.parse.urljoin(url, link)
            curl(link, f"cbirc_doc_{i}.pdf", "http://www.cbirc.gov.cn/")
        os.remove(path)

# Save results
with open(os.path.join(RAW, 'download_results.json'), 'w', encoding='utf-8') as f:
    json.dump({'DOWNLOAD_URLS': all_results}, f, ensure_ascii=False, indent=2)

print(f"\n\nTotal PDFs from eastmoney: {len(all_results)}")
print(f"\nAll files in raw dir:")
for f in sorted(os.listdir(RAW)):
    if f.endswith('.pdf'):
        sz = os.path.getsize(os.path.join(RAW, f))
        print(f"  {f[:60]:60s} {sz//1024:>5d}KB")
