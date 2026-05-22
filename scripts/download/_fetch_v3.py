#!/usr/bin/env python3
"""
下载更多东方财富研报 - 使用之前成功的API方式
"""
import json, os, subprocess, time, re

RAW_DIR = os.path.expanduser("~/finrag/data/raw")
os.makedirs(RAW_DIR, exist_ok=True)

def download_pdf(url, path, referer='https://data.eastmoney.com/report/'):
    cmd = ['curl', '-sL', '-o', path, '--connect-timeout', '15', '--max-time', '30',
           '-H', 'User-Agent: Mozilla/5.0',
           '-H', f'Referer: {referer}',
           url]
    r = subprocess.run(cmd, capture_output=True, timeout=35)
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

# The working eastmoney research report API from before
# Search for 研报 by different topics
categories = [
    ("新股研究报告", "xingu"),
    ("行业深度研究", "hangye"),
    ("策略研究报告", "celue"),
    ("宏观研究报告", "hongguan"), 
    ("公司深度研究", "gongsichendu"),
    ("债券研究报告", "zhaiquan"),
    ("基金研究报告", "jijinyj"),
    ("投资价值分析", "jiazhi"),
    ("年报点评", "nianbao_dp"),
    ("首次覆盖", "shouci"),
]

# For the research report API without keywords (broader search)
# The API without keyword returns all reports
print("=== 直接搜索各类研报(无keyword) ===")
for page in range(1, 4):  # Get 3 pages
    print(f"\nPage {page}:")
    url = f'https://np-anotice-stock.eastmoney.com/api/security/ann?sr=-1&page_size=5&page_index={page}&ann_type=REPORT&f_node=0&s_node=0'
    r = subprocess.run(['curl', '-s', '--connect-timeout', '10', '--max-time', '15',
                        '-H', 'User-Agent: Mozilla/5.0',
                        '-H', f'Referer: https://data.eastmoney.com/report/',
                        url],
                       capture_output=True, text=True, timeout=20)
    try:
        data = json.loads(r.stdout)
        items = data.get('data', {}).get('list', [])
        print(f"  共 {len(items)} 条")
        for item in items:
            title = item.get('title', '') or item.get('art_code', '') or ''
            art_code = item.get('art_code', '')
            if art_code:
                pdf_url = f'https://np-anotice-stock.eastmoney.com/api/security/ann/{art_code}/pdf'
                safe_title = re.sub(r'[^a-zA-Z0-9\u4e00-\u9fff_-]', '_', title[:40])
                fname = f"em_{safe_title}_{art_code[-8:]}.pdf"
                size = download_pdf(pdf_url, os.path.join(RAW_DIR, fname))
                if size:
                    chars = check_chars(os.path.join(RAW_DIR, fname))
                    print(f"  ✅ {fname[:50]:50s} ({size//1024}KB, {chars}字)")
                else:
                    print(f"  ❌ {title[:40]}")
                time.sleep(0.5)
    except Exception as e:
        print(f"  Error: {e}")

# Now try with keywords for more specific types
print("\n\n=== 关键词搜索各类研报 ===")
for kw, prefix in categories:
    print(f"\n  [{prefix}] {kw}")
    url = f'https://np-anotice-stock.eastmoney.com/api/security/ann?sr=-1&page_size=5&page_index=1&ann_type=REPORT&f_node=0&s_node=0&keyword={kw}'
    r = subprocess.run(['curl', '-s', '--connect-timeout', '10', '--max-time', '15',
                        '-H', 'User-Agent: Mozilla/5.0',
                        '-H', f'Referer: https://data.eastmoney.com/report/',
                        url],
                       capture_output=True, text=True, timeout=20)
    try:
        data = json.loads(r.stdout)
        items = data.get('data', {}).get('list', [])
        print(f"  共 {len(items)} 条")
        for item in items[:3]:
            title = item.get('title', '') or ''
            art_code = item.get('art_code', '')
            if art_code:
                pdf_url = f'https://np-anotice-stock.eastmoney.com/api/security/ann/{art_code}/pdf'
                safe_title = re.sub(r'[^a-zA-Z0-9\u4e00-\u9fff_-]', '_', title[:30])
                fname = f"em_{prefix}_{safe_title}_{art_code[-8:]}.pdf"
                size = download_pdf(pdf_url, os.path.join(RAW_DIR, fname))
                if size:
                    chars = check_chars(os.path.join(RAW_DIR, fname))
                    print(f"  ✅ {fname[:50]:50s} ({size//1024}KB, {chars}字)")
                else:
                    print(f"  ❌ {title[:30]}")
                time.sleep(0.5)
    except Exception as e:
        print(f"  Error: {e}")

# Summary
print("\n\n" + "=" * 60)
print("📊 最终汇总 - 全部PDF字数检查")
print("=" * 60)
all_pdfs = sorted([f for f in os.listdir(RAW_DIR) if f.endswith('.pdf')])
categories = {}
for f in all_pdfs:
    path = os.path.join(RAW_DIR, f)
    chars = check_chars(path) if os.path.getsize(path) < 10*1024*1024 else 999999
    flag = "✅" if chars >= 5000 else ("⚠️" if chars >= 1000 else "❌")
    print(f"  {flag} {f[:60]:60s} → {chars:>6d}字")
    
    # Categorize
    if 'annual' in f.lower() or '年报' in f:
        cat = '年报'
    elif 'PBOC' in f or '央行' in f or 'pboc' in f:
        cat = '央行报告'
    elif '监管' in f or '问询' in f:
        cat = '监管函件'
    elif 'cpi' in f.lower() or 'test' in f.lower():
        cat = '测试/宏观经济'
    elif 'csrc' in f.lower() or '证监会' in f or 'nfra' in f.lower():
        cat = '监管法规'
    elif '可转债' in f or '可转换' in f:
        cat = '可转债'
    elif 'gov_' in f or 'white_paper' in f:
        cat = '政策/白皮书'
    else:
        cat = '研报'
    categories.setdefault(cat, 0)
    categories[cat] += 1

print(f"\n总计: {len(all_pdfs)} 个PDF")
for cat, cnt in sorted(categories.items()):
    print(f"  {cat}: {cnt}个")
