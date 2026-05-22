"""finRAG — 知识库迁移脚本
将现有单 collection 拆分为 pro 和 safe 两个 collection。
"""
import json
import logging
import shutil
import sys
from pathlib import Path

_BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_BASE))
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(message)s")
logger = logging.getLogger("migrate")


def migrate_qdrant():
    from qdrant_client import QdrantClient
    from qdrant_client.http import models as qmodels
    from scripts.ingestion_v2 import Config as IngestionConfig
    from backend.config import settings

    qdrant_path = settings.QDRANT_PATH
    cfg = IngestionConfig()
    cfg.QDRANT_DB_PATH = qdrant_path

    client = QdrantClient(path=str(qdrant_path))
    existing = [c.name for c in client.get_collections().collections]
    logger.info(f"现有 collections: {existing}")

    for target in (settings.QDRANT_COLLECTION_PRO, settings.QDRANT_COLLECTION_SAFE):
        if target in existing:
            logger.info(f"  {target} 已存在，跳过创建")
            continue

        # 从旧集合获取 vector config
        old_col = "finrag" if "finrag" in existing else existing[0]
        old_info = client.get_collection(old_col)
        vector_config = old_info.config.params.vectors

        # 创建新集合
        client.create_collection(
            collection_name=target,
            vectors_config=qmodels.VectorParams(
                size=vector_config.size,
                distance=vector_config.distance,
            ),
        )
        logger.info(f"  ✅ 创建 collection: {target}")

        # 复制数据
        has_more = True
        offset = None
        copied = 0
        while has_more:
            pts, next_offset = client.scroll(
                collection_name=old_col,
                limit=100,
                offset=offset,
                with_payload=True,
                with_vectors=True,
            )
            if pts:
                client.upsert(
                    collection_name=target,
                    points=qmodels.Batch(
                        ids=[p.id for p in pts],
                        vectors=[p.vector for p in pts],
                        payloads=[p.payload for p in pts],
                    ),
                )
                copied += len(pts)
            has_more = bool(next_offset)
            offset = next_offset
            logger.info(f"  → {target}: 已复制 {copied} 个向量")

        logger.info(f"  ✅ {target}: 复制完成，共 {copied} 个向量")

    client.close()
    logger.info("🎉 Qdrant 迁移完成")


def migrate_bm25():
    bm25_path = _BASE / "data" / "bm25_index.pkl"
    bm25_pro = _BASE / "data" / "bm25_index_pro.pkl"
    bm25_safe = _BASE / "data" / "bm25_index_safe.pkl"

    if bm25_path.exists():
        if not bm25_pro.exists():
            shutil.copy2(bm25_path, bm25_pro)
            logger.info(f"  ✅ 复制 BM25 → bm25_index_pro.pkl")
        if not bm25_safe.exists():
            shutil.copy2(bm25_path, bm25_safe)
            logger.info(f"  ✅ 复制 BM25 → bm25_index_safe.pkl")
    else:
        logger.warning("  ⚠️  bm25_index.pkl 不存在，跳过")


def migrate_sources_index():
    idx_path = _BASE / "data" / "sources_index.json"
    idx_pro = _BASE / "data" / "sources_index_pro.json"
    idx_safe = _BASE / "data" / "sources_index_safe.json"

    if idx_path.exists():
        with open(idx_path, encoding="utf-8") as f:
            data = json.load(f)
        docs = data.get("documents", [])
        for d in docs:
            d["mode"] = "pro"

        if not idx_pro.exists():
            with open(idx_pro, "w", encoding="utf-8") as f:
                json.dump({"total": len(docs), "documents": docs}, f, ensure_ascii=False, indent=2)
            logger.info(f"  ✅ 创建 sources_index_pro.json ({len(docs)} 篇)")

        # safe 副本
        safe_docs = json.loads(json.dumps(docs))
        for d in safe_docs:
            d["mode"] = "safe"
        if not idx_safe.exists():
            with open(idx_safe, "w", encoding="utf-8") as f:
                json.dump({"total": len(safe_docs), "documents": safe_docs}, f, ensure_ascii=False, indent=2)
            logger.info(f"  ✅ 创建 sources_index_safe.json ({len(safe_docs)} 篇)")
    else:
        logger.warning("  ⚠️  sources_index.json 不存在，跳过")


if __name__ == "__main__":
    logger.info("=" * 50)
    logger.info("📦 finRAG 知识库迁移")
    logger.info("=" * 50)

    migrate_qdrant()
    migrate_bm25()
    migrate_sources_index()

    logger.info("=" * 50)
    logger.info("✅ 全部迁移完成！")
    logger.info("=" * 50)
