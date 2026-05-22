#!/usr/bin/env python3
"""
快速增量索引 — 单次加载模型，顺序写入剩余文档
"""
import gc, sys, time, logging
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))
logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")
logger = logging.getLogger("fast_index")

from scripts.ingestion_v2 import Config, ParentChildSplitter, MetadataExtractor, EmbeddingEngine, QdrantIndexer

cfg = Config()
splitter = ParentChildSplitter(cfg)
meta_extractor = MetadataExtractor(cfg)

# 1. 查已有 doc_id
from qdrant_client import QdrantClient
client = QdrantClient(path=str(cfg.QDRANT_DB_PATH))
exist = set()
try:
    offset = None
    while True:
        r = client.scroll('finrag', limit=5000, with_payload=True, with_vectors=False, offset=offset)
        for p in r[0]:
            did = p.payload.get('doc_id', '')
            if did:
                exist.add(did)
        if r[0] and r[1]:
            offset = r[1]
        else:
            break
except Exception as e:
    logger.warning(f"读取已有索引: {e}")
client.close()
logger.info(f"已索引文档数: {len(exist)}")

md_files = sorted(cfg.PARSED_DIR.glob("*.md"))
pending = [p for p in md_files if p.stem not in exist]
logger.info(f"待处理: {len(pending)}/{len(md_files)}")

if not pending:
    logger.info("✅ 全部已索引")
    sys.exit(0)

for p in pending:
    logger.info(f"  ⏳ {p.name}")

# 2. 一次性加载模型和索引器
logger.info("\n🔌 加载嵌入模型（一次性）...")
embedder = EmbeddingEngine(cfg)
indexer = QdrantIndexer(cfg)
indexer.ensure_collection(force_recreate=False)
logger.info("✅ 就绪\n")

# 3. 顺序处理
for idx, md_path in enumerate(pending, 1):
    logger.info(f"[{idx}/{len(pending)}] 📄 {md_path.name}")
    md_text = md_path.read_text(encoding="utf-8")
    if not md_text.strip():
        logger.warning("   ⚠️ 空文件跳过")
        continue
    
    metadata = meta_extractor.extract(md_path, md_text)
    logger.info(f"   🏷️  {metadata['company']} | {metadata['year']} | {metadata['doc_type']}")
    
    t0 = time.perf_counter()
    parents = splitter.split(md_text)
    logger.info(f"   ✂️  {len(parents)}父块, {sum(len(p.children) for p in parents)}子块 ({time.perf_counter()-t0:.2f}s)")
    
    t1 = time.perf_counter()
    try:
        n = indexer.index_document(metadata, parents, embedder)
        logger.info(f"   ✅ 写入 {n}父块 ({time.perf_counter()-t1:.1f}s)")
    except Exception as e:
        logger.error(f"   ❌ 错误: {e}")
    
    # 轻量 GC（模型保留）
    gc.collect()

indexer.close()
del embedder
gc.collect()

# 4. BM25
logger.info("\n⏳ 重建 BM25...")
try:
    from scripts.retriever import HybridRetriever
    r = HybridRetriever(cfg)
    r.build_bm25_index()
    logger.info("   ✅ BM25 完成")
except Exception as e:
    logger.warning(f"   ⚠️ BM25: {e}")

logger.info("\n✅ 全部完成")
