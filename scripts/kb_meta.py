"""
finRAG — 知识库元数据构建工具
==================================
从多种来源自动构建公司名↔文档名的映射关系。
用于：
  1. ingestion_v2 的 MetadataExtractor（解析时决定 company/year/doc_type）
  2. graph.py 的 COMPANY_NAME_MAP（检索时过滤）
  3. 后续新增文档的自动注册

用法:
    from scripts.kb_meta import build_metadata_index
    index = build_metadata_index()
    info = index.get_company_info("天齐锂业")
    print(info)  # -> {company: "天齐锂业", stocks: [...], aliases: [...]}
"""
import json
import re
from pathlib import Path
from typing import Optional

# 项目根目录
_BASE = Path(__file__).resolve().parents[1]


class KnowledgeBaseIndex:
    """
    本地知识库元数据索引。
    
    自动从以下源构建：
    1. data/raw/report_metadata.json  — 券商研报元数据（org, stock, title）
    2. data/parsed/*.md               — 已解析文档的文件名结构
    3. 上述组合推导出的公司别名词典
    """

    def __init__(self):
        self._stocks: set[str] = set()           # 所有已知股票名/公司名
        self._orgs: set[str] = set()             # 所有券商机构名
        self._company_aliases: dict[str, list[str]] = {}  # 别名映射
        self._filename_stock_map: dict[str, str] = {}   # 文件名→公司名
        self._stock_filenames: dict[str, list[str]] = {} # 公司名→文件名列表
        self._broker_metadata: list = []         # 原始研报 metadata
        self._built = False

    def build(self) -> "KnowledgeBaseIndex":
        """构建完整索引（幂等）。"""
        if self._built:
            return self
        self._load_broker_metadata()
        self._build_stock_set()
        self._build_aliases()
        self._built = True
        return self

    def _load_broker_metadata(self):
        """加载券商研报元数据。"""
        meta_path = _BASE / "data" / "raw" / "report_metadata.json"
        if meta_path.exists():
            with open(meta_path, encoding="utf-8") as f:
                self._broker_metadata = json.load(f)

    def _build_stock_set(self):
        """从所有源构建公司名全集。"""
        # 1. 从研报元数据
        for r in self._broker_metadata:
            stock = r.get("stock", "").strip()
            org = r.get("org", "").strip()
            if stock:
                self._stocks.add(stock)
            if org:
                self._orgs.add(org)

        # 2. 从 parsed 目录中的文件名（补充财报类 PDF）
        for f in sorted((_BASE / "data" / "parsed").glob("*.md")):
            self._extract_from_filename(f.stem)

        # 3. 从 raw 目录补充其他 PDF
        raw_dir = _BASE / "data" / "raw"
        if raw_dir.exists():
            for f in sorted(raw_dir.glob("*.pdf")):
                if f.stat().st_size > 10000:
                    self._extract_from_filename(f.stem)

    def _extract_from_filename(self, stem: str):
        """从文件名提取公司信息。"""
        # 模式1: 券商_公司_...（研报）→ 公司在第2段
        parts = stem.split("_")
        if len(parts) >= 2:
            # 如果第1段是券商机构名 → 第2段是公司名
            if parts[0] in {"东吴证券", "中邮证券", "中银证券", "信达证券",
                            "华源证券", "西南证券", "华泰证券", "中信证券",
                            "国泰君安", "海通证券", "申万宏源", "招商证券",
                            "广发证券", "国信证券"}:
                self._stocks.add(parts[1])
                return

        # 模式2: 特变电工_问询函 → 特变电工
        if len(parts) >= 2 and parts[0] not in (
            "chongzu", "zhaogu", "peigu", "credit_rating", "kezhuanzhai"
        ):
            # 第一个下划线前的可能是公司名或前缀
            if not any(kw in parts[0] for kw in [
                "2025", "2026", "年度", "法律", "审计", "股权", "资产",
                "chongzu", "zhaogu", "peigu", "kezhuanzhai", "credit",
            ]):
                self._stocks.add(parts[0])

        # 模式3: wuliangye_2025 → 五粮液 (使用规范化映射)
        FAMOUS_STOCKS = {
            "wuliangye": "五粮液", "maotai": "贵州茅台",
            "pingan": "中国平安", "midu": "美的集团",
            "kweichow": "贵州茅台", "yili": "伊利股份",
        }
        for key, name in FAMOUS_STOCKS.items():
            if key in stem.lower():
                self._stocks.add(name)

    def _build_aliases(self):
        """构建公司名 ↔ 别名映射。"""
        # 手动映射表（只覆盖核心上市公司简称→全称/股票名）
        self._company_aliases = {
            # 白酒
            "五粮液": ["五粮液", "宜宾五粮液", "宜宾五粮液股份", "WULIANGYE"],
            "贵州茅台": ["茅台", "贵州茅台", "MAOTAI"],
            "泸州老窖": ["泸州老窖"],
            "洋河股份": ["洋河", "洋河股份"],
            "山西汾酒": ["汾酒", "山西汾酒"],
            # 从研报metadata自动生成
        }

        # 自动从 metadata 生成别名
        for r in self._broker_metadata:
            stock = r.get("stock", "").strip()
            if stock and stock not in self._company_aliases:
                # 创建简短别名
                short = stock.replace("股份", "").replace("集团", "")
                self._company_aliases[stock] = [stock, short]

        # 补充常见映射
        extra_aliases = {
            "宁德时代": ["宁德时代", "CATL"],
            "比亚迪": ["比亚迪", "BYD"],
            "腾讯": ["腾讯", "腾讯控股", "腾讯控股有限公司"],
            "阿里巴巴": ["阿里巴巴", "阿里"],
        }
        for k, v in extra_aliases.items():
            if k not in self._company_aliases:
                self._company_aliases[k] = v

    def search_company(self, name: str) -> Optional[str]:
        """
        根据用户输入的简称/全称查找标准公司名。
        "天齐锂业" → "天齐锂业"
        "茅台" → "贵州茅台"
        "五粮液" → "五粮液"
        """
        name = name.strip()
        # 1. 精确匹配股票名
        if name in self._stocks:
            return name
        # 2. 别名反向查找
        for standard, aliases in self._company_aliases.items():
            if name in aliases or any(name in a for a in aliases):
                return standard
        # 3. 子串模糊匹配（阈值 >=2个中文字符且是子串）
        name_short = name.replace("股份", "").replace("集团", "").replace("有限", "")
        for stock in self._stocks:
            stock_short = stock.replace("股份", "").replace("集团", "").replace("有限", "")
            if len(name_short) >= 2 and (name_short in stock_short or stock_short in name_short):
                return stock
        return None

    def extract_company_from_text(self, text: str) -> Optional[str]:
        """
        从任意文本中提取已知公司名。
        用于 query_rewriter LLM 提取失败时的兜底。
        
        尝试策略：
        1. 从长到短匹配已知股票名
        2. 别名匹配
        3. 子串模糊匹配
        """
        # 按长度降序排列（优先匹配完整公司名，如"天齐锂业"匹配在"天齐"之前）
        stocks_by_len = sorted(self._stocks, key=len, reverse=True)
        
        for stock in stocks_by_len:
            if stock in text:
                return stock
        
        # 别名匹配
        for standard, aliases in self._company_aliases.items():
            for alias in aliases:
                if alias in text:
                    return standard

        # 子串模糊匹配
        for stock in stocks_by_len:
            stock_short = stock.replace("股份", "").replace("集团", "").replace("有限", "").strip()
            if len(stock_short) >= 2 and stock_short in text:
                return stock
        
        return None

    def get_broker_info(self, org: str, stock: str) -> Optional[dict]:
        """获取某券商对某股票的最新研报信息。"""
        for r in self._broker_metadata:
            if r.get("org") == org and r.get("stock") == stock:
                return r
        return None

    @property
    def all_stocks(self) -> list[str]:
        return sorted(self._stocks)

    @property
    def all_orgs(self) -> list[str]:
        return sorted(self._orgs)

    @property
    def company_name_map(self) -> dict[str, str]:
        """生成给 graph.py 用的 简称→全称 映射。"""
        mapping = {}
        for stock in self._stocks:
            short = stock.replace("股份", "").replace("集团", "")
            # 多个股票可能映射到同一简称，取原始名
            mapping[stock] = stock
        # 加上别名
        for standard, aliases in self._company_aliases.items():
            for alias in aliases:
                if alias not in mapping:
                    mapping[alias] = standard
        return mapping


# ── 全局单例 ────────────────────────────────────────────────
_global_index: KnowledgeBaseIndex = None


def get_kb_index() -> KnowledgeBaseIndex:
    """获取全局知识库索引（懒加载）。"""
    global _global_index
    if _global_index is None:
        _global_index = KnowledgeBaseIndex().build()
    return _global_index


def build_metadata_index() -> KnowledgeBaseIndex:
    """显式构建并返回索引。"""
    return KnowledgeBaseIndex().build()


if __name__ == "__main__":
    index = build_metadata_index()
    print(f"✅ 知识库元数据索引构建完成")
    print(f"   股票数: {len(index.all_stocks)}")
    print(f"   券商数: {len(index.all_orgs)}")
    print(f"   别名数: {len(index._company_aliases)}")
    
    # 测试几个查询
    for test in ["天齐锂业", "茅台", "五粮液", "牧原股份", "中邮证券"]:
        result = index.search_company(test)
        print(f"   search_company('{test}') → {result}")
