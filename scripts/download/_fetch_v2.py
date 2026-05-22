#!/usr/bin/env python3
"""
批量搜集各类财务文档的PDF下载链接并下载
尝试多个数据源
"""
import json, os, subprocess, sys, time, re

RAW_DIR = os.path.expanduser("~/finrag/data/raw")
os.makedirs(RAW_DIR, exist_ok=True)

def curl(url, timeout=30, data=None, referer=None):
    cmd = ['curl', '-sL', '--connect-timeout', '15', '--max-time', str(timeout),
           '-H', 'User-Agent: Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36']
    if referer:
        cmd += ['-H', f'Referer: {referer}']
    if data:
        cmd += ['-H', 'Content-Type: application/x-www-form-urlencoded', '--data-raw', data]
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
        size = os.path.getsize(path)
        print(f"  ✅ {filename} ({size/1024:.0f}KB)")
        return True
    elif os.path.exists(path):
        size = os.path.getsize(path)
        print(f"  ⚠️ {filename} (only {size} bytes)")
        os.remove(path)
        return False
    return False

def check_chars(pdf_name):
    path = os.path.join(RAW_DIR, pdf_name)
    if not os.path.exists(path):
        return 0
    try:
        r = subprocess.run(['python3', '-c',
            f'import pymupdf; doc=pymupdf.open("{path}"); print(sum(len(page.get_text()) for page in doc)); doc.close()'],
            capture_output=True, text=True, timeout=15)
        return int(r.stdout.strip())
    except:
        return 0

# ============================================================
# Source 1: 东方财富 - 研报(不同类型的研报:策略/行业/公司/新股)
# ============================================================
print("=" * 60)
print("📡 源1: 东方财富研报API - 搜集不同类型")
print("=" * 60)

# Search for different types of reports
search_kw = [
    ("新股研究", "xingu"),
    ("行业深度", "hangye"),
    ("策略研究", "celue"),
    ("宏观研究", "hongguan"),
    ("公司深度", "gongsi"),
    ("债券研究", "zhaiquan"),
    ("基金研究", "jijinyanjiu"),
]

for kw, prefix in search_kw:
    print(f"\n  搜索: {kw}")
    resp = curl(
        f'https://np-anotice-stock.eastmoney.com/api/security/ann?sr=-1&page_size=10&page_index=1&ann_type=REPORT&f_node=0&s_node=0&keyword={kw}',
        referer='https://data.eastmoney.com/report/',
    )
    try:
        data = json.loads(resp)
        items = data.get('data', {}).get('list', [])
        print(f"  找到 {len(items)} 条")
        dl_count = 0
        for item in items:
            if dl_count >= 2:
                break
            title = item.get('title', item.get('art_code', ''))[:60]
            art_code = item.get('art_code', '')
            if not art_code:
                continue
            # 东财研报PDF URL
            pdf_url = f'https://np-anotice-stock.eastmoney.com/api/security/ann/{art_code}/pdf'
            filename = f"eastmoney_{prefix}_{''.join(c if c.isalnum() else '_' for c in title[:30])}.pdf"
            if download(pdf_url, filename, referer='https://data.eastmoney.com/report/'):
                dl_count += 1
            time.sleep(0.3)
    except Exception as e:
        print(f"  Error: {e}")

# ============================================================
# Source 2: 试试深交所直接搜索
# ============================================================
print("\n" + "=" * 60)
print("📡 源2: 其他渠道搜索PDF")
print("=" * 60)

# Try to get the 统计公报 from stats.gov.cn
# The 2024 统计公报 PDF is usually at a URL like:
# https://www.stats.gov.cn/sj/zxfb/202402/t20240229_1947915.html
# Let's try to find the direct PDF link

# Try to find downloadable financial documents via DuckDuckGo
search_terms = [
    "招股说明书 pdf site:cninfo.com.cn",
    "募集说明书 pdf site:cninfo.com.cn", 
    "基金招募说明书 pdf",
    "国家统计局 统计公报 pdf",
    "证监会 上市公司监管 pdf",
]

for term in search_terms:
    print(f"\n  搜索: {term}")
    resp = curl(f'https://html.duckduckgo.com/html/?q={term.replace(" ", "+")}')
    # Find PDF links
    pdfs = re.findall(r'href="(https?://[^"]*\.pdf[^"]*)"', resp)
    for url in pdfs[:3]:
        print(f"    Found: {url[:100]}")

# ============================================================
# Source 3: 从已知的文档索引中找 - 直接下载已知文档
# ============================================================
print("\n" + "=" * 60)
print("📡 源3: 尝试下载特定知名文档")
print("=" * 60)

# 这些是已知存在的PDF链接，尝试直接下载
known_docs = [
    # 国家统计局2024年统计公报 (通常很大)
    # 试试找统计公报的PDF链接
    ("https://www.stats.gov.cn/sj/zxfb/202402/t20240229_1947915.html", "stats_2024_公报"),
    # 央行金融稳定报告
    ("http://www.pbc.gov.cn/goutongjiaoliu/113456/113469/5430948/index.html", "pboc_jinrongwending"),
]

# Try the SSE for bond prospectus
print("\n  尝试 中国债券信息网...")
resp = curl('https://www.chinabond.com.cn/cb/')
# Find any PDF links
pdfs = re.findall(r'href="(https?://[^"]*\.(?:pdf|PDF)[^"]*)"', resp)
for url in pdfs[:5]:
    print(f"    Found: {url[:100]}")

# ============================================================
# Source 4: 直接从词条搜索已知财报PDF
# ============================================================
print("\n" + "=" * 60)
print("📡 源4: 已知文档直接搜索")
print("=" * 60)

# 有些公司的年度报告PDF可以直接在它们的投资者关系页面找到
# 试试几个知名公司的直接链接
try_downloads = [
    # 这些是可能存在的通用链接
]

# ============================================================
# 总结
# ============================================================
print("\n\n" + "=" * 60)
print("📊 下载汇总 - 检查字数")
print("=" * 60)

all_pdfs = sorted([f for f in os.listdir(RAW_DIR) if f.endswith('.pdf') and f.startswith('eastmoney_')])
for f in all_pdfs:
    chars = check_chars(f)
    flag = "✅" if chars >= 3000 else "⚠️"
    print(f"  {flag} {f[:60]:60s} → {chars:>6d}字")

print(f"\n总计新增: {len(all_pdfs)} 个PDF")
print(f"原始文档数: {len([f for f in os.listdir(RAW_DIR) if f.endswith('.pdf')])} 个")
