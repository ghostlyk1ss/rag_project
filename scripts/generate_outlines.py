#!/usr/bin/env python3
"""
finRAG — Phase 2a: 结构纲要索引
=================================
对每个长文档按 section 标题分组，用 LLM 为每个 section 生成
50-80 字的概要 (outline)，写入 Qdrant 作为独立 chunk。

查询时：用户问宏观/总结类问题 → 向量匹配 outline 摘要
→ 命中对应 section 的全文 → LLM 有完整上下文

用法:
    python scripts/generate_outlines.py                    # 全量生成纲要
    python scripts/generate_outlines.py --force             # 覆盖已存在的 outline
    python scripts/generate_outlines.py --dry-run           # 预览分段但不写入

依赖:
    - ingestion_v2.py 中的 Config, EmbeddingEngine, QdrantIndexer._make_id
    - DeepSeek API
"""

import argparse
import json
import logging
import os
import random
import re
import sys
import time
import uuid
import sys
import time
import uuid
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")
logger = logging.getLogger("generate_outlines")

# ── 文件级常量 ───────────────────────────────────────────────
MIN_SECTION_CHARS = 500        # 生成 outline 的最小 section 字符数
OUTLINE_TARGET_WORDS = "50-80" # 给 LLM 的指令
MAX_SECTION_CHARS = 3000       # 喂给 LLM 的 section 文本上限
OUTLINE_RATE_LIMIT = 0.3   # DeepSeek 速率限制间隔（秒）
BATCH_WRITE_SIZE = 50      # Qdrant 单次写入上限

# DeepSeek API
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
DEEPSEEK_MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")
DEEPSEEK_BASE = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")

# ── Section 标题检测（与 ParentChildSplitter 一致） ──────────
# 匹配：一、二、三... （一）（二）1. 1.1 # ## ###
SECTION_HEADER_PATTERN = re.compile(
    r'^(#{1,4}\s+.+|'                          # Markdown 标题
    r'[一二三四五六七八九十百千]+[、．\.]\s*.+|'  # 一、二、
    r'[（\(][一二三四五六七八九十百千]+[）\)]\s*.+|' # （一）(一)
    r'\d+[、．\.]\s*.+|'                         # 1、2、
    r'\d+\.\d+\s+.*|'                            # 1.1 xxx
    r'(?:第\s*[一二三四五六七八九十百千]+|Chapter\s+\d+)\s*.*)'  # 第一章
)

# 跳过非内容性标题（附录、免责等）
SKIP_SECTION_PATTERNS = re.compile(
    r'^(附录|免责|声明|风险提示|释义|目录|联系人|评级说明|'
    r'附注|参考|Appendix|Disclaimer|Risk|Definition|'
    r'分析师声明|投资评级|一般声明|法律声明)', re.IGNORECASE
)

# 财务报表格外保护（不把报表标题当 section 分割点）
FINANCIAL_STATEMENT_TITLES = {
    "合并利润表", "母公司利润表",
    "合并资产负债表", "母公司资产负债表",
    "合并现金流量表", "母公司现金流量表",
    "合并股东权益变动表", "母公司股东权益变动表",
}


def load_config_and_imports():
    """延迟导入避免启动时加载 PyTorch。"""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from scripts.ingestion_v2 import Config, EmbeddingEngine, QdrantIndexer
    return Config, EmbeddingEngine, QdrantIndexer


# ══════════════════════════════════════════════════════════════════
#  Section 分割
# ══════════════════════════════════════════════════════════════════

# Markdown 内容标题净化：真正的章节标题通常较短且不含数值/百分比
_CONTENT_PATTERN = re.compile(
    r'[%％％元亿万千百]\s*$|'   # 以单位结尾
    r'[\d.,]+\s*[%％]|'            # 含百分比
    r'\d+\.\d+|'                   # 含小数
    r'同比|环比|增长|下降|提高'     # 含同比环比等财务术语
    r'(?:。|；|，)$'                # 以句号/分号/逗号结尾（说明是完整句子）
)

# 真正章节标题的格式检测
_SECTION_TITLE_CONTENT = re.compile(
    r'[的与和及或与和之是]'  # 如果标题包含过多连接词/助词，可能是正文
)


def _is_valid_section_title(stripped: str) -> bool:
    """检测是否为真正的章节标题（排除被误检的正文行）。"""
    # 移除前导标记（#、一、1. 等），获取实际标题内容
    content = re.sub(
        r'^(#+\s*|[\d一二三四五六七八九十百千]+[、．\.]\s*'
        r'|[（\(][一二三四五六七八九十百千]+[）\)]\s*'
        r'|\d+\.\d+\s+'
        r'|第\s*[一二三四五六七八九十百千]+\s*)',
        '', stripped
    ).strip()

    if not content:
        return False

    title_len = len(content)

    # 太短（< 3 字）或太长（> 80 字）→ 不是真实标题
    if title_len < 3 or title_len > 80:
        return False

    # 如果开头就是中文标点 → 肯定是正文续行
    if content[0] in '。？！；：，、．':
        return False

    # 如果含财务数值/百分比 → 是正文，不是标题
    if _CONTENT_PATTERN.search(content):
        return False

    # 标题应该自包含——如果含",，；、和与"等连接词，且长度>15，说明是正文
    if title_len > 15 and re.search(r'[，、；]', content):
        return False

    # 如果含太多"的与和及"等 → 可能是正文片段
    if title_len > 20 and _SECTION_TITLE_CONTENT.search(content):
        return False

    # 正文句子通常以。？！结尾，标题从不
    if re.search(r'[。？！]$', content):
        return False

    # 真正的章节标题：以中文/英文大写字母/数字开头
    if not re.match(r'[\u4e00-\u9fff\u3400-\u4DBFA-Z0-9（(]', content):
        return False

    return True

def split_into_sections(text: str) -> list[dict]:
    """
    将文档按 section 标题分组。

    返回:
        [{
            "title": "三、公司业务",
            "text": "完整 section 文本...",
            "char_count": 1234,
            "order": 0,  # 在文档中的顺序
        }, ...]
    """
    lines = text.split("\n")
    sections = []
    current_title = "前言/概述"
    current_lines = []

    for i, line in enumerate(lines):
        stripped = line.strip()
        # 跳过空行
        if not stripped:
            current_lines.append(line)
            continue

        # 检测是否为 section 标题（正则匹配 + 语义校验）
        if SECTION_HEADER_PATTERN.match(stripped) and _is_valid_section_title(stripped):
            # 跳过财务报表格（如 "3、合并利润表" 是表格内容，非章节）
            if stripped in FINANCIAL_STATEMENT_TITLES or any(
                t in stripped for t in FINANCIAL_STATEMENT_TITLES
            ):
                current_lines.append(line)
                continue
            # 跳过非内容性 section
            if SKIP_SECTION_PATTERNS.match(stripped):
                current_lines.append(line)
                continue

            # flush 上一个 section
            if current_lines:
                section_text = "\n".join(current_lines).strip()
                if section_text:
                    sections.append({
                        "title": current_title,
                        "text": section_text,
                        "char_count": len(section_text),
                        "order": len(sections),
                    })

            # 开始新 section
            current_title = stripped[:80]  # 截断过长的标题
            current_lines = []
        else:
            current_lines.append(line)

    # flush 最后一个 section
    if current_lines:
        section_text = "\n".join(current_lines).strip()
        if section_text:
            sections.append({
                "title": current_title,
                "text": section_text,
                "char_count": len(section_text),
                "order": len(sections),
            })

    return sections


# ══════════════════════════════════════════════════════════════════
#  LLM outline 生成
# ══════════════════════════════════════════════════════════════════

OUTLINE_SYSTEM_PROMPT = """你是一个金融文档分析助手。你的任务是为文档的每个章节生成简洁的概要（outline）。

要求：
1. 用 {target_words} 个字概括该章节的核心内容
2. 抓住关键数据、观点和结论
3. 语言精炼，适合作为该章节的索引摘要
4. 只输出概要本身，不要额外解释"""

OUTLINE_USER_PROMPT = """请为以下章节生成 {target_words} 字的概要。

章节标题：{title}

章节内容（前 {max_chars} 字）：
{text}

概要："""


def call_llm_for_outline(title: str, text: str) -> str:
    """调用 DeepSeek 为 section 生成 outline。"""
    import urllib.request, urllib.error
    import ssl

    sample = text[:MAX_SECTION_CHARS]
    prompt = OUTLINE_USER_PROMPT.format(
        title=title, text=sample,
        max_chars=MAX_SECTION_CHARS,
        target_words=OUTLINE_TARGET_WORDS,
    )
    system_prompt = OUTLINE_SYSTEM_PROMPT.format(
        target_words=OUTLINE_TARGET_WORDS,
    )

    payload = json.dumps({
        "model": DEEPSEEK_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.1,
        "max_tokens": 200,
    }).encode()

    req = urllib.request.Request(
        f"{DEEPSEEK_BASE}/v1/chat/completions",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        },
    )
    ctx = ssl.create_default_context()
    for attempt in range(3):
        try:
            resp = urllib.request.urlopen(req, context=ctx, timeout=120)  # 120s timeout
            data = json.loads(resp.read())
            content = data["choices"][0]["message"]["content"].strip()
            # 移除可能的引号和前缀
            content = re.sub(r'^["\'「『]|["\'」』]$', '', content)
            content = re.sub(r'^概要[：:]\s*', '', content)
            return content
        except (json.JSONDecodeError, KeyError, urllib.error.URLError,
                TimeoutError, OSError) as e:
            logger.warning(f"  LLM 调用失败(尝试{attempt+1}): {e}")
            if attempt < 2:
                wait = 5 * (attempt + 1) + random.uniform(0, 2)  # 指数退避 + jitter
                logger.info(f"    等待 {wait}s 后重试...")
                time.sleep(wait)
    return ""


# ══════════════════════════════════════════════════════════════════
#  Qdrant 写入
# ══════════════════════════════════════════════════════════════════

def _make_outline_id(doc_id: str, section_order: int) -> int:
    """为 outline 生成稳定的 Qdrant ID。"""
    seed = f"outline_{doc_id}_{section_order}"
    return uuid.uuid5(uuid.NAMESPACE_DNS, seed).int & 0x7FFFFFFFFFFFFFFF


def write_doc_outlines(
    doc_outlines: list[dict],
    config,
    embedder,
    client,
    force: bool = False,
) -> int:
    """文档级 checkpoint：编码并写入单个文档的 outline。"""
    from qdrant_client import models
    col_name = config.QDRANT_COLLECTION

    # 编码 outline 文本
    texts_to_encode = [o["outline_text"] for o in doc_outlines]
    t0 = time.perf_counter()
    vectors = embedder.encode(texts_to_encode)
    logger.info(f"      🧠 编码 {len(texts_to_encode)} 个 outline ({time.perf_counter()-t0:.1f}s)")

    # 构建 Point 并写入
    points = []
    skipped = 0
    for i, o in enumerate(doc_outlines):
        point_id = _make_outline_id(o["doc_id"], o["section_order"])

        # 非 force 模式检查是否已存在
        if not force:
            try:
                existing = client.retrieve(
                    collection_name=col_name,
                    ids=[point_id],
                    with_payload=False,
                    with_vectors=False,
                )
                if existing:
                    logger.debug(f"      ⏭️  outline {point_id} 已存在，跳过")
                    skipped += 1
                    continue
            except Exception as e:
                logger.debug(f"      ⚠️  检查 outline {point_id} 出错: {e}")

        points.append(models.PointStruct(
            id=point_id,
            vector=vectors[i].tolist(),
            payload={
                "chunk_type": "outline",
                "has_outline": True,
                "is_parent": True,
                "text": o["full_text"],
                "outline_text": o["outline_text"],
                "section": o["section_title"],
                "section_order": o["section_order"],
                "company": o.get("company", "未知"),
                "year": o.get("year", "未知"),
                "doc_type": o.get("doc_type", "未知"),
                "source_file": o.get("source_file", ""),
                "doc_id": o["doc_id"],
                "source": o.get("source", ""),
                "char_count": len(o["full_text"]),
                "importance": 1.3,
                "has_table": False,
                "page": 1,
                "table_summary": "",
                "prefix_title": f"【概要】{o['outline_text'][:60]}",
                "financial_keywords": [],
                "statement_type": "",
            }
        ))

    if not points:
        return skipped

    for start in range(0, len(points), BATCH_WRITE_SIZE):
        batch = points[start:start + BATCH_WRITE_SIZE]
        try:
            client.upsert(collection_name=col_name, points=batch, wait=True)
        except Exception as e:
            logger.error(f"      ❌ 写入失败: {e}")

    return len(points)


# ══════════════════════════════════════════════════════════════════
#  主流程
# ══════════════════════════════════════════════════════════════════

def process_all(mode: str = "pro", force: bool = False, dry_run: bool = False):
    """全量生成结构纲要索引。"""
    Config, EmbeddingEngine, QdrantIndexer = load_config_and_imports()

    cfg = Config()
    # 双模式: 设置对应的集合名
    if mode == "safe":
        cfg.QDRANT_COLLECTION = "finrag_safe"
    else:
        cfg.QDRANT_COLLECTION = "finrag_pro"

    parsed_dir = cfg.PARSED_DIR

    logger.info(f"{'='*60}")
    logger.info(f"  finRAG 结构纲要索引 (Phase 2a)")
    logger.info(f"  模式: {mode} → 目标集合: {cfg.QDRANT_COLLECTION}")
    if dry_run:
        logger.info(f"  🔍 DRY-RUN 模式（不调用 LLM，不写入 Qdrant）")
    elif force:
        logger.info(f"  🔄 FORCE 模式（覆盖已有 outline）")
    logger.info(f"{'='*60}\n")

    # 1) 加载嵌入模型
    embedder = EmbeddingEngine(cfg)
    embedder._load()

    # 2) 收集 .md 文件
    md_files = sorted(parsed_dir.glob("*.md"))
    # 排除 AI 摘要文件
    md_files = [f for f in md_files if "_ai_summaries" not in f.name]
    logger.info(f"📄 共 {len(md_files)} 个待检查文件\n")

    all_outlines = []  # 当前文档的 outline 暂存（每文档写入后清空）
    total_outlines = 0
    llm_calls = 0
    total_cost = 0.0
    # Qdrant 连接（文档级写入复用）
    from qdrant_client import QdrantClient, models
    qdrant_client = QdrantClient(path=str(cfg.QDRANT_DB_PATH))

    for idx, md_path in enumerate(md_files, 1):
        fname = md_path.name
        text = md_path.read_text(encoding="utf-8")
        if not text.strip():
            logger.info(f"[{idx:3d}/{len(md_files)}] ⏭️  {fname} (空文件)")
            continue

        # 切分 section
        sections = split_into_sections(text)
        # 只保留有足够内容的 section
        content_sections = [s for s in sections if s["char_count"] >= MIN_SECTION_CHARS]

        logger.info(
            f"[{idx:3d}/{len(md_files)}] 📄 {fname} "
            f"→ {len(sections)} sections, "
            f"{len(content_sections)} 个达标 (>={MIN_SECTION_CHARS}字)"
        )

        if not content_sections:
            continue

        # 从文件名中提取元数据（复用 ingestion_v2 的 MetadataExtractor）
        from scripts.ingestion_v2 import MetadataExtractor
        meta = MetadataExtractor(cfg).extract(md_path, text[:2000])
        doc_id = re.sub(r'[^\w\-_]', '_', fname.rsplit(".", 1)[0])

        if dry_run:
            # Dry-run 仅展示 section 信息，不调 LLM
            for s in content_sections[:3]:
                logger.info(
                    f"      📋 [{s['order']}] {s['title'][:50]} "
                    f"({s['char_count']}字)"
                )
            if len(content_sections) > 3:
                logger.info(f"      ... 还有 {len(content_sections)-3} 个 section")
            continue

        # 为每个 section 生成 outline
        doc_outlines = []
        doc_llm_calls = 0
        for s in content_sections:
            logger.info(
                f"      🤖 [{s['order']}] {s['title'][:50]} "
                f"({s['char_count']}字) ..."
            )

            outline_text = call_llm_for_outline(s["title"], s["text"])
            llm_calls += 1
            doc_llm_calls += 1
            total_cost += (len(s["text"]) // 4) * 0.000002

            if outline_text:
                logger.info(f"         ✅ {outline_text[:80]}...")
                doc_outlines.append({
                    "doc_id": doc_id,
                    "section_title": s["title"],
                    "section_order": s["order"],
                    "outline_text": outline_text,
                    "full_text": s["text"],
                    "company": meta.get("company", "未知"),
                    "year": meta.get("year", "未知"),
                    "doc_type": meta.get("doc_type", "未知"),
                    "source_file": fname,
                    "source": meta.get("source", ""),
                })
            else:
                logger.warning(f"         ❌ 生成失败")

            # DeepSeek 速率限制
            time.sleep(OUTLINE_RATE_LIMIT)

        # ── 文档级 checkpoint：立即写入 Qdrant ──────────────
        if doc_outlines:
            try:
                batch_written = write_doc_outlines(
                    doc_outlines, cfg, embedder, qdrant_client,
                    force=force
                )
                total_outlines += batch_written
                logger.info(f"      💾 写入 {batch_written}/{len(doc_outlines)} 个 outline")
            except Exception as e:
                logger.error(f"      ❌ Qdrant 写入失败: {e}")
                # 不中断，继续下一个文档
        else:
            logger.info(f"      ⏭️  无 outline 需写入")

        # 进度
        logger.info(
            f"  📊 进度: {idx}/{len(md_files)} 文件, "
            f"{llm_calls} 次 LLM 调用, "
            f"累积 {total_outlines} 个 outline\n"
        )

    qdrant_client.close()

    # ── 完成 ──────────────────────────────────────────────────────
    if dry_run:
        logger.info(f"\n{'='*60}")
        logger.info(f"  DRY-RUN 完成")
        logger.info(f"  共 {total_outlines} 个 outline（未写入）")
        logger.info(f"  预估 LLM 调用: {llm_calls} 次")
        logger.info(f"  预估成本: ¥{total_cost:.4f}")
        logger.info(f"{'='*60}")
        return

    if not total_outlines:
        logger.warning("⚠️  没有写入任何 outline")
        return

    logger.info(f"\n{'='*60}")
    logger.info(f"  ✅ 完成！")
    logger.info(f"  写入 {total_outlines} 个 outline chunk")
    logger.info(f"  LLM 调用: {llm_calls} 次")
    logger.info(f"  预估成本: ¥{total_cost:.4f}")
    logger.info(f"  提示: 后续需重建 BM25 索引")
    logger.info(f"{'='*60}\n")


# ══════════════════════════════════════════════════════════════════
#  CLI
# ══════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="finRAG 结构纲要索引",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""示例:
  python scripts/generate_outlines.py            # 全量生成 (pro 模式)
  python scripts/generate_outlines.py --force    # 覆盖已有
  python scripts/generate_outlines.py --dry-run  # 预览
  python scripts/generate_outlines.py --mode safe  # 安全模式
        """,
    )
    parser.add_argument("--mode", default="pro", choices=["pro", "safe"],
                        help="目标模式: pro (finrag_pro) / safe (finrag_safe)")
    parser.add_argument("--force", action="store_true",
                        help="覆盖已存在的 outline")
    parser.add_argument("--dry-run", action="store_true",
                        help="预览 section 切分，不调 LLM，不写入")
    args = parser.parse_args()

    process_all(mode=args.mode, force=args.force, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
