#!/usr/bin/env python3
"""Fetch diverse financial documents from multiple sources."""
import json, os, sys, subprocess, time

RAW_DIR = os.path.expanduser("~/finrag/data/raw")
os.makedirs(RAW_DIR, exist_ok=True)

def curl(url, data=None, timeout=20):
    cmd = ['curl', '-s', '--connect-timeout', '10', '--max-time', str(timeout),
           '-H', 'User-Agent: Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36']
    if data:
        cmd += ['-H', 'Content-Type: application/x-www-form-urlencoded',
                '--data-raw', data]
    cmd.append(url)
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout+5)
    return r.stdout

def cninfo_search(keyword, stock='', page=1, pagesize=10, date_from='2024-01-01', date_to='2026-05-08'):
    """Search cninfo.com.cn for announcements."""
    data = f'stock={stock}&plate=&category=&trade=&column=&keyword={keyword}&pageNum={page}&pageSize={pagesize}&tabName=fulltext&seDate={date_from}~{date_to}&sortName=createdate&sortType=desc&isHLtitle=true'
    result = curl('https://www.cninfo.com.cn/new/hisAnnouncement/query', data)
    try:
        return json.loads(result)
    except:
        return None

def download_pdf(adjunct_url, filename):
    """Download PDF from cninfo."""
    url = f'https://www.cninfo.com.cn/new/disclosure/{adjunct_url}'
    path = os.path.join(RAW_DIR, filename)
    r = subprocess.run(['curl', '-sL', '-o', path,
                        '-H', 'User-Agent: Mozilla/5.0',
                        '--connect-timeout', '10', '--max-time', '30',
                        url],
                       capture_output=True, text=True, timeout=35)
    if os.path.exists(path) and os.path.getsize(path) > 1000:
        size = os.path.getsize(path)
        print(f"  ✅ Downloaded: {filename} ({size/1024:.0f}KB)")
        return True
    else:
        print(f"  ❌ Failed: {filename}")
        if os.path.exists(path) and os.path.getsize(path) < 100:
            os.remove(path)
        return False

def check_text_length(pdf_path):
    """Check text length using PyMuPDF."""
    result = subprocess.run([
        'python3', '-c',
        f'import pymupdf; doc=pymupdf.open("{pdf_path}"); print(sum(len(page.get_text()) for page in doc)); doc.close()'
    ], capture_output=True, text=True, timeout=15)
    try:
        return int(result.stdout.strip())
    except:
        return 0

# =====================================================
# 1. SEARCH AND DOWNLOAD BY DOCUMENT TYPE
# =====================================================
print("=" * 60)
print("📡 开始批量搜集各类金融文档")
print("=" * 60)

# Define search queries and what we're looking for
doc_types = [
    # (label, keyword, stock_filter, filename_prefix)
    # --- 招股说明书 (IPO Prospectus) ---
    ("招股说明书_科创板", "首次公开发行股票科创板招股说明书", "", "zhaogu_kechuang"),
    ("招股说明书_创业板", "首次公开发行股票并在创业板上市招股说明书", "", "zhaogu_chuangyeban"),
    ("招股说明书_主板", "首次公开发行股票招股说明书", "", "zhaogu_zhuban"),

    # --- 可转债 ---
    ("可转债_募集说明书", "公开发行可转换公司债券募集说明书", "", "kezhuanzhai"),

    # --- 重组 ---
    ("重大资产重组", "重大资产重组报告书", "", "chongzu"),

    # --- 配股/增发 ---
    ("配股说明书", "配股说明书", "", "peigu"),
    ("增发说明书", "非公开发行股票预案", "", "zengfa"),

    # --- 基金 ---
    ("基金招募书", "招募说明书", "", "jijin_zhaomu"),

    # --- 评级报告 ---
    ("信用评级", "信用评级报告", "", "xinyong_pingji"),

    # --- 资产评估 ---
    ("资产评估", "资产评估报告", "", "zichan_pinggu"),

    # --- 法律意见书 ---
    ("法律意见书", "法律意见书", "", "falv_yijian"),

    # --- 股权激励 ---
    ("股权激励", "股权激励计划", "", "guquan_jili"),
]

# Try to find long documents (>50 pages) with varied types
for label, keyword, stock, prefix in doc_types:
    print(f"\n--- 搜索: {label} ---")
    result = cninfo_search(keyword, stock=stock, page=1, pagesize=15)

    if not result:
        print(f"  API failed")
        continue

    total = result.get('totalRecordNum', 0)
    print(f"  共 {total} 条结果")

    announcements = result.get('announcements') or []
    dl_count = 0
    for a in announcements:
        title = a.get('announcementTitle', '')[:80]
        url = a.get('adjunctUrl', '') or ''
        sec_name = a.get('secName', '') or ''
        sec_code = a.get('secCode', '') or ''

        if not url or not url.endswith('.PDF'):
            continue

        # Sanitize filename
        safe_name = ''.join(c if c.isalnum() or c in '_-.' else '_' for c in title[:60])
        filename = f"{prefix}_{sec_code}_{sec_name}_{safe_name}.pdf"

        if download_pdf(url, filename):
            dl_count += 1
            if dl_count >= 3:
                break
        time.sleep(0.5)

print("\n" + "=" * 60)
print("📊 下载完成！检查字数...")
print("=" * 60)

# Check text lengths
for f in sorted(os.listdir(RAW_DIR)):
    if f.endswith('.pdf') and (f.startswith('zhaogu_') or f.startswith('kezhuanzhai_') or 
                               f.startswith('chongzu_') or f.startswith('peigu_') or
                               f.startswith('zengfa_') or f.startswith('jijin_') or
                               f.startswith('xinyong_') or f.startswith('zichan_') or
                               f.startswith('falv_') or f.startswith('guquan_')):
        path = os.path.join(RAW_DIR, f)
        if os.path.exists(path):
            chars = check_text_length(path)
            flag = "✅" if chars >= 10000 else "⚠️"
            print(f"  {flag} {f[:55]:55s} → {chars:>6d}字")
