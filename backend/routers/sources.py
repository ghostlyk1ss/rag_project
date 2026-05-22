"""
finRAG API — Sources 路由（基于 Qdrant 预构建缓存）
======================================================
GET /api/v1/sources — 列出所有文档
GET /api/v1/sources/{doc_id} — 文档详情+chunks

使用 data/sources_index.json 作为元数据缓存（从 Qdrant 构建）。
避免运行时 Qdrant 锁冲突。
"""
import json
import logging
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException

from backend.config import settings
from backend.models.schemas import SourceChunk, SourceDetail, SourceDocument

logger = logging.getLogger("api.sources")
router = APIRouter(prefix="/api/v1", tags=["Sources"])

# ── 全局缓存 ─────────────────────────────────────────────
_cache: Optional[list[dict]] = None
_INDEX_PATH: Path = settings.BASE_DIR / "data" / "sources_index.json"
_INDEX_PATH_PRO: Path = settings.BASE_DIR / "data" / "sources_index_pro.json"
_INDEX_PATH_SAFE: Path = settings.BASE_DIR / "data" / "sources_index_safe.json"


def _get_index_path(mode: str | None = None) -> Path:
    """根据模式返回对应的 sources_index 文件路径。"""
    if mode == "safe":
        return _INDEX_PATH_SAFE
    elif mode == "pro":
        return _INDEX_PATH_PRO
    return _INDEX_PATH


def refresh_sources_cache() -> dict:
    """从 Qdrant 重建 sources_index.json 并刷新内存缓存。"""
    global _cache
    _cache = None
    from scripts.ingestion_v2 import Config as IngestionConfig
    from backend.services.rag_engine import _get_retriever

    cfg = IngestionConfig()
    cfg.QDRANT_COLLECTION = settings.QDRANT_COLLECTION_PRO

    # 复用 retriever 的持久 Qdrant 连接，避免 disk 锁冲突
    retriever = _get_retriever("pro")
    client = retriever._qdrant_client
    try:
        pts, _ = client.scroll(cfg.QDRANT_COLLECTION, limit=10000, with_payload=True, with_vectors=False)
        sources = {}
        for p in pts:
            pl = p.payload
            sf = pl.get("source_file", "")
            if not sf:
                continue
            if sf not in sources:
                sources[sf] = {
                    "doc_id": sf.replace(".md", ""),
                    "file_name": sf,
                    "title": pl.get("title", pl.get("source_title", sf.replace(".md", ""))),
                    "company": pl.get("company", ""),
                    "year": pl.get("year", ""),
                    "doc_type": pl.get("doc_type", ""),
                    "char_count": pl.get("char_count", 0),
                    "has_table": pl.get("has_table", False),
                    "chunks": 0,
                }
            sources[sf]["chunks"] += 1

        _INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(_INDEX_PATH, "w", encoding="utf-8") as f:
            json.dump({"total": len(sources), "documents": list(sources.values())}, f, ensure_ascii=False, indent=2)
        logger.info(f"✅ sources_index.json 已刷新: {len(sources)} 篇文档")

        # 重新标注 mode/outline 标签
        try:
            from scripts.annotate_sources import annotate_sources_index
            annotate_sources_index()
            logger.info(f"✅ sources_index 标签已标注")
        except Exception as e:
            logger.warning(f"   ⚠️ 标签标注失败: {e}")
    except Exception as e:
        logger.error(f"刷新 sources 失败: {e}")
        raise

    _cache = _load_index()
    return {"status": "ok", "total": len(_cache), "documents": _cache}


def _load_index(mode=None) -> list[dict]:
    global _cache
    if _cache is not None and mode is None:
        return _cache

    idx_path = _get_index_path(mode)

    if not idx_path.exists():
        if mode is None:
            logger.warning(f"{idx_path.name} 不存在，尝试从 parsed 目录构建")
            return _build_fallback_list()
        return _load_index(mode=None)

    try:
        with open(idx_path, encoding="utf-8") as f:
            data = json.load(f)
        docs = data.get("documents", [])
        # 补充 has_pdf 字段
        raw_dir: Path = settings.RAW_DIR
        for d in docs:
            doc_id = d.get("doc_id", "")
            d["has_pdf"] = (raw_dir / f"{doc_id}.pdf").exists()
            d["page_count"] = -1 if d.get("has_pdf") else 0
        _cache = docs
        logger.info(f"📚 加载 {len(docs)} 篇文档元数据 (sources_index.json)")
        return docs
    except Exception as e:
        logger.error(f"加载 sources_index.json 失败: {e}")
        return _build_fallback_list()


def _build_fallback_list() -> list[dict]:
    """兜底：从 parsed 目录扫描（无元数据）。"""
    parsed_dir: Path = settings.PARSED_DIR
    raw_dir: Path = settings.RAW_DIR
    docs = []
    for md_file in sorted(parsed_dir.glob("*.md")):
        doc_id = md_file.stem
        pdf_path = raw_dir / f"{doc_id}.pdf"
        docs.append({
            "doc_id": doc_id,
            "file_name": md_file.name,
            "title": doc_id,
            "company": "",
            "year": "",
            "doc_type": "",
            "char_count": md_file.stat().st_size,
            "page_count": -1 if pdf_path.exists() else 0,
            "has_table": False,
            "has_pdf": pdf_path.exists(),
            "error": "元数据不可用（未找到 sources_index.json）",
        })
    _cache = docs
    return docs


def _read_md_content(doc_id: str) -> Optional[str]:
    """读取 parsed 目录下的 md 文件内容。"""
    md_path = settings.PARSED_DIR / f"{doc_id}.md"
    if not md_path.exists():
        return None
    try:
        return md_path.read_text(encoding="utf-8")
    except Exception as e:
        logger.warning(f"读取 {md_path} 失败: {e}")
        return None


# ── 路由 ───────────────────────────────────────────────────


@router.get("/sources")
async def list_sources(mode: Optional[str] = None):
    """列出所有已索引的文档及其元数据。可指定 mode=pro|safe 过滤。"""
    docs = _load_index(mode=mode)
    if mode and docs:
        docs = [d for d in docs if d.get("mode", mode) == mode]
    return {"total": len(docs), "documents": docs}


@router.post("/sources/refresh")
async def refresh_sources():
    """刷新缓存（从 Qdrant 重建 sources_index.json）。"""
    global _cache
    _cache = None
    return refresh_sources_cache()


@router.get("/sources/{doc_id}")
async def get_source_detail(doc_id: str):
    """获取文档详情及所有 chunks（从 .md 文件中解析）。"""
    # 验证 doc_id 合法性
    if ".." in doc_id or "/" in doc_id or "\\" in doc_id:
        raise HTTPException(status_code=400, detail="无效的 doc_id")

    # 从文档列表中获取元数据
    docs = _load_index()
    doc_meta = None
    for d in docs:
        if d["doc_id"] == doc_id:
            doc_meta = d
            break

    if not doc_meta:
        md_path = settings.PARSED_DIR / f"{doc_id}.md"
        if not md_path.exists():
            raise HTTPException(status_code=404, detail=f"文档 {doc_id} 未找到")
        doc_meta = {
            "doc_id": doc_id,
            "file_name": f"{doc_id}.md",
            "title": doc_id,
            "company": "",
            "year": "",
            "doc_type": "",
            "char_count": md_path.stat().st_size,
            "page_count": 0,
            "has_table": False,
            "has_pdf": (settings.RAW_DIR / f"{doc_id}.pdf").exists(),
            "error": None,
        }

    doc = SourceDocument(
        doc_id=doc_meta["doc_id"],
        file_name=doc_meta["file_name"],
        title=doc_meta.get("title", doc_meta["doc_id"]),
        company=doc_meta.get("company", ""),
        year=doc_meta.get("year", ""),
        doc_type=doc_meta.get("doc_type", ""),
        char_count=doc_meta.get("char_count", 0),
        page_count=doc_meta.get("page_count", 0),
        has_table=doc_meta.get("has_table", False),
        parsed_at=None,
        error=doc_meta.get("error"),
    )

    # 读取 md 文件作为 chunks
    content = _read_md_content(doc_id)
    if content is None:
        return SourceDetail(document=doc, chunks=[])

    paragraphs = [p.strip() for p in content.split("\n\n") if p.strip()]
    chunks = []
    for idx, para in enumerate(paragraphs):
        chunks.append(SourceChunk(
            chunk_id=f"{doc_id}_{idx}",
            text=para,
            score=0.0,
            is_parent=True,
            payload={
                "source_file": f"{doc_id}.md",
                "chunk_index": idx,
                "doc_id": doc_id,
            },
        ))

    return SourceDetail(document=doc, chunks=chunks)


@router.delete("/sources/{doc_id}")
async def delete_source(doc_id: str, mode: Optional[str] = None):
    """删除文档（从 Qdrant, parsed, raw 中删除）。mode=pro|safe|all（默认 all）。"""
    if ".." in doc_id or "/" in doc_id or "\\" in doc_id:
        raise HTTPException(status_code=400, detail="无效的 doc_id")

    from scripts.ingestion_v2 import Config as IngestionConfig
    from backend.services.rag_engine import delete_from_qdrant

    cfg = IngestionConfig()
    cfg.QDRANT_DB_PATH = settings.QDRANT_PATH

    # 确定要删除的集合
    collections_to_delete = []
    if mode == "pro":
        collections_to_delete = [settings.QDRANT_COLLECTION_PRO]
    elif mode == "safe":
        collections_to_delete = [settings.QDRANT_COLLECTION_SAFE]
    else:
        collections_to_delete = [settings.QDRANT_COLLECTION_PRO, settings.QDRANT_COLLECTION_SAFE]

    # ── Qdrant 删除（复用已有持久连接，避免 disk 锁冲突）──
    deleted_results = delete_from_qdrant(doc_id, collections_to_delete)

    # ── 删除物理文件（扫描 raw + parsed 目录） ──────
    deleted_files = []
    raw_extensions = [".pdf", ".docx", ".pptx", ".xlsx", ".md", ".txt"]
    for ext in raw_extensions:
        p = settings.RAW_DIR / f"{doc_id}{ext}"
        if p.exists():
            p.unlink()
            deleted_files.append(str(p))
            logger.info(f"  🗑️ 删除 raw 文件: {p.name}")

    md_path = settings.PARSED_DIR / f"{doc_id}.md"
    if md_path.exists():
        md_path.unlink()
        deleted_files.append(str(md_path))
        logger.info(f"  🗑️ 删除 parsed 文件: {md_path.name}")

    # ── 后台异步清理（跳过大开销的 BM25 全量重建）──
    # BM25 索引不立刻重建：下一次查询时发现文件不存在会自动重建
    # 或通过 /api/v1/sources/refresh 手动刷新
    import threading
    def _cleanup_after_delete():
        try:
            refresh_sources_cache()
            logger.info("  ✅ sources_index 已刷新")
        except Exception as e:
            logger.warning(f"  ⚠️ sources 缓存刷新失败: {e}")

        try:
            from backend.services.cache import query_cache
            query_cache.invalidate("q:")
            logger.info("  🧹 查询缓存已清空（文档删除后）")
        except Exception:
            pass

    t = threading.Thread(target=_cleanup_after_delete, daemon=True)
    t.start()
    logger.info(f"  ⏳ 后台清理已启动（sources + 缓存）")

    logger.info(f"🗑️ 删除文档: {doc_id} (Qdrant: {list(deleted_results.keys())}, 文件: {deleted_files})")
    return {
        "status": "ok",
        "doc_id": doc_id,
        "qdrant_deleted": deleted_results,
        "files_deleted": deleted_files,
    }
