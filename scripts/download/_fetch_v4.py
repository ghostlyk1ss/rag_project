#!/usr/bin/env python3
"""
更系统地搜集各类金融文档PDF
尝试: 带Cookie的巨潮API + 上交所/深交所发行文件 + 东方财富其他数据
"""
import json, os, subprocess, time, re, sys

RAW_DIR = os.path.expanduser("~/finrag/data/raw")
os.makedirs(RAW_DIR, exist_ok=True)

def curl_with_cookies(url, data=None, output=None, timeout=30, cookie_file=None, referer=None):
    cmd = ['curl', '-sL', '--connect-timeout', '15', '--max-time', str(timeout),
           '-H', 'User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36']
    if cookie_file:
        cmd += ['-b', cookie_file, '-c', cookie_file]
    if referer:
        cmd += ['-H', f'Referer: {referer}']
    if data:
        cmd += ['-H', 'Content-Type: application/x-www-form-urlencoded',
                '-H', 'X-Requested-With: XMLHttpRequest',
                '--data-raw', data]
    if output:
        cmd += ['-o', output]
    cmd.append(url)
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout+5)
        return r.stdout
    except:
        return ""

def download(url, filename, timeout=30, referer=None):
    path = os.path.join(RAW_DIR, filename)
    cmd = ['curl', '-sL', '-o', path, '--connect-timeout', '15', '--max-time', str(timeout),
           '-H', 'User-Agent: Mozilla/5.0']
    if referer:
        cmd += ['-H', f'Referer: {referer}']
    cmd.append(url)
    subprocess.run(cmd, capture_output=True, timeout=timeout+5)
    if os.path.exists(path) and os.path.getsize(path) > 5000:
        return os.path.getsize(path)
    return 0

def check_chars(path):
    r = subprocess.run(['python3', '-c',
        f'import pymupdf; doc=pymupdf.open("{path}"); print(sum(len(page.get_text()) for page in doc)); doc.close()'],
        capture_output=True, text=True, timeout=15)
    try:
        return int(r.stdout.strip())
    except:
        return 0

# ============================================================
# Strategy 1: 东方财富 - 股票公告搜索(不同分类)
# ============================================================
print("=" * 60)
print("📡 [策略1] 东方财富 - 搜索可转债/新股/基金公告")
print("=" * 60)

# Search for IPO prospectuses via stock code (known IPOs)
# 招股书相关的公告类型
stock_targets = [
    # (stock_code, stock_name, label)
    ("688981", "中芯国际", "招股书"),
    ("300750", "宁德时代", "招股书"),
    ("600036", "招商银行", "可转债"),
    ("601166", "兴业银行", "可转债"),
    ("000858", "五粮液", "招股书"),
]

for code, name, label in stock_targets:
    print(f"\n  {name}({code}) - {label}:")
    # 东方财富公告API
    url = f'https://np-anotice-stock.eastmoney.com/api/security/ann?sr=-1&page_size=10&page_index=1&ann_type=ALL&stock_list={code}&f_node=0&s_node=0'
    r = curl_with_cookies(url, referer='https://data.eastmoney.com/notices/')
    try:
        data = json.loads(r)
        items = data.get('data', {}).get('list', [])
        for item in items[:5]:
            title = item.get('title_ch', '')[:70]
            art_code = item.get('art_code', '')
            # Try to download
            if art_code:
                pdf_url = f'https://np-anotice-stock.eastmoney.com/api/security/ann/{art_code}/pdf'
                safe = re.sub(r'[^\u4e00-\u9fff_a-zA-Z0-9]', '_', title[:30])
                fname = f"em_{name}_{safe}_{art_code[-8:]}.pdf"
                sz = download(pdf_url, fname, referer='https://data.eastmoney.com/notices/')
                if sz:
                    ch = check_chars(os.path.join(RAW_DIR, fname))
                    print(f"  ✅ {title[:55]:55s} ({sz//1024}KB/{ch}字)")
                else:
                    print(f"  ❌ {title[:55]}")
            time.sleep(0.5)
    except Exception as e:
        print(f"  Error: {e}")

# ============================================================
# Strategy 2: 巨潮资讯网 - 带Cookie+Referer
# ============================================================
print("\n" + "=" * 60)
print("📡 [策略2] 巨潮资讯网 - 带Cookie方式")
print("=" * 60)

COOKIE_FILE = '/tmp/cninfo_cookies.txt'

# Step 1: Get initial session cookie
print("  获取Session Cookie...")
curl_with_cookies('https://www.cninfo.com.cn', cookie_file=COOKIE_FILE)

# Step 2: Try search with cookie
print("\n  搜索特定文档类型:")
categories_to_search = [
    ("招股说明书（申报稿）", "zhaogu"),
    ("可转换公司债券募集说明书", "kezhuan"),
    ("重大资产重组报告书", "chongzu"),
    ("基金招募说明书", "jijin_zhaomu"),
    ("信用评级报告", "pingji"),
    ("资产评估报告", "pinggu"),
    ("上市公司配股说明书", "peigu"),
    ("上市公司增发说明书", "zengfa"),
]

for kw, prefix in categories_to_search:
    data = f'stock=&plate=&category=&trade=&column=&keyword={kw}&pageNum=1&pageSize=5&tabName=fulltext&seDate=2024-01-01~2026-05-08&sortName=filingDate&sortType=desc&isHLtitle=true'
    resp = curl_with_cookies('https://www.cninfo.com.cn/new/hisAnnouncement/query', 
                            data=data, cookie_file=COOKIE_FILE,
                            referer='https://www.cninfo.com.cn/new/disclosure')
    try:
        d = json.loads(resp)
        total = d.get('totalRecordNum', 0)
        anns = d.get('announcements') or []
        print(f"  [{prefix:10s}] {total}条结果", end="")
        if anns:
            for a in anns[:2]:
                t = a.get('announcementTitle', '')[:50]
                url = a.get('adjunctUrl', '') or ''
                print(f"\n    {t}")
                print(f"    url={url[:60]}")
                if url and url.endswith('.PDF'):
                    dl_url = f'https://www.cninfo.com.cn/new/disclosure/{url}'
                    safe_t = re.sub(r'[^\u4e00-\u9fff_a-zA-Z0-9]', '_', t[:20])
                    fname = f"cninfo_{prefix}_{safe_t}.pdf"
                    sz = download(dl_url, fname, referer='https://www.cninfo.com.cn/new/disclosure')
                    if sz:
                        ch = check_chars(os.path.join(RAW_DIR, fname))
                        print(f"      ✅ {sz//1024}KB/{ch}字")
                    else:
                        print(f"      ❌ 下载失败")
                time.sleep(0.5)
        else:
            print()
    except Exception as e:
        print(f"  [{prefix:10s}] Error: {e}")
    time.sleep(1)

# ============================================================
# Strategy 3: 上交所(SSE) IPO文件搜索
# ============================================================
print("\n" + "=" * 60)
print("📡 [策略3] 上交所/深交所 IPO文件")
print("=" * 60)

# SSE disclosure search
# https://query.sse.com.cn/security/stock/queryBulletinStockSearch.do?...
print("  SSE 科创板公告搜索...")
resp = curl_with_cookies(
    'https://query.sse.com.cn/security/stock/queryBulletinStockSearch.do?jsonCallBack=&searchTitle=招股说明书&securityCode=&type=&pageHelp.pageSize=5&pageHelp.pageNo=1&pageHelp.beginDate=2024-01-01&pageHelp.endDate=2026-05-08&isPagination=true&_=1712345678',
    referer='https://www.sse.com.cn/disclosure/listedinfo/announcement/',
)

# Try with different format (SSE uses different endpoints)
print("\n  SSE 科创板发行审核...")
resp = curl_with_cookies(
    'https://kcb.sse.com.cn/ipo/init?pageSize=5&pageNo=1',
    referer='https://kcb.sse.com.cn/',
)
print(f"  响应长度: {len(resp)}")
if '招股' in resp or 'prospectus' in resp.lower():
    # Find PDF links
    pdfs = re.findall(r'(https?://[^"\']*\.(?:pdf|PDF)[^"\']*)', resp)
    for url in pdfs[:5]:
        print(f"  ✅ Found PDF: {url[:100]}")

print("\n  深交所创业板发行审核...")
resp = curl_with_cookies(
    'https://listing.szse.cn/api/ras/query/announce/items?pageSize=5&pageNum=1&plate=CHN_GEM',
    referer='https://listing.szse.cn/',
    data='announceType=ZGS',
)
print(f"  响应长度: {len(resp)}")
if resp:
    try:
        d = json.loads(resp)
        print(f"  结果: {json.dumps(d, ensure_ascii=False)[:500]}")
    except:
        print(f"  Raw: {resp[:300]}")

# ============================================================
# Strategy 4: 尝试通过关键字搜索知名长文档
# ============================================================
print("\n" + "=" * 60)
print("📡 [策略4] 直接搜索知名长文档PDF")
print("=" * 60)

# Try to find 招股说明书(完整版) for specific companies
# These are usually hosted on cninfo or issuer websites
known_pdfs = {
    "中芯国际招股书": "https://www.sse.com.cn/disclosure/listedinfo/announcement/c/2020-07-06/688981_20200706_1.pdf",
    "宁德时代招股书": "https://www.szse.cn/api/disc/announcement/300750/招股说明书.pdf",
}

for label, url in known_pdfs.items():
    print(f"  {label} ...")
    sz = download(url, f"{label}.pdf")
    if sz:
        ch = check_chars(os.path.join(RAW_DIR, f"{label}.pdf"))
        print(f"    ✅ {sz//1024}KB/{ch}字")
    else:
        print(f"    ❌ 下载失败")

# ============================================================
# Summary
# ============================================================
print("\n\n" + "=" * 60)
print("📊 最终汇总")
print("=" * 60)

new_files = sorted([f for f in os.listdir(RAW_DIR) if f.endswith('.pdf') and 
                    (f.startswith('em_') or f.startswith('cninfo_') or '中芯' in f or '宁德' in f)])

for f in new_files:
    ch = check_chars(os.path.join(RAW_DIR, f))
    flag = "✅" if ch >= 5000 else ("⚠️" if ch >= 1000 else "❌")
    print(f"  {flag} {f[:60]:60s} → {ch:>6d}字")

print(f"\n总计当前PDF数: {len([f for f in os.listdir(RAW_DIR) if f.endswith('.pdf')])}")
