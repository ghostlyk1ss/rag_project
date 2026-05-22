#!/usr/bin/env python3
"""
finRAG — Phase 2: 层级化索引与向量库构建
=========================================
Parent-Child 分块策略 + BGE Embedding + Qdrant 向量库

架构：
  解析后的 .md 文件 → 文本预处理
    → 切分父块 (800-1000 tokens, 保留表格完整性)
      → 切分子块 (200 tokens, 语义聚焦)
        → BGE-small-zh-v1.5 向量化
          → Qdrant 入库（含元数据 Payload）

检索流程：
  用户查询 → 向量搜索子块(语义匹配) → 取出对应父块(完整上下文) → LLM 生成
  ⚡ 元数据过滤：只搜指定 company/year/doc_type，消除张冠李戴

用法:
    python scripts/ingestion_v2.py                          # 全量入库
    python scripts/ingestion_v2.py --clean                  # 重建索引
    python scripts/ingestion_v2.py --dry-run                # 预览分块结果
"""

import argparse
import json
import logging
import os
import re
import sys
import time
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

import numpy as np

logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")
logger = logging.getLogger("ingestion_v2")


# ══════════════════════════════════════════════════════════════════════
#  公司名别名映射 & 规范化
# ══════════════════════════════════════════════════════════════════════

COMPANY_ALIAS = {
    "五粮液": ["五粮液", "宜宾五粮液", "五粮液股份", "宜宾五粮液股份"],
    "贵州茅台": ["贵州茅台", "茅台", "贵州茅台酒", "贵州茅台酒股份"],
    "天齐锂业": ["天齐锂业", "天齐", "Tianqi"],
    "宁德时代": ["宁德时代", "CATL"],
    "比亚迪": ["比亚迪", "BYD"],
    "腾讯控股": ["腾讯控股", "腾讯", "Tencent"],
    "阿里巴巴": ["阿里巴巴", "阿里", "Alibaba"],
    "中国平安": ["中国平安", "平安保险", "平安集团"],
    "药明康德": ["药明康德", "WuXi AppTec"],
    "海康威视": ["海康威视", "Hikvision"],
    "美的集团": ["美的集团", "美的", "Midea"],
    "格力电器": ["格力电器", "格力", "Gree"],
    "中国石油": ["中国石油", "中石油", "PetroChina"],
    "中国石化": ["中国石化", "中石化", "Sinopec"],
    "中国移动": ["中国移动", "China Mobile"],
    "招商银行": ["招商银行", "招行", "CMB"],
    "兴业银行": ["兴业银行", "兴业", "CIB"],
    "工商银行": ["工商银行", "工行", "ICBC"],
    "建设银行": ["建设银行", "建行", "CCB"],
    "农业银行": ["农业银行", "农行", "ABC"],
    "中国银行": ["中国银行", "中行", "BOC"],
    "京东方": ["京东方", "BOE", "京东方A"],
    "立讯精密": ["立讯精密", "立讯", "Luxshare"],
    "隆基绿能": ["隆基绿能", "隆基", "隆基股份", "LONGi"],
    "通威股份": ["通威股份", "通威", "Tongwei"],
    "中芯国际": ["中芯国际", "中芯", "SMIC"],
    "华大基因": ["华大基因", "华大", "BGI"],
    "科大讯飞": ["科大讯飞", "讯飞", "iFlytek"],
    "恒瑞医药": ["恒瑞医药", "恒瑞", "Hengrui"],
    "迈瑞医疗": ["迈瑞医疗", "迈瑞", "Mindray"],
    "中国中免": ["中国中免", "中免", "中国国旅"],
    "万科A": ["万科A", "万科", "Vanke"],
    "保利发展": ["保利发展", "保利地产", "保利"],
    "中信证券": ["中信证券", "中信", "CITIC Securities"],
    "中金公司": ["中金公司", "中金", "CICC"],
    "华泰证券": ["华泰证券", "华泰", "HTSC"],
    "紫金矿业": ["紫金矿业", "紫金", "Zijin Mining"],
    "赣锋锂业": ["赣锋锂业", "赣锋", "Ganfeng"],
    "中航光电": ["中航光电", "中航", "AVIC"],
    "汇川技术": ["汇川技术", "汇川", "Inovance"],
    "韦尔股份": ["韦尔股份", "韦尔", "Will Semiconductor"],
    "福斯特": ["福斯特", "州福斯特科技集团"],
    "中矿资源": ["中矿资源", "上海奇思信息技术"],
    "徐工机械": ["徐工机械"],
    "皖仪科技": ["皖仪科技"],
    "同享科技": ["同享科技"],
    "甘李药业": ["甘李药业"],
    "奥浦迈": ["奥浦迈"],
    "陕西能源": ["陕西能源"],
    "中国国家铁路集团": ["中国国家铁路集团", "国铁集团"],
    "北方长龙": ["北方长龙"],
    "广东鸿特": ["广东鸿特"],
    "联讯仪器": ["联讯仪器"],
    "长裕集团": ["长裕集团"],
    "海尔智家": ["海尔智家"],
}


def normalize_company(name: str) -> str:
    """将公司名别名映射为规范名称。"""
    if not name:
        return name
    name = name.strip()
    for canonical, aliases in COMPANY_ALIAS.items():
        if name == canonical:
            return canonical
        for alias in aliases:
            if name == alias:
                return canonical
    return name


# ══════════════════════════════════════════════════════════════════════
#  重要性评分
# ══════════════════════════════════════════════════════════════════════

def compute_importance(doc_type: str, char_count: int, company: str) -> float:
    """基于文档类型和特征计算重要性权重。
    
    年报chunk多→降权, 研报chunk少→提权
    """
    base = {
        "年报": 0.7,
        "半年报": 0.8,
        "研报": 1.2,
        "宏观": 0.9,
        "监管函": 1.0,
        "未知": 1.0,
    }.get(doc_type, 1.0)
    return base


# ══════════════════════════════════════════════════════════════════════
#  配置
# ══════════════════════════════════════════════════════════════════════

@dataclass
class Config:
    """所有配置集中管理，便于后期切换生产环境。"""

    # ── 路径 ────────────────────────────────────────────────────
    _BASE_DIR: Path = Path(__file__).resolve().parents[1]
    PARSED_DIR: Path = _BASE_DIR / "data" / "parsed"
    QDRANT_DB_PATH: Path = _BASE_DIR / "data" / "qdrant_db"

    # ── 分块参数（中文约 2 chars/token）─────────────────────────
    CHILD_TOKEN_TARGET: int = 200       # 子块目标 token 数
    CHILD_CHAR_TARGET: int = 400        # ≈200 tokens * 2 chars
    CHILD_OVERLAP_CHARS: int = 80       # 子块间重叠（原40，关键财务数据避免被边界切分）
    PARENT_TOKEN_TARGET: int = 1000     # 父块目标 token 数
    PARENT_CHAR_TARGET: int = 2000      # ≈1000 tokens * 2 chars
    PARENT_MIN_CHARS: int = 600         # 父块最小长度（太短不切）

    # ── Embedding ───────────────────────────────────────────────
    EMBED_MODEL: str = os.getenv("EMBED_MODEL_PATH",
        os.path.expanduser("~/.cache/huggingface/hub/models--BAAI--bge-large-zh-v1.5/snapshots/79e7739b6ab944e86d6171e44d24c997fc1e0116")
    )  # 使用本地缓存路径，避免 sentence-transformers 尝试联网
    EMBED_DIM: int = 1024
    EMBED_BATCH_SIZE: int = 4

    # ── Qdrant ──────────────────────────────────────────────────
    QDRANT_COLLECTION: str = "finrag"
    QDRANT_MODE: str = "disk"          # memory | disk | remote
    QDRANT_HOST: str = "localhost"
    QDRANT_PORT: int = 6333

    # ── 元数据关键字（用于从文件名/内容中提取）──────────────────
    DOC_TYPE_KEYWORDS: dict = field(default_factory=lambda: {
        "年报": ["年度报告", "annual report", "年報", "2025年度报告", "2026年度报告", "年度报告摘要"],
        "半年报": ["半年度报告", "semiannual", "中期报告"],
        "季报": ["季度报告", "quarterly"],
        "研报": ["研究报告", "行业报告", "深度报告", "行业研究", "证券研究报告", "公司点评"],
        "招股说明书": ["招股说明书", "招股", "zhaogu_"],
        "重组报告": ["重组报告", "资产重组", "chongzu_"],
        "配股说明书": ["配股说明书", "配股", "peigu_"],
        "基金招募说明书": ["招募说明书", "交易型开放式指数", "ETF"],
        "交易所规则": ["交易规则", "证券交易所", "上市规则"],
        "信用评级": ["信用评级", "评级报告", "credit_rating"],
        "股权激励": ["股权激励", "股票期权", "限制性股票", "股权激励计划"],
        "法律意见书": ["法律意见书"],
        "政策文件": ["政策", "白皮书", "policy_"],
        "ESG": ["esg", "环境社会及公司治理", "sustainability"],
        "宏观": ["货币政策", "货币政策执行报告", "pboc", "央行", "宏观经济"],
        "问询函": ["问询函", "问询"],
        "监管函": ["监管函", "监管关注"],
        "审计报告": ["审计报告", "内部控制审计"],
        "资产评估报告": ["资产评估报告"],
    })
    # 常见中国上市公司关键词（保障识别）
    COMPANY_PATTERNS: list = field(default_factory=lambda: [
        r"^(五粮液|贵州茅台|腾讯控股|阿里巴巴|宁德时代|比亚迪)",
        r"(联讯仪器|长裕集团|北方长龙|广东鸿特|海尔智家)",
        r"(上海证券交易所|易方达|陕西能源|澜起科技|中国平安|招商银行)",
        r"^zhaogu_(联讯仪器|长裕集团)",
        r"^peigu_(广东鸿特)",
    ])

    # 已知券商名（用于从文件名提取 source）
    KNOWN_BROKERS: list = field(default_factory=lambda: [
        "中信证券", "中邮证券", "中银证券", "华源证券",
        "信达证券", "西南证券", "东吴证券",
        "国信证券", "太平洋证券", "山西证券", "联储证券",
    ])


# ══════════════════════════════════════════════════════════════════════
#  元数据提取
# ══════════════════════════════════════════════════════════════════════

class MetadataExtractor:
    """从文件名和 Markdown 内容中提取结构化元数据。"""

    def __init__(self, config: Config):
        self.cfg = config
        # 预加载研报元数据反向索引（文件名→stock名）
        self._broker_filename_map = self._load_broker_metadata()

    def _load_broker_metadata(self) -> dict[str, str]:
        """
        从 report_metadata.json 构建文件名→公司名反向索引。
        
        本地文件名格式: 中邮证券_锂矿业务有望..._AP202605011821928221.md
        元数据中: info_code=AP202605011821928221, stock=大中矿业
        
        匹配策略：用 info_code 在文件名中做子串匹配。
        """
        meta_path = self.cfg._BASE_DIR / "data" / "raw" / "report_metadata.json"
        if not meta_path.exists():
            return {}
        try:
            import json
            with open(meta_path, encoding="utf-8") as f:
                reports = json.load(f)
            result = {}
            for r in reports:
                info_code = r.get("info_code", "")
                stock = r.get("stock", "")
                if info_code and stock:
                    # 直接用 info_code 做键（后续匹配用子串搜索）
                    result[info_code] = stock
            return result
        except Exception as e:
            logger.warning(f"加载研报元数据失败: {e}")
            return {}

    def extract(self, filepath: Path, content: str) -> dict:
        """
        返回: {
            "company": "五粮液",
            "year": "2025",
            "doc_type": "年报",
            "source_file": "wuliangye_2025_annual_report.md",
            "source": "中信证券",
        }
        """
        fname = filepath.name
        head = content[:2000]  # 仅需文档开头部分

        # 1) 提取公司名（优先从内容第一行，其次文件名）
        company = self._extract_company(fname, head)

        # 2) 提取年份
        year = self._extract_year(fname, head)

        # 3) 提取文档类型
        doc_type = self._extract_doc_type(fname, head)

        # 4) 提取券商名（source）
        source = self._extract_source(fname)

        return {
            "company": normalize_company(company or "未知"),
            "year": year or "未知",
            "doc_type": doc_type or "未知",
            "source_file": fname,
            "source": source,
        }

    def _extract_source(self, filename: str) -> str:
        """从文件名提取券商名。文件名通常以券商名开头，如 中信证券_xxx.md。"""
        for broker in self.cfg.KNOWN_BROKERS:
            if filename.startswith(broker):
                return broker
        return ""

    def _extract_company(self, filename: str, head: str) -> Optional[str]:
        """
        从文件名和内容提取公司名。
        
        策略（按优先级）：
        1. 研报元数据精确匹配：文件名→stock名（从 report_metadata.json）
        2. 文件名中已知公司模式匹配（五粮液、茅台等）
        3. 内容中匹配"xxx股份有限公司"模式
        4. 英文/拼音文件名回退
        5. 兜底：文件名中取第一个连续中文字符串
        """
        stem = filename.rsplit(".", 1)[0]

        # ── 0) 研报元数据精确匹配 ⭐ 最重要 ──
        # 用 info_code 做子串匹配（文件名含 AP202605011821928221）
        if self._broker_filename_map:
            for info_code, company in self._broker_filename_map.items():
                if info_code and info_code in stem:
                    return company

        # ── 1) 从文件名匹配已知公司模式 ──
        for pat in self.cfg.COMPANY_PATTERNS:
            m = re.search(pat, filename)
            if m:
                return m.group(1)

        # ── 2) 从内容中查找"xxx股份有限公司"或"有限公司"模式 ──
        # 优先匹配"股票简称：北方长龙"格式
        m = re.search(
            r"股票简称[：:]\s*([\u4e00-\u9fff]{2,8})",
            head
        )
        if m:
            return m.group(1)
        # 也匹配"A 股简称：中芯国际"格式
        m = re.search(
            r"(?:A|H)?\s*股简称[：:]\s*([\u4e00-\u9fff]{2,8})",
            head
        )
        if m:
            return m.group(1)

        # 匹配"xxx股份有限公司"（含股份有限+公司）
        m = re.search(
            r"([\u4e00-\u9fff]{2,8})(?:股份有限|股份|集团|有限)公司",
            head
        )
        if m:
            return m.group(1)

        # ── 3) 文件名中下划线/横线前的拼音/英文部分 ──
        m = re.match(r"^([a-z]+)", stem, re.I)
        if m:
            return m.group(1).capitalize()

        # ── 4) 最后兜底：文件名中提取中文字符串 ↴
        m = re.search(r"([\u4e00-\u9fff]{2,6})", filename)
        if m:
            return m.group(1)
        return None

    def _extract_year(self, filename: str, head: str) -> Optional[str]:
        # 优先匹配 "2025年报" 或 "2026一季报" 等明确报告期表述
        combined = f"{filename} {head[:500]}"
        # 1) "2025年报" → 2025
        m = re.search(r"(20[0-9]{2})\s*年[报度]", combined)
        if m:
            return m.group(1)
        # 2) 文件名中 "{year}_annual_report" 模式
        m = re.search(r"(20[0-9]{2})_annual_report", filename)
        if m:
            return m.group(1)
        # 3) 文件名中 "{YY}Q1" 模式 → 用该年份
        m = re.search(r"(20[0-9]{2})Q[1-4]", filename)
        if m:
            return m.group(1)
        # 4) 内容中匹配 "2025年" 或 "2025 年度"
        for text in [head, filename]:
            m = re.search(r"(20[0-9]{2})\s*[年度年]", text)
            if m:
                return m.group(1)
        # 5) 文件名中的四位数字年份（排除 AP2026... 时间戳）
        m = re.search(r"(?<!AP)(20[0-9]{2})(?!\d{12})", filename)
        if m:
            return m.group(1)
        # 6) 文件名中的 AP 时间戳年份（作为兜底）
        m = re.search(r"AP(20[0-9]{2})", filename)
        if m:
            return m.group(1)
        return None

    def _extract_doc_type(self, filename: str, head: str) -> Optional[str]:
        combined = f"{filename} {head.lower()}"
        for dtype, keywords in self.cfg.DOC_TYPE_KEYWORDS.items():
            for kw in keywords:
                if kw.lower() in combined:
                    return dtype
        return None


# ══════════════════════════════════════════════════════════════════════
#  表格摘要生成
# ══════════════════════════════════════════════════════════════════════

def generate_table_summary(text: str) -> str:
    """如果文本包含表格行（|），提取数值+表头生成紧凑摘要。"""
    lines = text.split("\n")
    table_lines = [l.strip() for l in lines if l.strip().startswith("|") and "|" in l.strip()[1:]]
    if not table_lines:
        return ""

    # 尝试识别表头（第一行包含文字而非纯分隔符）
    headers = []
    if table_lines:
        first_parts = [p.strip() for p in table_lines[0].split("|") if p.strip()]
        # 跳过纯分隔行（|---|）
        if not all(re.match(r"^[-:]+$", p) for p in first_parts):
            headers = first_parts

    # 提取数值行中的关键信息
    numeric_rows = []
    for row in table_lines:
        parts = [p.strip() for p in row.split("|") if p.strip()]
        if len(parts) >= 2:
            has_number = any(re.search(r"[\d,.]+\s*", p) for p in parts[1:])
            if has_number:
                # 只保留前两列（名称 + 第一个数值）
                first_col = parts[0]
                val_cols = [p for p in parts[1:] if re.search(r"[\d,.]", p)]
                if val_cols:
                    numeric_rows.append((first_col, val_cols[0]))

    # 构建紧凑摘要
    if headers:
        header_str = " | ".join(headers[:4])  # 最多 4 列
        if numeric_rows:
            sample = "; ".join(f"{r[0]}: {r[1]}" for r in numeric_rows[:8])
            summary = f"[表] {header_str} | 值: {sample}"
            if len(numeric_rows) > 8:
                summary += f" ...共{len(numeric_rows)}行"
            return summary
        else:
            return f"[表] {header_str}"
    elif numeric_rows:
        sample = "; ".join(f"{r[0]}: {r[1]}" for r in numeric_rows[:8])
        summary = f"[表] 值: {sample}"
        if len(numeric_rows) > 8:
            summary += f" ...共{len(numeric_rows)}行"
        return summary

    return ""


def looks_like_table_text(text: str) -> bool:
    """检测文本是否包含表格内容（支持 | 分隔符和空格对齐两种格式）。

    空格对齐表格示例:
        营业收入（元） 40,528,509,770.23 89,175,178,322.70 -54.55%
        归属于上市公司股东的净利润（元） 8,954,257,202.51 31,853,172,533.98
    """
    import re
    lines = text.split(chr(10))
    # 方法1：检测 Markdown 表格（含 | 分隔符）
    pipe_lines = [l for l in lines if l.strip().startswith("|") and "|" in l.strip()[1:]]
    if len(pipe_lines) >= 2:
        return True
    if len(pipe_lines) >= 1 and len(lines) <= 3:
        return True

    # 方法2：检测空格对齐表格（中文描述 + 多个数值列）
    table_line_count = 0
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        parts = stripped.split()
        if len(parts) < 3:  # 至少：名称 + 值 + 值
            continue
        # 必须包含中文字符
        has_chinese = any('\u4e00' <= c <= '\u9fff' for c in stripped)
        if not has_chinese:
            continue
        # 至少2个非首列部分是数值
        numeric_parts = sum(1 for p in parts[1:] if re.search(r'\d', p))
        if numeric_parts >= 2:
            table_line_count += 1

    return table_line_count >= 2


def table_to_natural_language(text: str, company: str = "", year: str = "",
                               statement_type: str = "") -> str:
    """将 Markdown 表格（| 分隔符）转换为自然语言描述，提升语义检索质量。

    转换示例:
        原始:
        | 项目 | 2025年 | 2024年 |
        | 营业收入 | 44,163,325,242 | 37,273,295,430 |

        转换后:
        五粮液2025年度财务数据：营业收入 44,163,325,242元（2024年: 37,273,295,430元）
    """
    lines = text.split(chr(10))
    import re

    # Try pipe-delimited table (Markdown)
    pipe_lines = [l.strip() for l in lines if l.strip().startswith('|') and '|' in l.strip()[1:]]
    data_rows = []
    headers = []
    if pipe_lines:
        for row in pipe_lines:
            parts = [p.strip() for p in row.split('|') if p.strip()]
            if parts and not all(re.match(r'^[-:]+$', p) for p in parts):
                if not headers:
                    headers = parts
                    continue
            if parts and all(re.match(r'^[-:]+$', p) for p in parts):
                continue
            if len(parts) >= 2:
                data_rows.append(parts)
    else:
        # Try space-aligned table
        space_rows = []
        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue
            has_chinese = any('\u4e00' <= c <= '\u9fff' for c in stripped)
            has_numbers = bool(re.search(r'\d', stripped))
            if not (has_chinese and has_numbers):
                continue
            parts = [p.strip() for p in stripped.split() if p.strip()]
            if len(parts) >= 2:
                space_rows.append(parts)
        if not space_rows:
            return ''
        first_row = space_rows[0]
        if len(first_row) >= 2 and not any(re.search(r'\d', p) for p in first_row[1:]):
            headers = first_row
            data_rows = space_rows[1:]
        else:
            data_rows = space_rows

    if not data_rows:
        return ''

    n_cols = max(len(r) for r in data_rows)
    has_year_cols = sum(1 for h in headers[1:] if re.search(r'20\d{2}', h)) > 0 if len(headers) > 1 else False

    fragments = []
    if has_year_cols and headers:
        for row in data_rows:
            name = row[0]
            vals = []
            for j, v in enumerate(row[1:], 1):
                col_label = headers[j] if j < len(headers) else f'列{j}'
                vals.append(f'{col_label}: {v}')
            fragments.append(f"{name} {','.join(vals)}")
    elif len(data_rows) <= 15:
        for row in data_rows:
            name = row[0]
            vals = row[1:]
            if len(vals) == 1:
                fragments.append(f'{name} {vals[0]}')
            else:
                fragments.append(f"{name}: {'、'.join(vals)}")
    else:
        for row in data_rows[:8]:
            name = row[0]
            vals = row[1:]
            fragments.append(f'{name} {vals[0] if vals else ""}')
        fragments.append(f'等共{len(data_rows)}项数据')

    # 组装前缀
    prefix_parts = []
    if company:
        prefix_parts.append(company)
    if year:
        prefix_parts.append(f"{year}年度")
    label = statement_type or "财务数据"
    ctx = "".join(prefix_parts)
    context_prefix = f"{ctx}" if ctx else ""
    title_prefix = f"【{label}】" if label else ""

    body = "；".join(fragments)

    result = f"{title_prefix}{context_prefix}：{body}"
    return result[:2000]  # 限制长度


# ══════════════════════════════════════════════════════════════════════
#  父子分块器 (Parent-Child Splitter)
# ══════════════════════════════════════════════════════════════════════

@dataclass
class ParentChunk:
    """父块：用于 LLM 上下文，包含完整段落/表格。"""
    id: str
    text: str
    char_count: int
    index: int
    has_table: bool = False
    section: str = ""
    table_summary: str = ""
    page: int = 1        # 起始页码
    children: list = field(default_factory=list)  # [ChildChunk, ...]


@dataclass
class ChildChunk:
    """子块：用于向量匹配，短小语义精准。"""
    id: str
    text: str
    char_count: int
    index: int
    parent_id: str
    has_table: bool = False
    section: str = ""
    table_summary: str = ""
    page: int = 1        # 继承自父块的起始页码
    search_text: str = ""  # 带前缀/摘要的增强文本（用于检索/BM25）


class ParentChildSplitter:
    """
    父子分块策略：

    [父块]    800-1000 tokens / 1600-2000 chars
      ├── [子块 1]  ≈200 tokens / ≈400 chars
      ├── [子块 2]  ≈200 tokens / ≈400 chars
      └── [子块 N]  ...

    表格保护：Markdown 表格（| 行）不会被切断在父块边界。
    子块间有 overlap 保证边界语义不丢失。
    """

    def __init__(self, config: Config):
        self.cfg = config

    def _build_page_breaks(self, text: str) -> list[int]:
        """构建字符位置→页码映射。

        扫描文本中独立成行的 `---`（页面分隔符），记录每个分页符的字符偏移。
        Docling 和 PyMuPDF 两种解析器都会输出 `---` 独占一行作为翻页标记。
        返回 [end_of_page1, end_of_page2, ..., total_length]
        其中 end_of_pageN 是第 N 页结束的累计字符偏移。
        """
        breaks = []
        lines = text.split("\n")
        offset = 0
        for line in lines:
            offset += len(line) + 1  # +1 for the newline char
            if line.strip() == "---":
                # 此 `---` 是当前页的结束标记
                # offset 已经包含了这个 `---\n`，但我们要的是页内容结束的位置
                # 所以减去 `---\n` 的长度（4）
                breaks.append(offset - 4)
        # 确保最后一页也有边界
        if not breaks or breaks[-1] < len(text):
            breaks.append(len(text))
        return breaks

    def _char_offset_to_page(
        self, char_offset: int, page_breaks: list[int]
    ) -> int:
        """将字符偏移转换为页码（1-indexed）。"""
        for page_num, break_pos in enumerate(page_breaks, 1):
            if char_offset < break_pos:
                return page_num
        return len(page_breaks)  # 最后一页

    def split(self, md_text: str) -> list[ParentChunk]:
        """将完整 Markdown 文档切分为 ParentChunk 列表。"""
        # 0) 先保存原始文本用于后续构建页面索引
        # 1) 文档级预处理
        text = self._preprocess(md_text)
        if not text.strip():
            return []

        # 构建页面索引（基于预处理后的文本）
        page_breaks = self._build_page_breaks(text)

        # 2) 按段落切分（保留表格完整性）
        paragraphs = self._split_into_paragraphs(text)

        # 3) 合并为父块（带章节追踪 + 页码追踪）
        parents = self._merge_into_parents(paragraphs)

        # 4) 从父块切分子块
        for parent in parents:
            parent.children = self._split_children(parent)

        return parents

    def _preprocess(self, text: str) -> str:
        """清理无关行、规范化空白。"""
        lines = text.split("\n")
        cleaned = []
        for line in lines:
            s = line.strip()
            # 跳过纯图片占位和空行
            if s == "<!-- image -->":
                continue
            # 保留表格、标题、列表、正文
            cleaned.append(line)
        return "\n".join(cleaned)

    FINANCIAL_STATEMENT_TITLES = {
        "合并利润表": "合并利润表", "母公司利润表": "母公司利润表",
        "合并资产负债表": "合并资产负债表", "母公司资产负债表": "母公司资产负债表",
        "合并现金流量表": "合并现金流量表", "母公司现金流量表": "母公司现金流量表",
        "合并股东权益变动表": "合并股东权益变动表", "母公司股东权益变动表": "母公司股东权益变动表",
    }

    FINANCIAL_KEYWORD_GROUPS = {
        "利润表": ["营业收入", "营业成本", "营业利润", "利润总额", "净利润",
                   "归属于母公司所有者的净利润", "归母净利润", "少数股东损益",
                   "综合收益总额", "营业总收入", "营业总成本",
                   "销售费用", "管理费用", "研发费用", "财务费用",
                   "投资收益", "资产减值损失", "信用减值损失"],
        "资产负债表": ["资产总计", "负债合计", "所有者权益", "流动资产", "非流动资产",
                     "流动负债", "非流动负债", "货币资金", "应收账款", "存货",
                     "固定资产", "无形资产", "短期借款", "长期借款", "应付账款",
                     "股本", "资本公积", "盈余公积", "未分配利润"],
        "现金流量表": ["经营活动产生的现金流量", "投资活动产生的现金流量",
                     "筹资活动产生的现金流量", "现金及现金等价物净增加额",
                     "销售商品、提供劳务收到的现金"],
        "财务指标": ["毛利率", "净利率", "ROE", "净资产收益率", "资产负债率",
                   "流动比率", "速动比率", "每股收益", "每股净资产",
                   "基本每股收益", "稀释每股收益", "加权平均净资产收益率"],
    }

    def _is_financial_statement_title(self, line: str) -> bool:
        """检测一行是否是财务报表标题（如"3、合并利润表"）"""
        stripped = line.strip()
        import re
        for title in FINANCIAL_STATEMENT_TITLES:
            # 必须匹配 "X、标题" 格式（标题在数字序号后），不能是文本中随意提到
            if re.search(rf'[0-9一二三四五六七八九十]+[、．]{re.escape(title)}\s*$', stripped):
                return True
        return False

    def _is_section_header(self, line: str) -> bool:
        """检测一行是否是节标题（如"七、投资状况分析"）"""
        stripped = line.strip()
        import re
        # 匹配 "一、" "二、" "三、" ... "二十、" 等节标题
        if re.match(r'^[一二三四五六七八九十百千]+[、．\.]', stripped):
            return True
        # 也匹配英文数字节标题 "1." "2."
        if re.match(r'^\d+[、．\.]\s*\S', stripped):
            return True
        return False

    def _extract_section_name(self, line: str) -> str:
        """从节标题行提取章节名（去掉序号前缀）。"""
        stripped = line.strip()
        # 匹配中文数字节标题 "X、标题"
        m = re.match(r'^[一二三四五六七八九十百千]+[、．\.]\s*(.*)', stripped)
        if m:
            return m.group(1).strip()
        # 匹配英文数字节标题 "1. 标题"
        m = re.match(r'^\d+[、．\.]\s*(.*)', stripped)
        if m:
            return m.group(1).strip()
        # Markdown heading
        m = re.match(r'^#+\s+(.*)', stripped)
        if m:
            return m.group(1).strip()
        return stripped

    def _split_into_paragraphs(self, text: str) -> list[str]:
        """
        将文本切成段落级别的片段，确保表格和财务报表不被拆分。

        规则：
        - 表格行（| 开头）与前后行合并为一个段落
        - 财务报表标题（如"3、合并利润表"）触发"整表保护模式"
        - 标题行（# 开头）独立为段落
        - 空行分隔的正文段落
        """
        lines = text.split("\n")
        paragraphs = []
        current = []
        in_table = False
        in_financial_statement = False  # 财务报表保护模式

        for line in lines:
            stripped = line.strip()
            is_table_line = stripped.startswith("|")
            is_heading = stripped.startswith("#")
            is_empty = not stripped
            is_fs_title = self._is_financial_statement_title(stripped)

            if is_fs_title:
                # 财务报表标题 → 开启保护模式，收集整张表
                if current:
                    paragraphs.append("\n".join(current))
                    current = []
                current = [line]
                in_financial_statement = True
                in_table = False

            elif in_financial_statement:
                # 在财务表保护模式中，收集所有行直到遇到:
                # 1) Markdown 标题（# 开头）
                # 2) 纯文字性节标题（不含财务关键词的"X、XXX"格式）
                # 注意：利润表内的"一、营业总收入""二、营业总成本"等是数据行，不是节标题
                if is_heading:
                    if current:
                        paragraphs.append("\n".join(current))
                        current = []
                    current = [line]
                    in_financial_statement = False
                elif self._is_section_header(stripped) and not is_empty:
                    # 检查是否是纯文字节标题（不含财务关键词）
                    import re
                    # 提取"X、"之后的文字部分
                    after_num = re.sub(r'^[一二三四五六七八九十百千]+[、．\.]\s*', '', stripped)
                    # 如果标题文字不含财务关键词，视为真正的节标题
                    all_financial_keywords = set()
                    for stype, keywords in FINANCIAL_KEYWORD_GROUPS.items():
                        all_financial_keywords.update(keywords)
                    all_statement_titles = set(FINANCIAL_STATEMENT_TITLES.keys())
                    all_financial_terms = all_financial_keywords | all_statement_titles
                    
                    has_financial_kw = any(kw in after_num for kw in all_financial_terms)
                    if not has_financial_kw:
                        # 真正的节标题 → 结束当前报表
                        if current:
                            paragraphs.append("\n".join(current))
                            current = []
                        current = [line]
                        in_financial_statement = False
                    else:
                        # 财务表的数据行（如"一、营业总收入"）→ 继续收集
                        current.append(line)
                else:
                    # 仍在报表内，继续收集
                    current.append(line)
                    # 安全阀：单行报表太长时强制结束
                    if len("\n".join(current)) > 10000:
                        paragraphs.append("\n".join(current))
                        current = []
                        in_financial_statement = False

            elif is_table_line:
                current.append(line)
                in_table = True
            elif in_table and (is_empty or is_heading):
                if current:
                    paragraphs.append("\n".join(current))
                    current = []
                in_table = False
                if is_heading:
                    paragraphs.append(line)
            elif in_table:
                current.append(line)
            elif is_heading:
                if current:
                    paragraphs.append("\n".join(current))
                    current = []
                paragraphs.append(line)
            elif is_empty:
                if current:
                    paragraphs.append("\n".join(current))
                    current = []
            else:
                current.append(line)

        if current:
            paragraphs.append("\n".join(current))

        return [p.strip() for p in paragraphs if p.strip()]

    def _merge_into_parents(self, paragraphs: list[str]) -> list[ParentChunk]:
        """将段落按目标长度合并为父块，同时追踪当前章节和页码。

        页码通过统计段落中 `---`（翻页标记）的出现次数来确定。
        """
        parents = []
        current_chunks = []
        current_len = 0
        idx = 0
        current_section = ""  # 追踪当前章节名
        page_break_count = 0  # 已遇到的 `---` 翻页标记数
        chunk_start_page = 1  # 当前正在构建的 chunk 的起始页码

        for para in paragraphs:
            para_len = len(para)
            is_page_break = para.strip() == "---"

            # 记录翻页标记（不加入 parent chunk 内容）
            if is_page_break:
                page_break_count += 1
                continue

            stripped = para.strip()

            # 检测是否是新章节标题
            is_new_section = self._is_section_header(stripped) or stripped.startswith("#")

            # 如果是新节标题 & 当前块已有内容 & 已超出最小大小 → 主动封块换节
            if is_new_section and current_chunks and current_len >= self.cfg.PARENT_MIN_CHARS:
                text = "\n\n".join(current_chunks)
                has_table = looks_like_table_text(text)
                if len(text) >= self.cfg.PARENT_MIN_CHARS:
                    parents.append(ParentChunk(
                        id=str(uuid.uuid4()),
                        text=text,
                        char_count=len(text),
                        index=idx,
                        has_table=has_table,
                        section=current_section,
                        table_summary=generate_table_summary(text),
                        page=chunk_start_page,
                    ))
                    idx += 1
                current_chunks = []
                current_len = 0

            # 更新章节名（在新节触发 flush 之后）
            if is_new_section:
                current_section = self._extract_section_name(stripped)

            # 如果当前块 + 本段落超出目标且当前块已有内容 → 封块
            if current_len + para_len > self.cfg.PARENT_CHAR_TARGET and current_chunks:
                text = "\n\n".join(current_chunks)
                has_table = looks_like_table_text(text)
                if len(text) >= self.cfg.PARENT_MIN_CHARS:
                    parents.append(ParentChunk(
                        id=str(uuid.uuid4()),
                        text=text,
                        char_count=len(text),
                        index=idx,
                        has_table=has_table,
                        section=current_section,
                        table_summary=generate_table_summary(text),
                        page=chunk_start_page,
                    ))
                    idx += 1
                current_chunks = []
                current_len = 0

            # 将本段加入当前块
            current_chunks.append(para)
            current_len += para_len
            if len(current_chunks) == 1:
                chunk_start_page = page_break_count + 1  # 记录起始页码

        # 最后一段
        if current_chunks:
            text = "\n\n".join(current_chunks)
            has_table = looks_like_table_text(text)
            if len(text) >= self.cfg.PARENT_MIN_CHARS:
                parents.append(ParentChunk(
                    id=str(uuid.uuid4()),
                    text=text,
                    char_count=len(text),
                    index=idx,
                    has_table=has_table,
                    section=current_section,
                    table_summary=generate_table_summary(text),
                    page=chunk_start_page,  # 使用 chunk 起始时的页码
                ))

        return parents

    def _split_children(self, parent: ParentChunk) -> list[ChildChunk]:
        """从父块切分子块，带重叠。"""
        text = parent.text
        target = self.cfg.CHILD_CHAR_TARGET
        overlap = self.cfg.CHILD_OVERLAP_CHARS
        min_chunk = target // 4  # 最少 50 token / ~100 字

        children = []
        start = 0
        ci = 0

        while start < len(text):
            raw_end = min(start + target, len(text))

            # 尝试在自然边界断开（但不过度靠近 start）
            end = raw_end
            if end < len(text):
                for boundary in ["\n\n", "\n", "。", "；", ". "]:
                    pos = text.rfind(boundary, start, end)
                    min_pos = start + min_chunk
                    if min_pos < pos < end:
                        end = pos + len(boundary)
                        break

            chunk_text = text[start:end].strip()
            # 金融数据保护：如果切分边界卡在数字/财务数据行，扩展至行尾
            if end < len(text):
                rest = text[end:]
                first_line_end = rest.find("\n")
                if first_line_end > 0:
                    first_line = rest[:first_line_end]
                    # 检测是否以财务数据开头（数字+关键金融关键词）
                    import re
                    fin_pattern = r'^\s*\d+[,.]?\d*\s*(亿元|万元|元|%|每股|占总|同比|增长|下降|收入|利润|分红|股息|净利润|营收|归母)'
                    if re.search(fin_pattern, first_line):
                        end = end + first_line_end + 1  # 包含整行
                        chunk_text = text[start:end].strip()
            # 注入父块章节名作为上下文（帮助嵌入模型理解片段来源）
            if parent.section and not chunk_text.startswith(parent.section):
                chunk_text = f"[{parent.section}] {chunk_text}"
            if len(chunk_text) >= min_chunk:
                has_table = "|" in chunk_text
                
                # ── 构建增强检索文本（注入节标题+表格摘要）──
                prefix_parts = []
                if parent.section:
                    prefix_parts.append(f"[{parent.section}]")
                # 表格摘要（优先用当前 chunck 的, 兜底用父块的）
                ts = generate_table_summary(chunk_text)
                if not ts and parent.table_summary:
                    ts = parent.table_summary
                if ts:
                    # 提取数字和关键信息注入
                    prefix_parts.append(ts)
                    # 标记 chunk 类型
                    prefix_parts.append("[table_data]")
                # 从父块继承公司/年份信息
                search_prefix = " ".join(prefix_parts)
                search_text = f"{search_prefix}\n{chunk_text}" if search_prefix else chunk_text
                
                children.append(ChildChunk(
                    id=str(uuid.uuid4()),
                    text=chunk_text,
                    char_count=len(chunk_text),
                    index=ci,
                    parent_id=parent.id,
                    has_table=has_table,
                    section=parent.section,
                    table_summary=ts or parent.table_summary,
                    page=parent.page,
                    search_text=search_text,
                ))
                ci += 1
            elif chunk_text and not children:
                # 第一个块太短也要保留（文档开头）
                children.append(ChildChunk(
                    id=str(uuid.uuid4()),
                    text=chunk_text,
                    char_count=len(chunk_text),
                    index=ci,
                    parent_id=parent.id,
                    has_table=has_table,
                    section=parent.section,
                    table_summary=generate_table_summary(chunk_text),
                    page=parent.page,
                ))
                ci += 1

            # 前进：确保至少前进 target/4，最多 target - overlap
            step = max(target - overlap, min_chunk)
            start = max(start + step, end - min_chunk)
            if start >= len(text):
                break

            # 确保不超限
            if start > len(text) - min_chunk:
                start = len(text) - min_chunk
                break

        return children

    def get_section_for_chunk(self, chunk_index: int, parents: list[ParentChunk]) -> str:
        """给定一个子块在文档中的大致位置，返回最近的章节名。"""
        # 遍历父块查找包含该 chunk_index 的父块
        for parent in parents:
            for child in parent.children:
                if child.index == chunk_index:
                    return parent.section or child.section
        return ""


# ══════════════════════════════════════════════════════════════════════
#  财务表格感知：语义前缀注入 + 关键词检测
# ══════════════════════════════════════════════════════════════════════

FINANCIAL_STATEMENT_TITLES = {
    "合并利润表": "合并利润表", "母公司利润表": "母公司利润表",
    "合并资产负债表": "合并资产负债表", "母公司资产负债表": "母公司资产负债表",
    "合并现金流量表": "合并现金流量表", "母公司现金流量表": "母公司现金流量表",
    "合并股东权益变动表": "合并股东权益变动表", "母公司股东权益变动表": "母公司股东权益变动表",
}

FINANCIAL_KEYWORD_GROUPS = {
    "利润表": ["营业收入", "营业成本", "营业利润", "利润总额", "净利润",
               "归属于母公司所有者的净利润", "归母净利润", "少数股东损益",
               "综合收益总额", "营业总收入", "营业总成本",
               "销售费用", "管理费用", "研发费用", "财务费用",
               "投资收益", "资产减值损失", "信用减值损失"],
    "资产负债表": ["资产总计", "负债合计", "所有者权益", "流动资产", "非流动资产",
                 "流动负债", "非流动负债", "货币资金", "应收账款", "存货",
                 "固定资产", "无形资产", "短期借款", "长期借款", "应付账款",
                 "股本", "资本公积", "盈余公积", "未分配利润"],
    "现金流量表": ["经营活动产生的现金流量", "投资活动产生的现金流量",
                 "筹资活动产生的现金流量", "现金及现金等价物净增加额",
                 "销售商品、提供劳务收到的现金"],
    "财务指标": ["毛利率", "净利率", "ROE", "净资产收益率", "资产负债率",
               "流动比率", "速动比率", "每股收益", "每股净资产",
               "基本每股收益", "稀释每股收益", "加权平均净资产收益率"],
}

STATEMENT_PREFIX_TEMPLATE = "【{statement}】以下是上市公司{company}{year}年{statement}数据，包含{keywords}等关键财务指标。\n\n"


def detect_financial_chunk(text: str) -> dict:
    """
    检测文本片段是否包含财务报表数据。

    返回:
        {"is_financial": bool, "title": str, "statement_type": str, "keywords": list[str], "prefix": str}
    """
    result = {"is_financial": False, "title": "", "statement_type": "", "keywords": [], "prefix": ""}

    # 1) 检测报表标题（优先级最高）
    for title, stype in FINANCIAL_STATEMENT_TITLES.items():
        if title in text:
            result["is_financial"] = True
            result["title"] = title
            result["statement_type"] = stype
            break

    # 2) 检测财务关键词
    found_keywords = []
    for stype, keywords in FINANCIAL_KEYWORD_GROUPS.items():
        for kw in keywords:
            if kw in text:
                found_keywords.append(kw)
    if found_keywords:
        result["is_financial"] = True
        result["keywords"] = found_keywords
        if not result["statement_type"]:
            for stype, keywords in FINANCIAL_KEYWORD_GROUPS.items():
                if any(kw in text for kw in keywords[:8]):
                    result["statement_type"] = stype
                    result["title"] = stype
                    break

    return result


def build_financial_prefix(text: str, statement_type: str, keywords: list[str],
                           company: str = "", year: str = "",
                           title: str = "", section: str = "") -> str:
    """为财务数据块构建语义前缀。新格式： [公司=xxx][章节=xxx][年份=xxx][报表=xxx] 正文..."""
    if not statement_type or not keywords:
        return ""
    import re
    if not company:
        m = re.search(r"(五粮液|贵州茅台|宁德时代|比亚迪|腾讯)", text)
        if m: company = m.group(1)
    if not year:
        m = re.search(r"(20[0-9]{2})", text)
        if m: year = m.group(1)

    # 使用精确的报表标题（如"合并利润表"）而非泛型类型
    label = title or statement_type
    top_kw = keywords[:5]
    kw_str = "、".join(top_kw)

    # 新格式： [公司=xxx][章节=xxx][年份=xxx][报表=xxx] 正文...
    prefix = f"[公司={company or '该公司'}][章节={section}][年份={year}][报表={label}] "
    return prefix


# ══════════════════════════════════════════════════════════════════════
#  嵌入引擎 (Embedding Engine)
# ══════════════════════════════════════════════════════════════════════

class EmbeddingEngine:
    """BGE-small-zh-v1.5 本地嵌入模型。懒加载 + 批次编码。"""

    def __init__(self, config: Config):
        self.cfg = config
        self._model = None

    def _load(self):
        """懒加载模型（仅在首次 encode 时触发下载）。"""
        if self._model is not None:
            return
        model_name = self.cfg.EMBED_MODEL
        logger.info(f"🔌 加载嵌入模型: {model_name} ...")

        # 离线模式：如果环境变量已设置则保留，否则不强制
        if os.environ.get("HF_HUB_OFFLINE", "").lower() in ("1", "true", "yes"):
            pass  # 用户已主动设置离线模式
        else:
            # 尝试从本地缓存加载，没有则走正常下载路径
            pass

        # 将模型名解析为本地缓存路径（避免 sentence-transformers 尝试联网）
        cache_dir = os.path.expanduser("~/.cache/huggingface/hub")
        safe_name = model_name.replace("/", "--")
        snap_dir = os.path.join(cache_dir, f"models--{safe_name}", "snapshots")
        model_path = model_name
        if os.path.isdir(snap_dir):
            snapshots = sorted(os.listdir(snap_dir))
            if snapshots:
                model_path = os.path.join(snap_dir, snapshots[-1])
                logger.info(f"   ↪ 本地缓存路径: {model_path}")

        from sentence_transformers import SentenceTransformer
        self._model = SentenceTransformer(
            model_path,
            device="cpu",
            local_files_only=os.path.isdir(snap_dir),
        )
        logger.info(f"   ✅ 模型加载完成（维度={self._model.get_sentence_embedding_dimension()}）")

    def encode(self, texts: list[str]) -> np.ndarray:
        """批量编码文本为向量。返回 shape (n, dim) 的 numpy 数组。"""
        self._load()
        # 调用方已自行处理前缀（查询加/文档不加），此处不做附加
        embeddings = self._model.encode(
            texts,
            batch_size=self.cfg.EMBED_BATCH_SIZE,
            show_progress_bar=True,
            normalize_embeddings=True,  # L2 归一化，余弦相似度兼容点积
        )
        return embeddings

    @property
    def dim(self) -> int:
        """返回嵌入维度。"""
        self._load()
        return self._model.get_sentence_embedding_dimension()


# ══════════════════════════════════════════════════════════════════════
#  Qdrant 索引器
# ══════════════════════════════════════════════════════════════════════

class QdrantIndexer:
    """向量库操作：建集合、写入、检索。支持内存/磁盘/远程三种模式。"""

    def __init__(self, config: Config, existing_client=None):
        self.cfg = config
        self._client: Optional["QdrantClient"] = existing_client
        self._owns_client = existing_client is None

    def _connect(self):
        """初始化 Qdrant 客户端。"""
        if self._client is not None:
            return

        mode = self.cfg.QDRANT_MODE
        if mode == "memory":
            from qdrant_client import QdrantClient
            self._client = QdrantClient(location=":memory:")
            logger.info("🔌 Qdrant: 内存模式")
        elif mode == "disk":
            from qdrant_client import QdrantClient
            self.cfg.QDRANT_DB_PATH.mkdir(parents=True, exist_ok=True)
            self._client = QdrantClient(path=str(self.cfg.QDRANT_DB_PATH))
            logger.info(f"🔌 Qdrant: 磁盘模式 → {self.cfg.QDRANT_DB_PATH}")
        elif mode == "remote":
            from qdrant_client import QdrantClient
            self._client = QdrantClient(
                host=self.cfg.QDRANT_HOST,
                port=self.cfg.QDRANT_PORT,
            )
            logger.info(f"🔌 Qdrant: 远程模式 → {self.cfg.QDRANT_HOST}:{self.cfg.QDRANT_PORT}")
        else:
            raise ValueError(f"未知 Qdrant 模式: {mode}")

    def ensure_collection(self, force_recreate: bool = False):
        """创建集合（如果不存在或需要重建）。"""
        self._connect()
        from qdrant_client import models

        col_name = self.cfg.QDRANT_COLLECTION

        if force_recreate:
            try:
                self._client.delete_collection(col_name)
                logger.info(f"🗑️  已删除集合: {col_name}")
            except Exception:
                pass

        try:
            self._client.get_collection(col_name)
            count = self._client.count(col_name).count
            logger.info(f"📦 集合已存在: '{col_name}', 现有向量: {count}")
            return count
        except Exception:
            pass

        # 创建集合
        self._client.create_collection(
            collection_name=col_name,
            vectors_config=models.VectorParams(
                size=self.cfg.EMBED_DIM,
                distance=models.Distance.COSINE,
            ),
            # 启用全文过滤（用于 payload 级别的文本检索兜底）
            optimizers_config=models.OptimizersConfigDiff(
                default_segment_number=2,
            ),
        )
        logger.info(f"📦 创建集合: '{col_name}', 维度={self.cfg.EMBED_DIM}, 距离=Cosine")
        return 0

    def index_document(
        self,
        metadata: dict,
        parent_chunks: list[ParentChunk],
        embedder: EmbeddingEngine,
    ):
        """
        将一个文档的所有父子块写入 Qdrant。

        策略：
        - 父块和子块都作为 point 写入
        - 父块: is_parent=True
        - 子块: is_parent=False, parent_id=父块ID
        - 元数据(company/year/doc_type) 写入 payload 支持过滤
        """
        self._connect()
        from qdrant_client import models
        from grpc import RpcError

        col_name = self.cfg.QDRANT_COLLECTION
        logger.info(f"   📝 准备写入 {len(parent_chunks)} 个父块...")

        # ── 收集所有待编码的文本 ──────────────────────────────────
        parent_texts = [p.text for p in parent_chunks]
        child_texts = []
        child_parent_map = []  # [(ChildChunk, ParentChunk), ...]
        child_financial_info = []  # [(child_index, financial_info), ...]

        for parent in parent_chunks:
            for child in parent.children:
                child_texts.append(child.text)
                child_parent_map.append((child, parent))

        all_texts = parent_texts + child_texts
        if not all_texts:
            logger.warning("   ⚠️  没有文本需要编码")
            return 0

        # ── 财务表格感知：注入语义前缀 ───────────────────────────
        # 检测每个 chunk 是否含财务报表数据，注入前缀提升 embedding 质量
        n_parents = len(parent_chunks)
        prefixed_texts = list(all_texts)  # 默认不变
        financial_payloads = []  # 每个 text 对应的 {financial_keywords, statement_type}
        
        # 提前扫描父块，获取准确的报表标题（供子块继承）
        parent_titles = []
        for parent_text in parent_texts:
            info = detect_financial_chunk(parent_text)
            parent_titles.append(info.get("title", ""))

        for idx, text in enumerate(all_texts):
            info = detect_financial_chunk(text)
            # 子块未检测到精确标题时，从其父块继承
            if not info.get("title") and info["is_financial"] and idx >= n_parents:
                child_idx = idx - n_parents
                if child_idx < len(child_parent_map):
                    parent = child_parent_map[child_idx][1]  # (child, parent)
                    parent_info = detect_financial_chunk(parent.text)
                    inherited = parent_info.get("title", "")
                    if inherited:
                        info["title"] = inherited

            if info["is_financial"] and info["keywords"]:
                # 获取章节名
                section = ""
                if idx < n_parents:
                    section = parent_chunks[idx].section
                else:
                    child_idx = idx - n_parents
                    if child_idx < len(child_parent_map):
                        child = child_parent_map[child_idx][0]
                        section = child.section

                prefix = build_financial_prefix(
                    text, info["statement_type"], info["keywords"],
                    company=metadata.get("company", ""),
                    year=metadata.get("year", ""),
                    title=info.get("title", ""),
                    section=section,
                )
                if prefix:
                    prefixed_texts[idx] = prefix + text
            financial_payloads.append({
                "financial_keywords": info["keywords"],
                "statement_type": info.get("title") or info["statement_type"],
            })

        n_prefixed = sum(
            1 for i in range(len(prefixed_texts))
            if prefixed_texts[i] != all_texts[i]
        )
        if n_prefixed:
            logger.info(f"   📊 注入财务语义前缀: {n_prefixed}/{len(all_texts)} 个文本片段")

        # ── 表格→自然语言转换（提升语义检索质量） ──────────────
        n_table_converted = 0
        meta_company = metadata.get("company", "")
        meta_year = metadata.get("year", "")
        for i in range(n_parents):
            if parent_chunks[i].has_table:
                stype = financial_payloads[i].get("statement_type", "")
                nl = table_to_natural_language(parent_chunks[i].text,
                                               company=meta_company,
                                               year=meta_year,
                                               statement_type=stype)
                if nl:
                    prefixed_texts[i] = nl
                    n_table_converted += 1
        for j, (child, parent) in enumerate(child_parent_map):
            if child.has_table:
                idx = n_parents + j
                stype = financial_payloads[idx].get("statement_type", "")
                nl = table_to_natural_language(child.text,
                                               company=meta_company,
                                               year=meta_year,
                                               statement_type=stype)
                if nl:
                    prefixed_texts[idx] = nl
                    n_table_converted += 1
        if n_table_converted:
            logger.info(f"   📝 表格→自然语言: {n_table_converted}/{len(all_texts)} 个文本片段")

        # ── 向量化（分块编码防 OOM）──────────────────────────────
        # 大文档（如千块重组报告）一次 encode 全部片段会导致内存 swap
        # 拆成 MICRO_BATCH 个小批次，每批后强制 GC
        MICRO_BATCH = 200
        all_vectors_list = []
        logger.info(f"   🧠 编码 {len(prefixed_texts)} 个文本片段 (批次={MICRO_BATCH}) ...")
        t0 = time.perf_counter()
        for chunk_start in range(0, len(prefixed_texts), MICRO_BATCH):
            chunk_end = min(chunk_start + MICRO_BATCH, len(prefixed_texts))
            chunk_texts = prefixed_texts[chunk_start:chunk_end]
            chunk_vecs = embedder.encode(chunk_texts)
            all_vectors_list.append(chunk_vecs)
            # 每批后强制回收内存
            import gc
            gc.collect()
        if len(all_vectors_list) == 1:
            all_vectors = all_vectors_list[0]
        else:
            all_vectors = np.concatenate(all_vectors_list, axis=0)
        del all_vectors_list
        gc.collect()
        elapsed = time.perf_counter() - t0
        logger.info(f"   ✅ 编码完成 ({elapsed:.1f}s), 维度={all_vectors.shape[1]}")
        logger.info(f"      (使用前缀后编码，提升表格 chunk 的语义匹配)")

        # ── 计算 doc_id（sanitized filename）──────────────────────
        fname = metadata.get("source_file", "")
        doc_id = re.sub(r'[^\w\-_]', '_', fname.rsplit(".", 1)[0]) if fname else ""

        # ── 构建 Point 列表 ──────────────────────────────────────
        points = []

        for i, parent in enumerate(parent_chunks):
            finfo = financial_payloads[i]
            # 计算 prefix_title（预览前缀内容）
            prefix_text = prefixed_texts[i]
            prefix_title = prefix_text[:80] if prefix_text != all_texts[i] else ""
            points.append(models.PointStruct(
                id=self._make_id(parent.id),
                vector=all_vectors[i].tolist(),
                payload={
                    **metadata,
                    "doc_id": doc_id,
                    "source": metadata.get("source", ""),
                    "is_parent": True,
                    "parent_id": parent.id,
                    "chunk_index": parent.index,
                    "chunk_type": "parent",
                    "has_table": parent.has_table,
                    "text": parent.text,              # 父块保留完整文本
                    "char_count": parent.char_count,
                    "financial_keywords": finfo["financial_keywords"],
                    "statement_type": finfo["statement_type"],
                    "section": parent.section,
                    "prefix_title": prefix_title,
                    "table_summary": parent.table_summary,
                    "importance": compute_importance(
                        metadata.get("doc_type", "未知"),
                        parent.char_count,
                        metadata.get("company", ""),
                    ),
                    "page": parent.page,
                }
            ))

        for j, (child, parent) in enumerate(child_parent_map):
            finfo = financial_payloads[n_parents + j]
            prefix_text = prefixed_texts[n_parents + j]
            prefix_title = prefix_text[:80] if prefix_text != all_texts[n_parents + j] else ""
            points.append(models.PointStruct(
                id=self._make_id(child.id),
                vector=all_vectors[n_parents + j].tolist(),
                payload={
                    **metadata,
                    "doc_id": doc_id,
                    "source": metadata.get("source", ""),
                    "is_parent": False,
                    "parent_id": parent.id,
                    "chunk_index": child.index,
                    "chunk_type": "child",
                    "has_table": child.has_table,
                    "text": child.text[:500],
                    "search_text": child.search_text[:1000] if child.search_text else child.text[:500],
                    "char_count": child.char_count,
                    "financial_keywords": finfo["financial_keywords"],
                    "statement_type": finfo["statement_type"],
                    "section": child.section,
                    "prefix_title": prefix_title,
                    "table_summary": child.table_summary,
                    "importance": compute_importance(
                        metadata.get("doc_type", "未知"),
                        child.char_count,
                        metadata.get("company", ""),
                    ),
                    "page": child.page,
                }
            ))

        # ── 批量写入 ────────────────────────────────────────────
        # Qdrant 单次上传建议 ≤ 1000 点
        batch_size = 500
        total_points = len(points)
        for batch_start in range(0, total_points, batch_size):
            batch = points[batch_start:batch_start + batch_size]
            try:
                self._client.upsert(
                    collection_name=col_name,
                    points=batch,
                    wait=True,  # 确保写入完成
                )
            except RpcError as e:
                logger.error(f"   ❌ Qdrant 写入失败: {e}")
                raise

        logger.info(f"   ✅ 写入 {total_points} 个向量 ({n_parents} 父 + {total_points - n_parents} 子)")

        return n_parents

    def _make_id(self, uid: str) -> int:
        """UUID → Qdrant 兼容的 64bit int ID（取前 8 字节）。"""
        return uuid.UUID(uid).int & 0x7FFFFFFFFFFFFFFF

    def close(self):
        """断开连接（内存模式无需操作）。"""
        if self._owns_client and self._client is not None and self.cfg.QDRANT_MODE in ("disk",):
            self._client.close()
        self._client = None


# ══════════════════════════════════════════════════════════════════════
#  主流程
# ══════════════════════════════════════════════════════════════════════

def process_all(config: Config, force_recreate: bool = False, dry_run: bool = False):
    """全量入库流水线。"""

    # 1. 初始化组件
    embedder = EmbeddingEngine(config)
    indexer = QdrantIndexer(config)
    splitter = ParentChildSplitter(config)
    meta_extractor = MetadataExtractor(config)

    if not dry_run:
        indexer.ensure_collection(force_recreate=force_recreate)

    # 2. 收集待处理的 .md 文件
    md_files = sorted(config.PARSED_DIR.glob("*.md"))
    if not md_files:
        logger.warning(f"⚠️  {config.PARSED_DIR} 中没有 .md 文件。请先运行阶段一解析。")
        return

    logger.info(f"\n{'='*60}")
    logger.info(f"  finRAG 层级索引流水线 (Phase 2)")
    logger.info(f"  Markdown 文件: {len(md_files)}")
    logger.info(f"  分块策略: 父块 ~{config.PARENT_CHAR_TARGET}字, 子块 ~{config.CHILD_CHAR_TARGET}字")
    logger.info(f"  嵌入模型: {config.EMBED_MODEL} (dim={config.EMBED_DIM})")
    logger.info(f"  目标集合: {config.QDRANT_COLLECTION}")
    if dry_run:
        logger.info(f"  🔍 DRY-RUN 模式（不写入向量库）")
    logger.info(f"{'='*60}\n")

    try:
        total_parents = 0
        total_children = 0
        total_start = time.perf_counter()

        for idx, md_path in enumerate(md_files, 1):
            logger.info(f"[{idx:3d}/{len(md_files)}] 📄 {md_path.name}")

            md_text = md_path.read_text(encoding="utf-8")
            if not md_text.strip():
                logger.warning(f"      ⚠️  空文件，跳过")
                continue

            # 元数据提取
            metadata = meta_extractor.extract(md_path, md_text)
            logger.info(f"      🏷️  元数据: {metadata['company']} | {metadata['year']} | {metadata['doc_type']} | source={metadata['source']}")

            # 父子分块
            doc_start = time.perf_counter()
            parents = splitter.split(md_text)
            split_time = time.perf_counter() - doc_start

            children_count = sum(len(p.children) for p in parents)

            logger.info(f"      ✂️  {len(parents)} 父块, {children_count} 子块 ({split_time:.2f}s)")

            if dry_run:
                # 展示第一个父块和子块
                if parents:
                    p = parents[0]
                    logger.info(f"      📋 首个父块 ({p.char_count}字, 章节={p.section}): {p.text[:120]}...")
                    if p.children:
                        c = p.children[0]
                        logger.info(f"      📋 首个子块 ({c.char_count}字, 章节={c.section}): {c.text[:120]}...")
                continue

            # 入库
            n_parents = indexer.index_document(metadata, parents, embedder)
            total_parents += n_parents
            total_children += children_count

            # 强制内存回收（大文档后释放 tokenized 缓存）
            import gc
            gc.collect()

        total_elapsed = time.perf_counter() - total_start
        logger.info(f"\n{'='*60}")
        if not dry_run:
            logger.info(f"  ✅ 入库完成！")
            logger.info(f"  总向量: {total_parents} 父 + {total_children} 子 = {total_parents + total_children}")
        else:
            logger.info(f"  🔍 DRY-RUN 完成（未写入任何向量）")
            logger.info(f"  预览: {total_parents} 父块 + {total_children} 子块")
        logger.info(f"  耗时: {total_elapsed:.1f} 秒")
        logger.info(f"{'='*60}\n")
    finally:
        indexer.close()
    if not dry_run and total_parents > 0:
        logger.info("⏳ 构建 BM25 关键词索引...")
        try:
            sys.path.insert(0, str(config._BASE_DIR))
            from scripts.retriever import HybridRetriever
            retriever = HybridRetriever(config)
            retriever.build_bm25_index()
            logger.info("   ✅ BM25 索引构建完成\n")
        except Exception as e:
            logger.warning(f"   ⚠️  BM25 索引构建失败: {e}\n")


# ══════════════════════════════════════════════════════════════════════
#  CLI
# ══════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="finRAG 层级索引与向量库构建 — Parent-Child + BGE + Qdrant",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python scripts/ingestion_v2.py                    # 全量入库（追加）
  python scripts/ingestion_v2.py --clean            # 重建索引+全量入库
  python scripts/ingestion_v2.py --dry-run          # 预览分块结果
  python scripts/ingestion_v2.py --mode disk        # 持久化到磁盘
        """,
    )
    parser.add_argument("--clean", action="store_true", help="清空集合后重建索引")
    parser.add_argument("--dry-run", action="store_true", help="预览分块，不写入向量库")
    parser.add_argument("--mode", choices=["memory", "disk", "remote"], default=None,
                        help="Qdrant 运行模式（默认: memory）")
    args = parser.parse_args()

    cfg = Config()
    if args.mode:
        cfg.QDRANT_MODE = args.mode

    process_all(cfg, force_recreate=args.clean, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
