#!/usr/bin/env python3
"""
finRAG — Phase 1: 工业级文档解析流水线
===========================================
支持两种后端：
  1. IBM Docling（优先）—— 布局最强，自动识别标题/段落/表格
  2. PyMuPDF (fitz) —— 轻量兜底，无需 PyTorch

核心改进：自动检测 PDF 类型
  - 文本型 → PyMuPDF（快，无需 OCR）
  - 扫描件 → Docling（布局 + OCR 强）

用法:
    python scripts/ingestion.py                        # 处理 data/raw/ 下所有 PDF
    python scripts/ingestion.py --pdf report.pdf       # 处理单个文件
    python scripts/ingestion.py --clean                # 清理输出目录重新解析
"""

import logging

# 抑制 RapidOCR/Docling 的 INFO 日志（它们只在解析扫描件时才需要）
for _r in ("rapidocr", "docling", "pytorch_lightning", "lightning"):
    logging.getLogger(_r).setLevel(logging.WARNING)
logging.getLogger().setLevel(logging.WARNING)

import argparse
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

import pandas as pd

# ── 解析后端可用性探测（懒加载：仅在需要时导入）─────────────────
import importlib.util

_BACKEND = None
_BACKEND_NAME = ""

# 检查 Docling 是否可用（用 find_spec 避免触发初始化）
if importlib.util.find_spec("docling"):
    _BACKEND = "docling"
    _BACKEND_NAME = "IBM Docling"

# 检查 PyMuPDF
if _BACKEND is None:
    import fitz  # noqa: F401 — 检查安装
    _BACKEND = "pymupdf"
    _BACKEND_NAME = "PyMuPDF (fitz)"

if _BACKEND is None:
    print("❌ 请安装 PDF 解析库之一:")
    print("   pip install docling               # 推荐（布局最强）")
    print("   pip install pymupdf pandas         # 轻量替代")
    sys.exit(1)

logger = logging.getLogger("ingestion")


# ── PDF 类型检测（文本型 vs 扫描型）────────────────────────────────
def detect_pdf_type(pdf_path: str, text_threshold: int = 80,
                    text_ratio: float = 0.8) -> str:
    """
    检测 PDF 是『文本型』还是『扫描型』。

    用 PyMuPDF 快速扫描每一页：
    - 如果 ≥ text_ratio 的页有 meaningful 文本 → 'text'
    - 否则 → 'scanned'

    参数:
        text_threshold: 一页至少多少个字符才算『有文本』
        text_ratio:     有文本的页数占比下限
    """
    try:
        import fitz
        doc = fitz.open(pdf_path)
        total = len(doc)
        if total == 0:
            return "text"  # 空 PDF 走轻量路径

        text_pages = sum(
            1 for i in range(total)
            if len(doc[i].get_text("text").strip()) > text_threshold
        )
        doc.close()

        ratio = text_pages / total
        result = "text" if ratio >= text_ratio else "scanned"
        icon = "📝" if result == "text" else "🔍"
        print(f"      {icon} 类型检测: {text_pages}/{total} 页有文本 ({ratio:.0%}), 判定={result}")
        return result
    except ImportError:
        print("      ⚠️  无 PyMuPDF，无法检测类型，默认优先选择 Docling")
        return "scanned"
    except Exception as e:
        print(f"      ⚠️  类型检测失败 ({e})，默认走 Docling")
        return "scanned"


# ══════════════════════════════════════════════════════════════════════
#  配置
# ══════════════════════════════════════════════════════════════════════

@dataclass
class Config:
    """项目路径与解析参数。根据需要修改 _BASE_DIR 即可。"""
    _BASE_DIR: Path = Path(__file__).resolve().parents[1]

    RAW_DIR: Path = _BASE_DIR / "data" / "raw"
    PARSED_DIR: Path = _BASE_DIR / "data" / "parsed"
    METADATA_PATH: Path = _BASE_DIR / "data" / "metadata.json"

    # 后处理正则（可在此按需添加/修改规则）
    HEADER_FOOTER_PATTERNS: list = field(default_factory=lambda: [
        r"^\d+\s*$",                           # 孤立的页码
        r"^(第\s*\d+\s*页|Page\s+\d+)\s*$",    # 第 X 页 / Page X
        r"^免责声明.*$",                         # 免责声明整行（谨慎使用）
    ])
    MERGE_SHORT_LINES_MAX_LEN: int = 40         # 小于此长度的行尝试合并到下一行
    TABLE_LABEL_PATTERN: str = r"^(表\s*\d|Table\s*\d)"  # 表头识别


# ══════════════════════════════════════════════════════════════════════
#  元数据模型
# ══════════════════════════════════════════════════════════════════════

@dataclass
class DocumentMetadata:
    filename: str                        # 原始文件名
    source_path: str                     # 原始文件绝对路径
    parsed_path: str                     # 输出的 .md 路径
    file_size_bytes: int = 0             # PDF 大小
    page_count: int = 0                  # PDF 页数
    char_count: int = 0                  # 解析后的字符数
    table_count: int = 0                 # 检测到的表格数量
    has_financial_tables: bool = False   # 是否包含疑似财务报表
    parse_duration_sec: float = 0.0      # 解析耗时
    status: str = "unknown"              # success / error
    error: Optional[str] = None


# ══════════════════════════════════════════════════════════════════════
#  后处理器
# ══════════════════════════════════════════════════════════════════════

class MarkdownCleaner:
    """对 Docling 输出的 Markdown 进行金融场景专项清洗。"""

    def __init__(self, config: Config):
        self.cfg = config
        self._table_count = 0
        self._has_financial_tables = False

    def clean(self, raw_md: str) -> str:
        """完整清洗流水线：按顺序执行所有清洗步骤。"""
        self._table_count = 0
        self._has_financial_tables = False

        text = raw_md

        # 1) 移除页码 / 页眉 / 页脚
        text = self._remove_headers_footers(text)

        # 2) 去除空行过多的冗余空白
        text = re.sub(r"\n{4,}", "\n\n\n", text)

        # 3) 合并被 PDF 强制换行的短行
        text = self._merge_short_lines(text)

        # 4) 确保表格上方标题不被切断（回头看）
        text = self._protect_table_labels(text)

        # 5) 统计表格
        self._count_tables(text)

        return text.strip()

    # ── 子步骤 ────────────────────────────────────────────────────

    def _remove_headers_footers(self, text: str) -> str:
        lines = text.split("\n")
        filtered = []
        for line in lines:
            stripped = line.strip()
            # 跳过完全匹配页眉/页脚模式的行
            skip = False
            for pat in self.cfg.HEADER_FOOTER_PATTERNS:
                if re.match(pat, stripped):
                    skip = True
                    break
            if not skip:
                filtered.append(line)
        return "\n".join(filtered)

    def _merge_short_lines(self, text: str) -> str:
        """将孤立的短行（如 PDF 强制换行的半行文本）合并到下一行。"""
        lines = text.split("\n")
        merged = []
        i = 0
        while i < len(lines):
            current = lines[i]
            stripped = current.strip()

            # 如果是空行、表格行（以 | 开头）、列表项（- / *）、标题（#），不合并
            if (not stripped
                or stripped.startswith("|")
                or stripped.startswith("- ")
                or stripped.startswith("* ")
                or stripped.startswith("#")
                or stripped.startswith(">")
                or stripped.startswith("```")
                or re.match(r"^\d+[.、)]", stripped)):
                merged.append(current)
                i += 1
                continue

            # 检查是否短行且下一行是普通文本
            if (len(stripped) < self.cfg.MERGE_SHORT_LINES_MAX_LEN
                and i + 1 < len(lines)
                and lines[i + 1].strip()
                and not lines[i + 1].strip().startswith("|")
                and not lines[i + 1].strip().startswith("#")
                and not lines[i + 1].strip().startswith("- ")
                and not lines[i + 1].strip().startswith("```")):
                # 合并到下一行（用空格连接）
                lines[i + 1] = current + " " + lines[i + 1].strip()
            else:
                merged.append(current)
            i += 1
        return "\n".join(merged)

    def _protect_table_labels(self, text: str) -> str:
        """确保表格描述行（如"表 1-1 资产负债表"）紧贴表格，不被分离。"""
        lines = text.split("\n")
        protected = []
        for i, line in enumerate(lines):
            protected.append(line)
            # 如果当前行是表标题，且下一行不是表格开头（|），
            # 且再下一行是表格 → 交换顺序
            if (re.search(self.cfg.TABLE_LABEL_PATTERN, line.strip())
                and i + 2 < len(lines)
                and not lines[i + 1].strip().startswith("|")
                and lines[i + 2].strip().startswith("|")):
                # 把中间的干扰行下移
                protected[-1], protected[-2] = protected[-2], protected[-1]
        return "\n".join(protected)

    def _count_tables(self, text: str) -> None:
        """统计 Markdown 表格数量并检测是否包含财务表格。"""
        lines = text.split("\n")
        in_table = False
        for line in lines:
            if line.strip().startswith("|"):
                if not in_table:
                    self._table_count += 1
                    in_table = True
                # 检测财务特征：包含"万元、亿元、营业收入、净利润、资产"等关键词
                if re.search(r"(万元|亿元|营业收入|净利润|资产|负债|股东权益|利润|现金流|合计)", line):
                    self._has_financial_tables = True
            else:
                in_table = False

    @property
    def table_count(self) -> int:
        return self._table_count

    @property
    def has_financial_tables(self) -> bool:
        return self._has_financial_tables


# ══════════════════════════════════════════════════════════════════════
#  解析器引擎
# ══════════════════════════════════════════════════════════════════════

class PDFParser:
    """统一 PDF 解析接口。

    根据 pdf_type 选择后端：
      - 'text'    → PyMuPDF（快，不需要 OCR）
      - 'scanned' → Docling（布局 + OCR 强）
      - None      → 自动选择（Docling > PyMuPDF）
    """

    def __init__(self, pdf_type: Optional[str] = "text"):
        self._backend = _BACKEND  # 整体可用性
        self._name = _BACKEND_NAME

        if pdf_type == "scanned" and self._backend == "docling":
            # 扫描件 → Docling（懒加载，不触发顶部导入）
            from docling.document_converter import DocumentConverter
            self._converter = DocumentConverter()
            print(f"   🔍 后端: {self._name}（扫描件 OCR 模式）")
        elif pdf_type == "scanned" and self._backend != "docling":
            print(f"   ⚠️  扫描件但 Docling 未安装，降级至 PyMuPDF")
            self._backend = "pymupdf"
            self._name = "PyMuPDF (fitz)"
        elif pdf_type == "text" or self._backend == "pymupdf":
            # 文本型 → PyMuPDF（或兜底）
            self._backend = "pymupdf"
            self._name = "PyMuPDF (fitz)"
            print(f"   🔍 后端: {self._name}（文本型，轻量快速）")
        elif self._backend == "docling":
            # 无检测信息且 Docling 可用
            from docling.document_converter import DocumentConverter
            self._converter = DocumentConverter()
            print(f"   🔍 后端: {self._name}")

    def parse(self, pdf_path: str) -> tuple[Optional[str], Optional[str], int, str]:
        """解析 PDF 并返回 (markdown_text, error_message, page_count, status)。"""
        if self._backend == "docling":
            return self._parse_with_docling(pdf_path)
        elif self._backend == "pymupdf":
            return self._parse_with_pymupdf(pdf_path)
        return None, "没有可用的解析后端", 0, "error"

    def _parse_with_docling(self, pdf_path: str):
        try:
            start = time.perf_counter()
            result = self._converter.convert(pdf_path)
            _ = time.perf_counter() - start  # elapsed
            doc = result.document
            page_count = len(doc.pages) if doc.pages else 0
            md_text = doc.export_to_markdown()
            return md_text, None, page_count, "success"
        except Exception as e:
            return None, str(e), 0, "error"

    def _parse_with_pymupdf(self, pdf_path: str):
        """PyMuPDF 轻量解析：提取文本 + 基本布局（表格以文本形式保留）。"""
        import fitz
        try:
            start = time.perf_counter()
            doc = fitz.open(pdf_path)
            _ = time.perf_counter() - start
            page_count = len(doc)
            md_pages = []

            for page_num in range(page_count):
                page = doc[page_num]
                blocks = page.get_text("dict")["blocks"]
                md_blocks = []

                for block in blocks:
                    if block["type"] == 0:  # 文本块
                        lines = []
                        for line in block["lines"]:
                            text = "".join(
                                span["text"] for span in line["spans"]
                            ).strip()
                            if text:
                                lines.append(text)
                        if lines:
                            # 根据字体大小判断标题级别
                            first_span = block["lines"][0]["spans"][0]
                            font_size = first_span["size"]
                            line_text = " ".join(lines)
                            if font_size > 16:
                                md_blocks.append(f"# {line_text}")
                            elif font_size > 13:
                                md_blocks.append(f"## {line_text}")
                            else:
                                md_blocks.append(line_text)

                    elif block["type"] == 1:  # 图片块
                        # 图片用替代文本占位
                        md_blocks.append("<!-- image -->")

                md_pages.append("\n\n".join(md_blocks))

            doc.close()
            md_text = "\n\n---\n\n".join(md_pages)
            return md_text, None, page_count, "success"
        except Exception as e:
            return None, str(e), 0, "error"


# ══════════════════════════════════════════════════════════════════════
#  入口：批量处理
# ══════════════════════════════════════════════════════════════════════

def process_all(config: Config) -> None:
    """处理 data/raw/ 下所有 PDF，输出到 data/parsed/ 并生成 metadata.json。"""
    config.RAW_DIR.mkdir(parents=True, exist_ok=True)
    config.PARSED_DIR.mkdir(parents=True, exist_ok=True)

    pdf_files = sorted(config.RAW_DIR.glob("*.pdf"))
    if not pdf_files:
        print(f"⚠️  {config.RAW_DIR} 中没有 PDF 文件。请先放入研报或财报 PDF。")
        return

    all_metadata = []
    total_start = time.perf_counter()

    print(f"\n{'='*60}")
    print(f"  finRAG 文档解析流水线")
    print(f"  PDF 数量: {len(pdf_files)}")
    print(f"  输出目录: {config.PARSED_DIR}")
    print(f"{'='*60}\n")

    for idx, pdf_path in enumerate(pdf_files, 1):
        stem = pdf_path.stem                      # 不含扩展名的文件名
        md_path = config.PARSED_DIR / f"{stem}.md"

        print(f"[{idx:3d}/{len(pdf_files)}] 📄 {pdf_path.name}")

        meta = DocumentMetadata(
            filename=pdf_path.name,
            source_path=str(pdf_path.resolve()),
            parsed_path=str(md_path.resolve()),
            file_size_bytes=pdf_path.stat().st_size,
        )

        # ── 1) 检测 PDF 类型 ──────────────────────────────────────
        pdf_type = detect_pdf_type(str(pdf_path))

        # ── 2) 根据类型选择解析器 ──────────────────────────────────
        parser = PDFParser(pdf_type=pdf_type)
        cleaner = MarkdownCleaner(config)

        # ── 3) 解析 ────────────────────────────────────────────────
        parse_start = time.perf_counter()
        md_text, error, page_count, status = parser.parse(str(pdf_path))
        meta.parse_duration_sec = time.perf_counter() - parse_start
        meta.page_count = page_count
        meta.status = status

        if status == "error":
            meta.error = error
            all_metadata.append(asdict(meta))
            print(f"      ❌ 解析失败: {error[:120]}")
            continue

        # ── 清洗 ────────────────────────────────────────────────
        md_text = cleaner.clean(md_text)
        meta.char_count = len(md_text)
        meta.table_count = cleaner.table_count
        meta.has_financial_tables = cleaner.has_financial_tables

        # ── 写出 Markdown ───────────────────────────────────────
        md_path.write_text(md_text, encoding="utf-8")
        all_metadata.append(asdict(meta))

        summary = (
            f"      ✅ {page_count}页, "
            f"{meta.char_count:,}字符, "
            f"{meta.table_count}个表格"
        )
        if meta.has_financial_tables:
            summary += " 💰含财务表格"
        print(summary)

    # ── 写入元数据 ──────────────────────────────────────────────
    total_elapsed = time.perf_counter() - total_start
    metadata_json = {
        "pipeline": "finRAG ingestion v1",
        "total_files": len(pdf_files),
        "success_count": sum(1 for m in all_metadata if m["status"] == "success"),
        "error_count": sum(1 for m in all_metadata if m["status"] == "error"),
        "total_duration_sec": round(total_elapsed, 2),
        "documents": all_metadata,
    }
    config.METADATA_PATH.write_text(
        json.dumps(metadata_json, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"\n{'='*60}")
    print(f"  ✅ 完成！耗时 {total_elapsed:.1f} 秒")
    print(f"  ✅ Markdown: {config.PARSED_DIR}")
    print(f"  ✅ 元数据:   {config.METADATA_PATH}")
    print(f"  ✅ 成功 {metadata_json['success_count']}/{metadata_json['total_files']}")
    print(f"{'='*60}\n")


def process_single(pdf_path: str, config: Config) -> None:
    """处理单个 PDF 文件。"""
    p = Path(pdf_path)
    if not p.exists():
        print(f"❌ 文件不存在: {pdf_path}")
        return
    if p.suffix.lower() != ".pdf":
        print(f"❌ 不是 PDF 文件: {pdf_path}")
        return

    # 复制到 raw 目录，然后批量处理
    import shutil
    dest = config.RAW_DIR / p.name
    shutil.copy2(str(p), str(dest))
    print(f"📋 已复制到 {dest}，开始批量处理...\n")
    process_all(config)


# ══════════════════════════════════════════════════════════════════════
#  CLI
# ══════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="finRAG 文档解析流水线 — 基于 IBM Docling 的金融 PDF → Markdown",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python scripts/ingestion.py                        # 处理 data/raw/ 下全部 PDF
  python scripts/ingestion.py --pdf 贵州茅台年报.pdf   # 处理单个文件
  python scripts/ingestion.py --clean                 # 清空 parsed 目录后重新处理
        """,
    )
    parser.add_argument("--pdf", type=str, help="处理单个 PDF 文件路径")
    parser.add_argument("--clean", action="store_true", help="清空输出目录后重新处理")
    args = parser.parse_args()

    cfg = Config()

    if args.clean:
        import shutil
        if cfg.PARSED_DIR.exists():
            shutil.rmtree(str(cfg.PARSED_DIR))
            print(f"🧹 已清空 {cfg.PARSED_DIR}")
        if cfg.METADATA_PATH.exists():
            cfg.METADATA_PATH.unlink()
            print(f"🧹 已删除 {cfg.METADATA_PATH}")
        cfg.PARSED_DIR.mkdir(parents=True, exist_ok=True)

    if args.pdf:
        process_single(args.pdf, cfg)
    else:
        process_all(cfg)


if __name__ == "__main__":
    main()
