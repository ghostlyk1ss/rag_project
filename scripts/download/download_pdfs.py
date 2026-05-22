#!/usr/bin/env python3
"""
Download Chinese financial PDFs from various sources.
"""
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
import urllib.parse
import urllib.error
import ssl
import http.cookiejar

ssl._create_default_https_context = ssl._create_unverified_context

RAW_DIR = os.path.expanduser("~/finrag/data/raw")
os.makedirs(RAW_DIR, exist_ok=True)

# ---------- Utility ----------
def download(url, filename, max_size=50*1024*1024, timeout=30):
    """Download a URL to a file. Returns file size or -1 on failure."""
    path = os.path.join(RAW_DIR, filename)
    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept": "text/html,application/pdf,*/*",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            }
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read()
            if len(data) < 100:
                print(f"  ✗ {filename}: too small ({len(data)} bytes)")
                return -1
            if len(data) > max_size:
                print(f"  ✗ {filename}: too large ({len(data)} bytes)")
                return -1
            with open(path, 'wb') as f:
                f.write(data)
            print(f"  ✓ {filename}: {len(data)} bytes")
            return len(data)
    except Exception as e:
        print(f"  ✗ {filename}: {e}")
        return -1

def curl_download(url, filename, timeout=30):
    """Download using curl."""
    path = os.path.join(RAW_DIR, filename)
    cmd = ["curl", "-sL", "--max-time", str(timeout), "-o", path, url]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout+5)
        if os.path.exists(path):
            size = os.path.getsize(path)
            if size >= 100:
                print(f"  ✓ {filename}: {size} bytes")
                return size
            else:
                os.remove(path)
                print(f"  ✗ {filename}: too small ({size} bytes)")
                return -1
        else:
            print(f"  ✗ {filename}: download failed")
            return -1
    except Exception as e:
        print(f"  ✗ {filename}: {e}")
        return -1


def curl_download_with_cookies(url, filename, referer="https://www.cninfo.com.cn", timeout=30):
    """Download using curl with cookies."""
    path = os.path.join(RAW_DIR, filename)
    cmd = [
        "curl", "-sL", "--max-time", str(timeout),
        "-H", "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "-H", f"Referer: {referer}",
        "-H", "Accept-Language: zh-CN,zh;q=0.9",
        "-o", path, url
    ]
    # First get cookies
    cookie_jar = "/tmp/cookies.txt"
    subprocess.run(["curl", "-sL", "-c", cookie_jar, "https://www.cninfo.com.cn", "-o", "/dev/null"],
                   capture_output=True, timeout=15)
    if os.path.exists(cookie_jar):
        cmd.extend(["-b", cookie_jar])
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout+5)
        if os.path.exists(path):
            size = os.path.getsize(path)
            if size >= 100:
                print(f"  ✓ {filename}: {size} bytes")
                return size
            else:
                os.remove(path)
                print(f"  ✗ {filename}: too small ({size} bytes)")
                return -1
        else:
            print(f"  ✗ {filename}: download failed")
            return -1
    except Exception as e:
        print(f"  ✗ {filename}: {e}")
        return -1


# ---------- Source 1: National Bureau of Statistics (stats.gov.cn) ----------
def download_stats_pdfs():
    """Download statistical communiques from stats.gov.cn"""
    print("\n=== Source: stats.gov.cn (国家统计局) ===")
    
    # Try to find the latest 统计公报 PDF
    # 2024年国民经济和社会发展统计公报
    urls = [
        # Try to find pdf download from stats page
        ("https://www.stats.gov.cn/sj/zxfb/202502/t20250228_1959047.html", 
         "stats_2024_communique.html", "temp"),
    ]
    
    for url, fname, dtype in urls:
        path = os.path.join(RAW_DIR, fname)
        curl_download(url, fname)
        # Parse html for pdf/doc links
        if os.path.exists(path):
            with open(path, 'r', encoding='utf-8', errors='ignore') as f:
                html = f.read()
            # Look for PDF/document links
            links = re.findall(r'href=[\'"]([^\'"]*\.(pdf|doc|docx))[\'"]', html, re.IGNORECASE)
            links2 = re.findall(r'href=[\'"]([^\'"]*download[^\'"]*)[\'"]', html)
            all_links = [l[0] for l in links] + [l for l in links2]
            for i, link in enumerate(all_links[:3]):
                if not link.startswith('http'):
                    link = urllib.parse.urljoin(url, link)
                fname2 = f"stats_2024_communique_{i}.pdf"
                curl_download(link, fname2)
            os.remove(path)

# ---------- Source 2: EastMoney API ----------
def eastmoney_search(stock, page=1, page_size=20):
    """Search eastmoney API for stock announcements"""
    url = f"http://np-anotice-stock.eastmoney.com/api/security/ann?sr=-1&page_size={page_size}&page_index={page}&ann_type=SHA&stock_list={stock}&f_node=0&s_node=0"
    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0",
                "Referer": "http://data.eastmoney.com/"
            }
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            return data.get('data', {}).get('list', [])
    except Exception as e:
        print(f"  API error: {e}")
        return []

def download_from_eastmoney(art_code, filename):
    """Download a PDF from eastmoney given the art_code"""
    url = f"https://pdf.dfcfw.com/pdf/H2_{art_code}_1.pdf"
    return curl_download(url, filename)

def collect_eastmoney_pdfs():
    """Collect PDFs from eastmoney for various document types"""
    print("\n=== Source: EastMoney (东方财富) ===")
    
    # Different stocks for different document types
    # 招股说明书 - look for recently IPO'd companies
    # Stock codes for recent IPOs
    targets = [
        # (stock_code, company_name, doc_type, keywords_to_match)
        ("688981", "中芯国际", "招股说明书", ["招股说明书", "招股说明"]),
        ("688111", "金山办公", "招股说明书", ["招股说明书"]),
        ("300999", "金龙鱼", "招股说明书", ["招股说明书"]),
        ("688036", "传音控股", "招股说明书", ["招股说明书"]),
        # Annual reports
        ("600519", "贵州茅台", "年度报告", ["年度报告", "年报"]),
        ("000858", "五粮液", "年度报告", ["年度报告", "年报"]),
        ("000333", "美的集团", "年度报告", ["年度报告", "年报"]),
        ("601318", "中国平安", "年度报告", ["年度报告", "年报"]),
        # 审计报告
        ("600519", "贵州茅台", "审计报告", ["审计报告"]),
        ("000858", "五粮液", "审计报告", ["审计报告"]),
        # 股权激励计划
        ("000333", "美的集团", "股权激励", ["股权激励"]),
        ("002415", "海康威视", "股权激励", ["股权激励"]),
        # 可转换债券
        ("600036", "招商银行", "可转债募集", ["可转换", "可转债"]),
        ("601012", "隆基绿能", "可转债募集", ["可转换", "可转债"]),
        # 重大资产重组
        ("000002", "万科A", "重大资产重组", ["重大资产重组"]),
        ("600030", "中信证券", "重大资产重组", ["重大资产重组"]),
        # 法律意见书
        ("601318", "中国平安", "法律意见书", ["法律意见书"]),
        # 资产评估报告
        ("600900", "长江电力", "资产评估", ["资产评估"]),
        # 信用评级报告
        ("600036", "招商银行", "信用评级", ["信用评级"]),
        # 配股说明书
        ("600030", "中信证券", "配股说明书", ["配股说明"]),
        # 基金招募说明书
        ("000001", "平安银行", "基金招募", ["基金招募"]),
    ]

    downloaded = []
    for stock, company, doc_type, keywords in targets:
        print(f"\nSearching {company}({stock}) for {doc_type}...")
        items = eastmoney_search(stock)
        for item in items:
            title = item.get('title_ch', '')
            art_code = item.get('art_code', '')
            notice_date = item.get('notice_date', '')
            
            # Check if title matches any keyword
            if any(kw.lower() in title.lower() for kw in keywords):
                safe_title = re.sub(r'[\\/:*?"<>|]', '_', title)[:80]
                fname = f"eastmoney_{stock}_{doc_type}_{notice_date}_{safe_title}.pdf"
                
                # Check if it's likely a long document (skip short notices)
                title_len = len(title)
                if title_len < 15:
                    continue
                    
                size = download_from_eastmoney(art_code, fname)
                if size > 0:
                    downloaded.append({
                        'url': f"https://pdf.dfcfw.com/pdf/H2_{art_code}_1.pdf",
                        'title': title,
                        'doc_type': doc_type,
                        'source': 'eastmoney',
                        'size': size,
                        'filename': fname
                    })
                time.sleep(0.5)  # Be polite
                if len([d for d in downloaded if d['doc_type'] == doc_type]) >= 2:
                    break
    
    return downloaded


# ---------- Source 3: CSRC (证监会) ----------
def download_csrc_pdfs():
    """Download regulatory documents from CSRC"""
    print("\n=== Source: CSRC (证监会) ===")
    
    # Known CSRC PDF URLs (regulatory rules)
    # These are URLs that host PDF documents on CSRC
    csrc_docs = [
        # 上市公司信息披露管理办法
        ("https://www.csrc.gov.cn/csrc/c100028/c1000394/1000394_attachments/1000542.shtml",
         "csrc_info_disclosure_rules.pdf", "信息披露管理办法"),
    ]
    
    csrc_urls = [
        # Try different CSRC URL patterns for PDFs
        ("http://www.csrc.gov.cn/csrc/c100028/c1000394/1000394_attachments/1000547.shtml",
         "csrc_governance_guidelines.pdf", "公司治理准则"),
        ("http://www.csrc.gov.cn/csrc/c100028/c1000394/1000394_attachments/1000549.shtml",
         "csrc_merger_rules.pdf", "上市公司收购管理办法"),
    ]
    
    for url, fname, dtype in csrc_docs + csrc_urls:
        curl_download(url, fname)
        time.sleep(0.5)
    
    # Try to find PDF links on CSRC pages
    # 证监会发布规章
    base_url = "http://www.csrc.gov.cn/csrc/c100028/common_zfxxgknb.shtml"
    req = urllib.request.Request(
        base_url,
        headers={"User-Agent": "Mozilla/5.0"}
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            html = resp.read().decode('utf-8', errors='ignore')
        pdf_links = re.findall(r'href=[\'"]([^\'"]*\.pdf)[\'"]', html, re.IGNORECASE)
        for i, link in enumerate(pdf_links[:5]):
            if not link.startswith('http'):
                link = urllib.parse.urljoin(base_url, link)
            fname = f"csrc_doc_{i}.pdf"
            curl_download(link, fname)
            time.sleep(0.5)
    except Exception as e:
        print(f"  CSRC listing error: {e}")


# ---------- Source 4: Direct known PDF URLs ----------
def download_known_pdfs():
    """Download from known direct PDF URLs"""
    print("\n=== Source: Known PDF URLs ===")
    
    known_urls = [
        # 产业政策 - 国务院关于促进企业兼并重组的意见
        # Industry policy documents
        # Let's try various known sources
        
        # 金融监管规章制度 - PBOC/CSRC rules
        # 金融控股公司监督管理试行办法
        # Various regulatory documents from known URLs
    ]
    
    # Let's try some well-known Chinese financial PDF URLs
    # These are from various government websites
    
    # National Financial Regulatory Administration (国家金融监督管理总局)
    nfra_urls = [
        ("http://www.nfra.gov.cn/cn/view/pages/ItemDetail.html?docId=100001", 
         "nfra_regulation.pdf", "金融监管规章"),
    ]
    
    # Try to get useful PDFs from known sources
    for url, fname, dtype in nfra_urls:
        curl_download(url, fname)
        time.sleep(0.5)


# ---------- Source 5: Try to find PDFs from various regulatory sites ----------
def download_regulatory_pdfs():
    """Try to get regulatory PDFs from various sites"""
    print("\n=== Source: Other Regulatory Sites ===")
    
    # Try China Banking and Insurance Regulatory Commission (CBIRC)
    # URL patterns
    cbirc_urls = [
        ("http://www.cbirc.gov.cn/cn/view/pages/ItemDetail.html?docId=990000", 
         "cbirc_banking_rules.pdf", "银行监管规章"),
        ("http://www.cbirc.gov.cn/cn/view/pages/ItemDetail.html?docId=990001", 
         "cbirc_insurance_rules.pdf", "保险监管规章"),
    ]
    
    for url, fname, dtype in cbirc_urls:
        curl_download(url, fname)
        time.sleep(0.5)


# ---------- Main ----------
def main():
    results = {
        'DOWNLOAD_URLS': []
    }
    
    # Try eastmoney approach (most promising)
    em_results = collect_eastmoney_pdfs()
    results['DOWNLOAD_URLS'].extend(em_results)
    
    # Try stats.gov.cn
    download_stats_pdfs()
    
    # Try CSRC
    download_csrc_pdfs()
    
    # Try regulatory sites
    download_regulatory_pdfs()
    
    # Try known PDF URLs
    download_known_pdfs()
    
    # Save results
    with open(os.path.join(RAW_DIR, 'download_urls.json'), 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    
    print(f"\n\nTotal downloaded: {len(results['DOWNLOAD_URLS'])} PDFs")
    for r in results['DOWNLOAD_URLS']:
        print(f"  {r['doc_type']}: {r['title'][:60]}")
    
    # List all downloaded files
    print("\n=== All files in raw dir ===")
    for f in sorted(os.listdir(RAW_DIR)):
        if f.endswith('.pdf'):
            path = os.path.join(RAW_DIR, f)
            size = os.path.getsize(path)
            print(f"  {f[:60]:60s} {size/1024:7.1f} KB")

if __name__ == '__main__':
    main()
