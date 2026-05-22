#!/usr/bin/env python3
"""Analyze all PDF documents in ~/finrag/data/raw/ >=5000 chars using pymupdf."""

import os, json, re, sys
import fitz  # pymupdf

RAW_DIR = os.path.expanduser("~/finrag/data/raw")

def get_text_length(pdf_path):
    """Return total text character count."""
    try:
        doc = fitz.open(pdf_path)
        total = sum(len(page.get_text()) for page in doc)
        doc.close()
        return total
    except Exception as e:
        print(f"  ERROR reading {pdf_path}: {e}")
        return 0

def extract_text_page(pdf_path, page_num):
    """Extract text from a specific page (0-indexed)."""
    try:
        doc = fitz.open(pdf_path)
        text = ""
        if page_num < len(doc):
            text = doc[page_num].get_text()
        doc.close()
        return text
    except Exception as e:
        return f"[ERROR: {e}]"

def extract_text_range(pdf_path, start, end):
    """Extract text from pages start to end-1."""
    try:
        doc = fitz.open(pdf_path)
        texts = []
        for i in range(start, min(end, len(doc))):
            texts.append(doc[i].get_text())
        doc.close()
        return "\n".join(texts)
    except Exception as e:
        return f"[ERROR: {e}]"

def search_in_doc(pdf_path, terms):
    """Search for terms in a PDF and return page-level results."""
    try:
        doc = fitz.open(pdf_path)
        results = {}
        for term in terms:
            found_pages = []
            for i, page in enumerate(doc):
                text = page.get_text()
                if term in text:
                    # Find the sentence containing the term
                    lines = text.split('\n')
                    context_lines = [l for l in lines if term in l]
                    found_pages.append({
                        "page": i + 1,
                        "context": context_lines[:3]
                    })
            results[term] = found_pages
        doc.close()
        return results
    except Exception as e:
        return {t: [] for t in terms}

def analyze_document(filepath):
    """Analyze a single PDF document and return structured data."""
    filename = os.path.basename(filepath)
    size = os.path.getsize(filepath)
    text_length = get_text_length(filepath)
    
    print(f"\n{'='*80}")
    print(f"FILE: {filename}")
    print(f"Size: {size:,} bytes | Text: {text_length:,} chars")
    
    if text_length < 5000:
        print(f"SKIP: Too short ({text_length} chars < 5000)")
        return None
    
    # Determine document type from filename
    doc_type = "其他"
    if "zhaogu" in filename or "招股说明书" in filename:
        doc_type = "招股说明书"
    elif "年度报告" in filename:
        if "摘要" in filename:
            doc_type = "年度报告摘要"
        else:
            doc_type = "年度报告"
    elif "annual_report" in filename or "annual report" in filename.lower():
        doc_type = "年度报告"
    elif "chongzu" in filename or "重组" in filename:
        doc_type = "重大资产重组报告书"
    elif "peigu" in filename or "配股说明书" in filename:
        doc_type = "配股说明书"
    elif "研报" in filename or any(kw in filename for kw in ["证券_", "证券.pdf"]):
        doc_type = "研报"
    elif "credit_rating" in filename or "信用评级" in filename:
        doc_type = "信用评级报告"
    elif "policy" in filename or "白皮书" in filename:
        doc_type = "政策白皮书"
    elif "股权激励" in filename:
        doc_type = "股权激励法律意见书"
    elif "法律意见书" in filename:
        doc_type = "法律意见书"
    elif "审计报告" in filename:
        doc_type = "审计报告"
    elif "资产评估报告" in filename:
        doc_type = "资产评估报告"
    elif "kezhuanzhai" in filename or "可转债" in filename:
        doc_type = "可转债受托管理报告"
    elif "问询函" in filename:
        doc_type = "问询函"
    elif "监管函" in filename:
        doc_type = "监管函"
    elif "Monetary_Policy" in filename:
        doc_type = "货币政策报告"
    
    # Read first 2 pages for key info
    first_pages_text = extract_text_range(filepath, 0, 3)
    
    # For long docs, do specific searches
    search_terms_cn = ["营业收入", "净利润", "总资产", "增长率", "业务", "风险"]
    search_results = search_in_doc(filepath, search_terms_cn)
    
    # For English docs
    search_terms_en = ["revenue", "net profit", "total assets", "growth", "business", "risk"]
    search_results_en = search_in_doc(filepath, search_terms_en)
    
    # Extract company name from filename or text
    company = "待识别"
    # Try common patterns
    company_patterns = [
        r'(招商银行|中国平安|贵州茅台|澜起科技|联讯仪器|长裕集团|广东鸿特|陕西能源|海尔智家|隆基绿能|长江电力|特变电工|陇神戎发|福斯特|新华医疗|徐工机械|中矿资源|同享科技)',
        r'(wuliangye|maotai)',
    ]
    
    for pat in company_patterns:
        m = re.search(pat, filename)
        if m:
            company = m.group(1)
            break
    
    # Try to find company in text if not in filename
    if company == "待识别":
        for line in first_pages_text.split('\n')[:50]:
            if any(kw in line for kw in ["公司名称", "发行人", "上市公司", "Company", "股票简称"]):
                company = line.strip()[:60]
                break
    
    # Extract key facts
    key_facts = []
    sections = []
    
    # For long documents, search for key data
    if doc_type in ["招股说明书", "年度报告", "重大资产重组报告书", "配股说明书", "年度报告摘要"]:
        print(f"\n  --- Analyzing {doc_type}: {filename[:50]}...")
        
        # Get first 2 pages
        print(f"\n  FIRST PAGES (0-2):")
        pages_text = []
        try:
            doc = fitz.open(filepath)
            for i in range(min(3, len(doc))):
                pt = doc[i].get_text()
                pages_text.append(pt[:2000])
            doc.close()
        except Exception as e:
            pages_text = [f"[ERROR: {e}]"]
        
        for i, pt in enumerate(pages_text):
            lines = [l.strip() for l in pt.split('\n') if l.strip() and len(l.strip()) > 5]
            print(f"  Page {i}: {' | '.join(lines[:8])}")
        
        # Search results
        for term in search_terms_cn:
            pages = search_results.get(term, [])
            if pages:
                for found in pages[:3]:
                    ctx = " | ".join(found["context"][:2])
                    if len(ctx) > 200:
                        ctx = ctx[:200] + "..."
                    key_facts.append(f"[Page {found['page']}] {term}: {ctx}")
        
        # Try to find table of contents
        toc_text = extract_text_range(filepath, 0, 5)
        for line in toc_text.split('\n'):
            line = line.strip()
            if re.match(r'^第[一二三四五六七八九十]+[章节篇]', line) or re.match(r'^\d+\.\d+', line):
                sections.append(line[:80])
        
        # Also search for specific financial data in English docs
        for term in search_terms_en:
            pages = search_results_en.get(term, [])
            if pages:
                for found in pages[:2]:
                    ctx = " | ".join(found["context"][:2])
                    if len(ctx) > 200:
                        ctx = ctx[:200] + "..."
                    key_facts.append(f"[Page {found['page']}] {term}: {ctx}")
    
    elif doc_type == "研报":
        print(f"\n  --- Analyzing Research Report: {filename[:50]}...")
        # Read pages 0-2
        rp_text = extract_text_range(filepath, 0, min(4, 10))
        lines = [l.strip() for l in rp_text.split('\n') if l.strip() and len(l.strip()) > 5]
        print(f"  Content preview: {' | '.join(lines[:20])}")
        
        # Find key numbers
        number_patterns = [
            (r'(\d+\.?\d*)[万亿亿元%倍]', '数据'),
            (r'(目标价|PE|EPS|营收|收入|利润|增长|评级|买入|增持|推荐)', '关键词'),
        ]
        
        for line in lines[:60]:
            for pat, label in number_patterns:
                if re.search(pat, line):
                    key_facts.append(f"{label}: {line[:150]}")
                    break
    
    else:
        print(f"\n  --- Analyzing {doc_type}: {filename[:50]}...")
        rp_text = extract_text_range(filepath, 0, min(4, 10))
        lines = [l.strip() for l in rp_text.split('\n') if l.strip() and len(l.strip()) > 5]
        print(f"  Content preview: {' | '.join(lines[:15])}")
        
        # Search for key terms
        for term in search_terms_cn:
            pages = search_results.get(term, [])
            if pages:
                for found in pages[:2]:
                    ctx = " | ".join(found["context"][:2])
                    if len(ctx) > 200:
                        ctx = ctx[:200] + "..."
                    key_facts.append(f"[Page {found['page']}] {term}: {ctx}")
    
    return {
        "filename": filename,
        "type": doc_type,
        "company": company,
        "size_bytes": size,
        "text_chars": text_length,
        "key_facts": key_facts[:15],
        "sections": sections[:20],
    }

# Main
results = []
files = sorted(os.listdir(RAW_DIR))
pdf_files = [f for f in files if f.endswith('.pdf') and os.path.isfile(os.path.join(RAW_DIR, f))]

print(f"Found {len(pdf_files)} PDF files in {RAW_DIR}")

for fname in pdf_files:
    fpath = os.path.join(RAW_DIR, fname)
    result = analyze_document(fpath)
    if result:
        results.append(result)

# Save intermediate results
output = {"documents": results, "total_analyzed": len(results)}
outpath = os.path.expanduser("~/finrag/analysis_results.json")
with open(outpath, 'w', encoding='utf-8') as f:
    json.dump(output, f, ensure_ascii=False, indent=2)

print(f"\n\n{'='*80}")
print(f"Saved {len(results)} document analyses to {outpath}")
