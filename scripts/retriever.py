#!/usr/bin/env python3
"""
finRAG — 混合检索引擎 (Hybrid Search + Rerank)
================================================
架构：
  用户查询
    ├──→ 向量检索 (Qdrant)   → Top-K 候选
    ├──→ BM25 检索 (Rank-BM25) → Top-K 候选
    │
    ├── RRF 融合排序 → Top-N 候选
    ├── Cross-Encoder 重排序 → Top-M 最终结果
    └── 取父块 (完整上下文) → LLM 输入

用法:
    from scripts.retriever import HybridRetriever
    retriever = HybridRetriever(cfg)
    results = retriever.retrieve("五粮液2025年营业收入")
"""

import json
import logging
import os
import pickle
import threading
import time
from backend.services.cache import bm25_cache, make_key  # BM25 缓存
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np

from qdrant_client import QdrantClient, models

logger = logging.getLogger("retriever")


# ══════════════════════════════════════════════════════════════════════
#  数据模型
# ══════════════════════════════════════════════════════════════════════

@dataclass
class RetrievedChunk:
    """一次检索命中的结果"""
    score: float          # RRF / 最终得分
    rank_vector: int      # 向量检索排名
    rank_bm25: int        # BM25 排名
    rank_rerank: float    # Reranker 得分（-1 表示未重排）
    text: str
    payload: dict
    point_id: int
    
    # 元数据快捷访问
    @property
    def company(self) -> str:
        return self.payload.get("company", "?")
    @property
    def year(self) -> str:
        return self.payload.get("year", "?")
    @property
    def doc_type(self) -> str:
        return self.payload.get("doc_type", "?")
    @property
    def is_parent(self) -> bool:
        return self.payload.get("is_parent", False)
    @property
    def parent_id(self) -> str:
        return self.payload.get("parent_id", "")
    @property
    def has_table(self) -> bool:
        return self.payload.get("has_table", False)
    @property
    def char_count(self) -> int:
        return self.payload.get("char_count", 0)
    @property
    def doc_id(self) -> str:
        return self.payload.get("doc_id", "")
    @property
    def importance(self) -> float:
        return self.payload.get("importance", 1.0)


@dataclass
class RetrievalResult:
    """一条完整检索结果（包含父块上下文）"""
    child_chunk: RetrievedChunk
    parent_chunk: Optional["RetrievedChunk"] = None
    rerank_score: float = -1.0


# ══════════════════════════════════════════════════════════════════════
#  BM25 索引构建与持久化
# ══════════════════════════════════════════════════════════════════════

class BM25Index:
    """
    BM25 关键词索引。
    
    构建方式：
      1. 从 Qdrant 中读取所有 child chunks 的文本
      2. 用 Rank-BM25 构建索引
      3. pickle 持久化到磁盘
    
    检索时：
      加载 pickle → 对查询分词 → 计算 BM25 得分
    """

    def __init__(self, index_path: Path):
        self.index_path = index_path
        self._bm25 = None
        self._corpus = []       # 原始文本列表（按 index 映射）
        self._point_ids = []    # 对应的 Qdrant point ID
        self._payloads = []     # 对应的 payload

    def build_from_qdrant(self, client: QdrantClient, col_name: str):
        """从 Qdrant 读取所有 child chunks 构建 BM25 索引。"""
        from rank_bm25 import BM25Okapi

        logger.info("📖 从 Qdrant 构建 BM25 索引...")

        # 滚动获取所有子块
        all_points = []
        offset = None
        while True:
            result = client.scroll(
                collection_name=col_name,
                limit=100,
                with_payload=True,
                with_vectors=False,
                scroll_filter=models.Filter(
                    must=[
                        models.FieldCondition(
                            key="is_parent",
                            match=models.MatchValue(value=False),
                        )
                    ]
                ),
                offset=offset,
            )
            points = result[0] if isinstance(result, tuple) else result.points
            if not points:
                break
            all_points.extend(points)
            # 获取下一页
            offset = result[-1] if len(result) > 1 else None
            if offset is None:
                break

        logger.info(f"   读取到 {len(all_points)} 个子块")

        # 分词 + 构建 BM25
        self._corpus = []
        self._point_ids = []
        self._payloads = []
        tokenized_corpus = []

        for pt in all_points:
            # 优先用 search_text（带节标题/表摘要），没有则用原始 text
            text = (pt.payload.get("search_text") or pt.payload.get("text") or "")[:1000]
            if not text.strip():
                continue
            tokens = self._tokenize(text)
            tokenized_corpus.append(tokens)
            self._corpus.append(text)
            self._point_ids.append(pt.id)
            self._payloads.append(pt.payload)

        self._bm25 = BM25Okapi(tokenized_corpus)
        logger.info(f"   ✅ BM25 索引构建完成（{len(self._corpus)} 篇文档）")

    def save(self):
        """持久化 BM25 索引到磁盘。"""
        self.index_path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "corpus": self._corpus,
            "point_ids": self._point_ids,
            "payloads": self._payloads,
            "bm25_params": {"k1": 1.5, "b": 0.75},
        }
        with open(self.index_path, "wb") as f:
            pickle.dump(data, f)
        logger.info(f"   💾 BM25 索引已保存: {self.index_path}")

    def load(self):
        """从磁盘加载 BM25 索引。"""
        if not self.index_path.exists():
            raise FileNotFoundError(f"BM25 索引不存在: {self.index_path}")

        from rank_bm25 import BM25Okapi

        with open(self.index_path, "rb") as f:
            data = pickle.load(f)

        self._corpus = data["corpus"]
        self._point_ids = data["point_ids"]
        self._payloads = data["payloads"]

        # 从文本重新构建 BM25 对象（使用保存的参数）
        bm25_params = data.get("bm25_params", {"k1": 1.5, "b": 0.75})
        tokenized = [self._tokenize(t) for t in self._corpus]
        self._bm25 = BM25Okapi(tokenized, k1=bm25_params["k1"], b=bm25_params["b"])

        logger.info(f"   🔄 BM25 索引已加载（{len(self._corpus)} 篇文档）")

    def search(self, query: str, top_k: int = 30) -> list[dict]:
        """
        BM25 检索。
        返回: [{
            "point_id": int,
            "score": float,
            "text": str,
            "payload": dict,
            "rank": int,
        }, ...]
        """
        if self._bm25 is None:
            raise RuntimeError("BM25 索引未加载，请先调用 load()")

        query_tokens = self._tokenize(query)
        scores = self._bm25.get_scores(query_tokens)

        # 按得分排序
        top_indices = np.argsort(scores)[::-1][:top_k]

        results = []
        for rank, idx in enumerate(top_indices):
            if scores[idx] <= 0:
                continue
            results.append({
                "point_id": self._point_ids[idx],
                "score": float(scores[idx]),
                "text": self._corpus[idx],
                "payload": self._payloads[idx],
                "rank": rank + 1,
            })

        return results

    @staticmethod
    def _tokenize(text: str) -> list[str]:
        """中文分词：jieba 分词 + 强制提取数字。
        
        示例：
          "营业收入405.29亿元" → 
          ["营业收入", "405.29", "亿元"]
          "2025年营业收入同比变化" → 
          ["2025", "年", "营业收入", "同比", "变化"]
        """
        import re
        tokens = []
        
        # 1. 尝试 jieba 分词（带 fallback）
        try:
            import jieba
            # 避免 jieba 每次都加载词典（已加载过的跳过）
            if not hasattr(BM25Index._tokenize, '_jieba_initialized'):
                jieba.initialize()
                BM25Index._tokenize._jieba_initialized = True
            
            # jieba 分词
            words = jieba.lcut(text)
            for w in words:
                stripped = w.strip()
                if not stripped:
                    continue
                # 纯数字/英文 token 保留
                if re.match(r'^[\d,.]+$', stripped) or re.match(r'^[a-zA-Z]+$', stripped):
                    tokens.append(stripped.lower())
                # 中文词保留
                elif any('\u4e00' <= c <= '\u9fff' for c in stripped):
                    tokens.append(stripped)
                # 其他（符号、混合字符）
                elif len(stripped) > 1:
                    tokens.append(stripped)
        except ImportError:
            # jieba 不可用时的 fallback：字级切分
            for char in text:
                if '\u4e00' <= char <= '\u9fff':
                    tokens.append(char)
            for word in re.findall(r'[a-zA-Z]+|\d+', text):
                tokens.append(word.lower())
        
        # 2. 强制提取所有数字 token（即使 jieba 把它们拆分成了单字）
        #    "405.29" 必须是 ["405.29"] 而不是 ["4", "0", "5", ".", "2", "9"]
        for match in re.finditer(r'\d+\.?\d*', text):
            num = match.group()
            if num not in tokens:
                tokens.append(num)
        
        # 3. 提取常见的财务单位（亿元、万元等）作为独立 token
        for unit in ['亿元', '万元', '百万元', '元', '%', '百分点']:
            if unit in text and unit not in tokens:
                tokens.append(unit)
        
        # 4. 年份数字（如 2025）强制加入
        for match in re.finditer(r'\b(19\d\d|20\d\d)\b', text):
            year = match.group()
            if year not in tokens:
                tokens.append(year)
        
        return tokens


# ══════════════════════════════════════════════════════════════════════
#  RRF 融合算法
# ══════════════════════════════════════════════════════════════════════

def rrf_fusion(
    vector_results: list[dict],
    bm25_results: list[dict],
    k: int = 60,
    vector_weight: float = 1.0,
    bm25_weight: float = 1.0,
) -> list[RetrievedChunk]:
    """
    Reciprocal Rank Fusion (RRF) 融合两种检索结果。

    公式: score(d) = Σ (w / (k + rank_i(d)))
    其中 w 是每种检索方式的权重, k 是 RRF 常数（默认 60）
    """
    # 构建 point_id → RRF score 映射
    fusion_scores = {}

    for rank, item in enumerate(vector_results):
        pid = item["point_id"]
        score = vector_weight / (k + rank + 1)
        fusion_scores.setdefault(pid, {
            "score": 0, "rank_vector": rank + 1, "rank_bm25": 999,
            "text": item["text"], "payload": item["payload"], "point_id": pid,
        })
        fusion_scores[pid]["score"] += score

    for rank, item in enumerate(bm25_results):
        pid = item["point_id"]
        if pid in fusion_scores:
            fusion_scores[pid]["score"] += bm25_weight / (k + rank + 1)
            fusion_scores[pid]["rank_bm25"] = rank + 1
        else:
            fusion_scores[pid] = {
                "score": bm25_weight / (k + rank + 1),
                "rank_vector": 999,
                "rank_bm25": rank + 1,
                "text": item["text"],
                "payload": item["payload"],
                "point_id": pid,
            }

    # 按 RRF 得分排序
    sorted_items = sorted(fusion_scores.values(), key=lambda x: -x["score"])

    fused = [
        RetrievedChunk(
            score=item["score"],
            rank_vector=item["rank_vector"],
            rank_bm25=item["rank_bm25"],
            rank_rerank=-1,
            text=item["text"],
            payload=item["payload"],
            point_id=item["point_id"],
        )
        for item in sorted_items
    ]

    # 重要性加权：按 doc 的 importance 字段缩放 score
    importances = [c.importance for c in fused]
    if importances:
        max_imp = max(importances)
        min_imp = min(importances)
        logger.info(f"   ☆  importance scaling: max={max_imp:.2f}, min={min_imp:.2f}")
    for chunk in fused:
        chunk.score *= chunk.importance

    return fused


# ══════════════════════════════════════════════════════════════════════
#  Cross-Encoder Reranker
# ══════════════════════════════════════════════════════════════════════

class Reranker:
    """
    BGE-Reranker Cross-Encoder 重排序。
    
    对初步召回的候选块进行精细语义打分，纠正向量/BM25 的误判。
    懒加载：仅在首次 rerank 时加载模型。
    """

    def __init__(self, model_name: str = "BAAI/bge-reranker-base"):
        self.model_name = model_name
        self._model = None
        self._tokenizer = None
        self._skip = False

    def _load(self):
        """懒加载 Cross-Encoder 模型。"""
        if self._model is not None:
            return
        logger.info(f"🔌 加载 Reranker 模型: {self.model_name} ...")

        # 从 Modelscope 或本地缓存加载
        local_path = self._find_local_path()
        if not local_path:
            logger.warning("⚠️  Reranker 模型未在本地缓存中找到，跳过 reranker 加载")
            self._skip = True
            return
        logger.info(f"   本地路径: {local_path}")

        from transformers import AutoModelForSequenceClassification, AutoTokenizer
        self._tokenizer = AutoTokenizer.from_pretrained(
            local_path, local_files_only=True
        )
        self._model = AutoModelForSequenceClassification.from_pretrained(
            local_path,
            num_labels=1,
            local_files_only=True,
        )
        self._model.eval()
        logger.info("   ✅ Reranker 加载完成")

    @staticmethod
    def _find_local_path() -> str:
        """查找模型在本地的缓存路径（Modelscope > HF）。"""
        import importlib.util
        # 1) Modelscope 缓存（优先 v2-m3）
        for model_id in ["BAAI/bge-reranker-v2-m3", "BAAI/bge-reranker-base"]:
            ms_path = (
                Path.home()
                / ".cache" / "modelscope" / "hub" / "models"
                / model_id.replace("/", "/")
            )
            if ms_path.exists():
                return str(ms_path)

        # 2) HuggingFace 缓存
        for model_id in ["BAAI/bge-reranker-v2-m3", "BAAI/bge-reranker-base"]:
            hf_path = (
                Path.home()
                / ".cache" / "huggingface" / "hub"
                / f"models--{model_id.replace('/', '--')}" / "snapshots"
            )
            if hf_path.exists():
                snapshots = sorted(hf_path.iterdir())
                if snapshots:
                    return str(snapshots[-1])

        # 3) 兜底 — 无可用模型，返回空字符串（调用方据此跳过 reranker）
        return ""

    def rerank(self, query: str, candidates: list[RetrievedChunk],
               top_k: int = 5) -> list[RetrievedChunk]:
        """
        对候选结果进行重排序。
        
        参数:
            query: 用户查询
            candidates: RRF 融合后的候选列表
            top_k: 最终返回前多少个结果
        
        返回:
            重排序后的结果，rerank_score 被更新
        """
        import torch

        self._load()

        if not candidates:
            return []

        if self._skip:
            logger.info("   ⏭️  Reranker 未加载，跳过重排序")
            return candidates[:top_k]

        # 构建 query-document pairs
        pairs = [(query, c.text) for c in candidates]

        # Tokenize
        encoded = self._tokenizer(
            pairs,
            padding=True,
            truncation=True,
            max_length=512,
            return_tensors="pt",
        )

        # 推理
        with torch.no_grad():
            outputs = self._model(**encoded)
            scores = outputs.logits.squeeze(-1).tolist()

        # 如果只有一个结果，scores 可能是 float
        if isinstance(scores, float):
            scores = [scores]

        # 更新候选的 rerank_score
        for i, score in enumerate(scores):
            candidates[i].rank_rerank = score

        # 按 rerank 得分重新排序
        reranked = sorted(candidates, key=lambda x: -x.rank_rerank)

        return reranked[:top_k]


# ══════════════════════════════════════════════════════════════════════
#  HybridRetriever（主入口）
# ══════════════════════════════════════════════════════════════════════

# ══════════════════════════════════════════════════════════════════════
#  意图→文档类型映射 & 最低质量阈值
# ══════════════════════════════════════════════════════════════════════

# 不同意图允许搜索的文档类型（None = 所有类型）
INTENT_DOC_TYPES = {
    "factual": None,       # 事实查询：所有类型皆可
    "macro": ["宏观"],     # 宏观查询：只搜货币政策报告
    "comparative": None,   # 对比查询：所有类型
    "summary": None,       # 总结查询：所有类型
    "computational": None, # 计算查询：所有类型
    "analytical": None,    # 分析查询：所有类型
}

# 最终返回的最低得分阈值（低于此值不返回，宁缺毋滥）
MIN_RESULT_SCORE = 0.005

# ── 财务指标关键词（用于表格感知检索） ───────────────────────────
FINANCIAL_METRICS = [
    # 利润表
    "营业收入", "营业成本", "营业利润", "利润总额", "净利润",
    "归属于母公司所有者的净利润", "归母净利润", "少数股东损益",
    "综合收益总额", "营业总收入", "营业总成本",
    "销售费用", "管理费用", "研发费用", "财务费用",
    "投资收益", "资产减值损失", "信用减值损失",
    "毛利", "毛利率", "净利率",
    # 资产负债表
    "资产总计", "负债合计", "所有者权益", "流动资产", "非流动资产",
    "流动负债", "非流动负债", "货币资金", "应收账款", "存货",
    "固定资产", "无形资产", "短期借款", "长期借款", "应付账款",
    "股本", "资本公积", "盈余公积", "未分配利润",
    "资产负债率", "流动比率", "速动比率",
    # 现金流量表
    "经营活动产生的现金流量", "投资活动产生的现金流量",
    "筹资活动产生的现金流量", "现金及现金等价物净增加额",
    # 其他指标
    "ROE", "净资产收益率", "每股收益", "每股净资产",
    "基本每股收益", "稀释每股收益", "加权平均净资产收益率",
    "分红", "每股分红", "股息率",
    # 常见简写/别名
    "营收", "毛利", "净利", "利润", "收入", "成本",
    "资产", "负债", "现金流", "收益率", "回报率",
]

TABLE_TRIGGER_WORDS = [
    "多少", "分别是", "是多少", "数据", "数值", "数字",
    "表格", "表", "列示", "如下",
]

TABLE_BOOST_WEIGHT = 1.5  # 表格 chunk RRF 权重倍率


class HybridRetriever:
    """
    混合检索引擎。
    
    检索流程:
      1. 向量检索 (Qdrant) → Top-30 candidates
      2. BM25 检索          → Top-30 candidates
      3. RRF 融合           → Top-20 fused
      4. [可选] Reranker     → Top-5 精排
      5. 取父块              → 完整上下文
    """

    def __init__(self, config):
        self.cfg = config
        self._qdrant_client: Optional[QdrantClient] = None
        self._bm25: Optional[BM25Index] = None
        self._embedder = None
        self._reranker: Optional[Reranker] = None
        self._lock = threading.Lock()  # thread safety for _connect_qdrant

        # BM25 索引路径（根据集合名区分 pro/safe）
        col = getattr(self.cfg, "QDRANT_COLLECTION", "finrag")
        suffix = f"_{col}" if col != "finrag" else ""
        self._bm25_path = self.cfg._BASE_DIR / "data" / f"bm25_index{suffix}.pkl"

    # ── 初始化 ────────────────────────────────────────────────────

    def _connect_qdrant(self):
        if self._qdrant_client is None:
            with self._lock:
                if self._qdrant_client is not None:
                    return
                mode = getattr(self.cfg, "QDRANT_MODE", "disk")
                if mode == "remote":
                    host = self.cfg.QDRANT_HOST
                    port = getattr(self.cfg, "QDRANT_PORT", 6333)
                    logger.info(f"   🔌 连接远程 Qdrant: {host}:{port}")
                    self._qdrant_client = QdrantClient(
                        host=host,
                        port=port,
                        prefer_grpc=False,
                    )
                else:
                    # 清理 stale lock（进程异常退出后残留）
                    lock_file = Path(str(self.cfg.QDRANT_DB_PATH)) / ".lock"
                    if lock_file.exists():
                        try:
                            lock_file.unlink()
                            logger.info(f"  🗑️ 清除 Qdrant stale lock: {lock_file}")
                        except Exception:
                            pass
                    self._qdrant_client = QdrantClient(
                        path=str(self.cfg.QDRANT_DB_PATH)
                    )

    def _load_embedder(self):
        if self._embedder is None:
            from scripts.ingestion_v2 import EmbeddingEngine
            self._embedder = EmbeddingEngine(self.cfg)

    def _load_reranker(self):
        if self._reranker is None:
            self._reranker = Reranker()  # default: BAAI/bge-reranker-v2-m3

    def _load_bm25(self):
        if self._bm25 is None:
            self._bm25 = BM25Index(self._bm25_path)
            try:
                self._bm25.load()
            except FileNotFoundError:
                logger.warning(f"BM25 索引不存在: {self._bm25_path}，触发重建")
                self.build_bm25_index()

    # ── 构建 BM25 ──────────────────────────────────────────────────

    def build_bm25_index(self):
        """从 Qdrant 构建 BM25 索引并持久化。"""
        self._connect_qdrant()
        self._bm25 = BM25Index(self._bm25_path)
        self._bm25.build_from_qdrant(
            self._qdrant_client, self.cfg.QDRANT_COLLECTION
        )
        self._bm25.save()

    # ── 向量检索 ──────────────────────────────────────────────────

    def _vector_search(self, query: str, top_k: int = 30,
                       filters: dict = None,
                       score_threshold: float = 0.0) -> tuple[list[dict], bool]:
        """向量检索。

        Args:
            query: 用户查询
            top_k: 召回数
            filters: 元数据过滤
            score_threshold: 软过滤阈值，若最高分低于此值则标记 filter_too_strict=True

        Returns:
            (results, filter_too_strict)
            filter_too_strict 表示最高分小于阈值，可能过滤过严
        """
        self._connect_qdrant()
        self._load_embedder()

        prefixed = query  # BGE-M3 不加前缀效果更好（与索引侧一致）

        # Layer 1: Embedding 缓存
        from backend.services.cache import embedding_cache, make_key
        emb_key = f"emb:{make_key(prefixed)}"
        cached_vec = embedding_cache.get(emb_key)
        if cached_vec is not None:
            q_vec = np.array(cached_vec)
            logger.info(f"   ⚡ Embedding 缓存命中")
        else:
            q_vec = self._embedder.encode([prefixed])[0]
            embedding_cache.set(emb_key, q_vec.tolist())

        # 构建过滤（默认只搜子块）
        must_conditions = [
            models.FieldCondition(
                key="is_parent",
                match=models.MatchValue(value=False),
            )
        ]
        if filters:
            for k, v in filters.items():
                must_conditions.append(
                    models.FieldCondition(
                        key=k, match=models.MatchValue(value=v)
                    )
                )

        qdrant_filter = models.Filter(must=must_conditions)

        hits = self._qdrant_client.query_points(
            collection_name=self.cfg.QDRANT_COLLECTION,
            query=q_vec.tolist(),
            limit=top_k,
            query_filter=qdrant_filter,
            with_payload=True,
            with_vectors=False,
        )
        hits = hits.points if hasattr(hits, "points") else hits

        results = []
        for rank, hit in enumerate(hits):
            results.append({
                "point_id": hit.id,
                "score": hit.score,
                "text": hit.payload.get("text", ""),
                "payload": hit.payload,
                "rank": rank + 1,
            })

        # Score-based soft filter check
        max_score = max((r["score"] for r in results), default=0.0)
        filter_too_strict = max_score < score_threshold

        return results, filter_too_strict

    # ── 取父块 ────────────────────────────────────────────────────

    def _get_parent_chunk(self, parent_id: str) -> Optional[RetrievedChunk]:
        """根据 parent_id 取父块（使用点 ID 直接检索，O(1) 性能）。"""
        self._connect_qdrant()
        # parent_id 是 UUID 字符串，父块的 Qdrant point_id = uuid(parent_id).int & mask
        import uuid
        parent_point_id = uuid.UUID(parent_id).int & 0x7FFFFFFFFFFFFFFF
        try:
            parent_points = self._qdrant_client.retrieve(
                collection_name=self.cfg.QDRANT_COLLECTION,
                ids=[parent_point_id],
                with_payload=True,
                with_vectors=False,
            )
        except Exception:
            # fallback: 用 scroll（payload 索引退化）
            parent_points = []
        if not parent_points:
            # fallback: scroll by payload field
            parent_hits = self._qdrant_client.scroll(
                collection_name=self.cfg.QDRANT_COLLECTION,
                limit=1,
                with_payload=True,
                with_vectors=False,
                scroll_filter=models.Filter(
                    must=[
                        models.FieldCondition(
                            key="parent_id", match=models.MatchValue(value=parent_id),
                        ),
                        models.FieldCondition(
                            key="is_parent", match=models.MatchValue(value=True),
                        ),
                    ]
                ),
            )
            parent_records = parent_hits[0] if isinstance(parent_hits, tuple) else parent_hits.points
            parent_points = parent_records if parent_records else []

        if parent_points:
            pt = parent_points[0]
            return RetrievedChunk(
                score=1.0,
                rank_vector=0,
                rank_bm25=0,
                rank_rerank=-1,
                text=pt.payload.get("text", ""),
                payload=pt.payload,
                point_id=pt.id,
            )
        return None

    # ── 文档级去重 ────────────────────────────────────────────────

    @staticmethod
    def _dedup_by_parent(chunks: list[RetrievedChunk]) -> list[RetrievedChunk]:
        """章节级去重：每个 parent_id 最多保留 top-2 高分 chunk，减少同父块子块误杀。"""
        from collections import defaultdict
        groups = defaultdict(list)
        for c in chunks:
            pid = c.parent_id or c.doc_id
            groups[pid].append(c)
        deduped = []
        for pid, group in groups.items():
            sorted_group = sorted(group, key=lambda x: -x.score)
            deduped.extend(sorted_group[:2])  # 保留 top-2
        deduped.sort(key=lambda x: -x.score)
        return deduped[:40]

    @staticmethod
    def _dedup_by_doc(chunks: list[RetrievedChunk]) -> list[RetrievedChunk]:
        """文档级去重：同一文档 (doc_id) 只保留最高分 chunk。"""
        seen = {}
        for c in chunks:
            did = c.doc_id
            if did not in seen or c.score > seen[did].score:
                seen[did] = c
        result = sorted(seen.values(), key=lambda x: -x.score)
        return result[:40]

    # ── 财务查询检测（表格感知检索） ─────────────────────────────

    def _is_financial_metric_query(self, query: str) -> bool:
        """判断查询是否涉及财务指标（需要表格感知检索）。"""
        for metric in FINANCIAL_METRICS:
            if metric in query:
                return True
        for trigger in TABLE_TRIGGER_WORDS:
            if trigger in query:
                return True
        import re
        if re.search(r"(?:公司|企业|集团)?\s*(?:20[0-9]{2})?\s*年?\s*(?:的)?\s*(?:营收|利润|收入|成本|资产|负债|现金流|ROE|收益率|分红)\s*(?:是|为|有)?\s*(?:多少|怎样|如何)", query):
            return True
        return False

    def _get_table_query(self, query: str) -> str:
        """为表格检索生成优化查询（追加检测到的财务关键词）。"""
        found = [m for m in FINANCIAL_METRICS if m in query]
        extra = " ".join(found[:3]) if found else "财务指标"
        return f"{query} 表格 {extra} 数据"

    # ── 动态 Reranker 决策 ──────────────────────────────────────────

    def _decide_reranker(
        self, query: str, chunks: list[RetrievedChunk], intent: str
    ) -> bool:
        """根据查询特征 + 检索质量，动态判断是否启用 Cross-Encoder 重排。

        规则（任一触发即启用）:

        ① 查询模式触发（正则匹配 query 文本）:
           - 比较类: 对比/比较/哪个/更/差异/vs/versus/优于/差距
           - 复杂条件: 超过/大于/小于/至少/连续/最高/最低/最佳/领先/排名
           - 多实体: 出现 2+ 公司/实体名（通过 KB 检测）

        ② 检索质量触发（分析 RRF 后的候选 chunks）:
           - top-3 得分差距 < 0.03（分数接近，需要精排）
           - 最高分 < 0.35（整体质量偏低）
           - 候选数 >= 8 且得分标准差 < 0.05（大量接近分数）

        ③ 意图触发:
           - "analytical": 分析类需要更精确文档排序
           - "comparative": 对比类对排序最敏感

        返回: True=启用 Reranker, False=跳过
        """
        # 环境变量强制跳过 Reranker（评估时用）
        if os.environ.get("RERANKER_DISABLE", "") == "1":
            return False

        # ── ① 查询模式检查 ──────────────────────────────────────
        COMPARISON_PATTERNS = [
            r"对比", r"比较", r"哪个", r"\bvs\b", r"versus",
            r"更[好大小多高]", r"差异", r"优于", r"差距",
            r"哪家", r"谁更", r"孰[优强]",
        ]
        COMPLEX_PATTERNS = [
            r"超过", r"大于", r"小于", r"至少", r"连续",
            r"最高", r"最低", r"最佳", r"领先", r"排名",
            r"增长.*超过", r"下降.*小于", r"[0-9]+年以上",
        ]
        MULTI_ENTITY_PATTERN = r"[、和与及,，].*[企业公司股份集团]"

        import re
        query_lower = query.lower()

        # 比较 → 高优先级
        for pat in COMPARISON_PATTERNS:
            if re.search(pat, query):
                logger.info(f"   🔍 动态 Reranker: 比较模式触发 (pattern={pat})")
                return True

        # 复杂条件 → 中优先级
        for pat in COMPLEX_PATTERNS:
            if re.search(pat, query):
                logger.info(f"   🔍 动态 Reranker: 复杂条件触发 (pattern={pat})")
                return True

        # 多实体（简单检测：带顿号/和/与且出现 公司/股份/集团）
        if re.search(MULTI_ENTITY_PATTERN, query):
            logger.info(f"   🔍 动态 Reranker: 多实体触发")
            return True

        # ── ② 检索质量检查 ──────────────────────────────────────
        if len(chunks) >= 3:
            top_scores = sorted([c.score for c in chunks], reverse=True)[:3]
            gap = top_scores[0] - top_scores[-1]
            max_score = top_scores[0]

            # 得分差距很小（top-3 接近）
            if gap < 0.03:
                logger.info(
                    f"   🔍 动态 Reranker: top-3 得分接近 "
                    f"(max={max_score:.3f} gap={gap:.3f})"
                )
                return True

            # 整体质量偏低
            if max_score < 0.35:
                logger.info(
                    f"   🔍 动态 Reranker: 最高分偏低 (max={max_score:.3f})"
                )
                return True

            # 大量接近分数
            if len(chunks) >= 8:
                scores = [c.score for c in chunks[:8]]
                mean = sum(scores) / len(scores)
                variance = sum((s - mean) ** 2 for s in scores) / len(scores)
                std_dev = variance ** 0.5
                if std_dev < 0.05:
                    logger.info(
                        f"   🔍 动态 Reranker: 分数密集 (n={len(chunks)}, "
                        f"std={std_dev:.3f})"
                    )
                    return True

        # ── ③ 意图触发 ──────────────────────────────────────────
        if intent in ("analytical", "comparative"):
            logger.info(f"   🔍 动态 Reranker: 意图触发 (intent={intent})")
            return True

        logger.info(f"   ✅ 动态 Reranker: 跳过 (质量达标)")
        return False

    # ── 主检索入口 ────────────────────────────────────────────────

    def retrieve(
        self,
        query: str,
        top_k_vector: int = 10,
        top_k_bm25: int = 20,
        top_k_final: int = 5,
        use_reranker: bool = False,
        filters: dict = None,
        with_parent: bool = True,
        intent: str = "factual",
        strict_filters: dict = None,
        score_threshold: float = 0.3,
    ) -> list[RetrievalResult]:
        """
        完整混合检索 + 重排序 + 取父块。

        参数:
            query: 用户查询
            top_k_vector: 向量检索召回数
            top_k_bm25: BM25 召回数
            top_k_final: 最终返回结果数
            use_reranker: 是否启用 Cross-Encoder 重排序
            filters: 元数据过滤 {company: xxx, year: xxx}（旧参数，保留兼容）
            with_parent: 是否取父块
            intent: 检索策略意图 ("factual", "analytical", "summary")
            strict_filters: 严格过滤条件（用于多阶段检索的第一阶段）
            score_threshold: 得分软过滤阈值

        返回:
            [RetrievalResult, ...]
        """
        # 向后兼容：strict_filters 优先于 filters
        actual_filters = strict_filters if strict_filters is not None else filters
        
        # 意图→文档类型硬过滤（宏观查询只搜宏观文档）
        intent_doc_types = INTENT_DOC_TYPES.get(intent)
        if intent_doc_types is not None:
            # 优先使用 intent 限制 doc_type（不替换原有 company 过滤）
            if not actual_filters:
                actual_filters = {}
            actual_filters["doc_type"] = intent_doc_types[0]  # 单值列表取第一个
            logger.info(f"   🎯 意图过滤: doc_type={intent_doc_types}")
        t0 = time.perf_counter()
        logger.info(f"🔍 混合检索: \"{query}\"")
        if actual_filters:
            logger.info(f"   过滤: {actual_filters}")

        # 0) 金融查询扩展（同义词替换，无 LLM 开销）
        from scripts.agents.query_rewriter import expand_financial_query
        expanded = expand_financial_query(query)
        if len(expanded) > 1:
            logger.info(f"   🔤 查询扩展: {expanded}")
        expanded = expanded[:2]  # 最多 2 个扩展查询（原查询 + 1 个同义扩展）

        # 1) 向量检索（对每个扩展查询做搜索，结果合并）
        t1 = time.perf_counter()
        all_vec_results = []
        for eq in expanded:
            vr, _ = self._vector_search(eq, top_k_vector, actual_filters, score_threshold)
            all_vec_results.extend(vr)
        # 去重：按 point_id 去重，保留最高分
        seen_vec = {}
        for r in all_vec_results:
            pid = r["point_id"]
            if pid not in seen_vec or r["score"] > seen_vec[pid]["score"]:
                seen_vec[pid] = r
        vec_results = sorted(seen_vec.values(), key=lambda x: -x["score"])[:top_k_vector]
        logger.info(f"   📡 向量检索: {len(vec_results)} 条 ({time.perf_counter()-t1:.1f}s)")

        # ⭐ BM25 缓存：对原查询 + 扩展查询分别做 BM25，合并结果
        t2 = time.perf_counter()
        self._load_bm25()
        bm25_all = []
        seen_bm25 = set()
        for eq in expanded:
            bmk = f"bm25:{make_key(eq, top_k=top_k_bm25, filters=str(actual_filters))}"
            cached_bm25 = bm25_cache.get(bmk)
            if cached_bm25 is not None:
                bm25_all.extend(cached_bm25)
                for r in cached_bm25:
                    pid = r.get("point_id", "")
                    if pid:
                        seen_bm25.add(pid)
                continue
            bm25_raw = self._bm25.search(eq, top_k_bm25)
            bm25_cache.set(bmk, bm25_raw)
            for r in bm25_raw:
                pid = r.get("point_id", "")
                if pid not in seen_bm25:
                    seen_bm25.add(pid)
                    bm25_all.append(r)
        bm25_results_raw = bm25_all[:top_k_bm25]
        if actual_filters:
            bm25_results = []
            for r in bm25_results_raw:
                payload = r.get("payload", {})
                match = True
                for k, v in actual_filters.items():
                    if payload.get(k) != v:
                        match = False
                        break
                if match:
                    bm25_results.append(r)
            logger.info(f"   📖 BM25 过滤后: {len(bm25_results)} 条 (原 {len(bm25_results_raw)} 条)")
        else:
            bm25_results = bm25_results_raw
        logger.info(f"   📖 BM25 检索: {len(bm25_results)} 条 ({time.perf_counter()-t2:.1f}s)")

        # 2.5) 表格感知检索（仅当检测到财务指标查询时）
        is_table_query = self._is_financial_metric_query(query)
        if is_table_query:
            t_table = time.perf_counter()
            table_filters = dict(actual_filters) if actual_filters else {}
            table_filters["has_table"] = True
            table_query = self._get_table_query(query)
            table_vec, _ = self._vector_search(table_query, top_k_vector, table_filters, score_threshold)
            if table_vec:
                logger.info(f"   📊 表格感知检索: {len(table_vec)} 条 ({time.perf_counter()-t_table:.1f}s)")
                # 提升表格 chunk 的 score
                for r in table_vec:
                    r["score"] *= TABLE_BOOST_WEIGHT
                    r["_from_table_boost"] = True
                # 注入到向量结果中（去重后保留最高分）
                seen_table = {r["point_id"] for r in vec_results}
                for r in table_vec:
                    pid = r["point_id"]
                    if pid not in seen_table:
                        seen_table.add(pid)
                        vec_results.append(r)
                    else:
                        # 已有同一点，如果表格版分更高则替换
                        for existing in vec_results:
                            if existing["point_id"] == pid and r["score"] > existing["score"]:
                                existing["score"] = r["score"]
                                existing["_from_table_boost"] = True
                                break
                vec_results.sort(key=lambda x: -x["score"])
                vec_results = vec_results[:top_k_vector]
            # 同时提升 BM25 结果中的表格 chunk
            for r in bm25_results:
                payload = r.get("payload", {})
                if payload.get("has_table"):
                    r["score"] *= TABLE_BOOST_WEIGHT
                    r["_from_table_boost"] = True
            bm25_results.sort(key=lambda x: -x["score"])

        # 3) RRF 融合
        t3 = time.perf_counter()
        fused = rrf_fusion(vec_results, bm25_results, bm25_weight=0.7, vector_weight=1.5, k=20)
        logger.info(f"   🔗 RRF 融合: {len(fused)} 条 ({time.perf_counter()-t3:.1f}s)")

        # 3.5) 章节级去重（RRF 后、Reranker 前：每个 parent 保留 1 个最高分 child）
        t_dedup = time.perf_counter()
        deduped = self._dedup_by_parent(fused)
        dedup_count = len(fused) - len(deduped)
        if dedup_count > 0:
            logger.info(f"   🔄 章节去重: 移除 {dedup_count} 条 (保留 {len(deduped)} 条, 每section最高1条)")
        logger.info(f"   🔄 章节去重: {len(deduped)} 条 ({time.perf_counter()-t_dedup:.3f}s)")

    # 4) 重排序（动态决策 — bge-reranker-base 中文专精）
        t4 = time.perf_counter()
        if use_reranker:
            pass
        else:
            # 自动决定：先检查质量，再决定是否启用
            use_reranker = self._decide_reranker(query, deduped, intent)
        
        if use_reranker:
            self._load_reranker()
            reranked = self._reranker.rerank(query, deduped, top_k=top_k_final)
            logger.info(f"   ⚖️  Reranker 重排: {len(reranked)} 条 ({time.perf_counter()-t4:.1f}s)")
        else:
            reranked = deduped[:top_k_final]
            logger.info(f"   ⚖️  跳过 Reranker, 取前 {len(reranked)} 条 ({time.perf_counter()-t4:.1f}s)")

        # 5) 取父块，去重：同一父块只保留最高分 child
        final_results = []
        seen_parent = {}
        for chunk in reranked:
            parent = None
            if with_parent and chunk.parent_id:
                parent = self._get_parent_chunk(chunk.parent_id)
                # 同一父块只保留第一个（最高分）
                if chunk.parent_id in seen_parent:
                    continue
                seen_parent[chunk.parent_id] = True
            final_results.append(RetrievalResult(
                child_chunk=chunk,
                parent_chunk=parent,
                rerank_score=chunk.rank_rerank,
            ))

        logger.info(f"   ✅ 最终: {len(final_results)} 条结果 ({time.perf_counter()-t0:.1f}s)")
        
        # 质量阈值过滤：低于 MIN_RESULT_SCORE 的不返回（宁缺毋滥）
        threshold = MIN_RESULT_SCORE
        before = len(final_results)
        final_results = [r for r in final_results if r.child_chunk.score >= threshold]
        filtered_out = before - len(final_results)
        if filtered_out:
            logger.info(f"   🚫 质量阈值过滤: 移除 {filtered_out} 条低分结果 (score<{threshold})")
        
        return final_results

    # ── 多阶段检索 ────────────────────────────────────────────────

    def retrieve_multi_stage(
        self,
        query: str,
        strict_filters: dict = None,
        intent: str = "factual",
        top_k_vector: int = 30,
        top_k_bm25: int = 30,
        top_k_final: int = 5,
        use_reranker: bool = False,
        with_parent: bool = True,
        score_threshold: float = 0.3,
    ) -> list[RetrievalResult]:
        """
        多阶段混合检索（严格→宽松 两阶段回退策略）。

        策略：
          - Stage 1 (strict): 应用 strict_filters, top_k=15, score_threshold=0.3
            · 若最高分 >= threshold 或 结果数 >= 3 → 保留 Stage 1 结果（score * 1.2 加权）
            · 否则 → Stage 2
          - Stage 2 (global): 无公司过滤, top_k=20
            · 与 Stage 1 结果合并（并集，doc_id 去重）

        参数:
            query: 用户查询
            strict_filters: 严格过滤条件，如 {"company": "天齐锂业"}
            intent: 检索策略意图
            top_k_vector: 向量检索召回数（默认）
            top_k_bm25: BM25 召回数
            top_k_final: 最终返回结果数
            use_reranker: 是否启用 Cross-Encoder 重排序
            with_parent: 是否取父块
            score_threshold: 得分软过滤阈值

        返回:
            [RetrievalResult, ...]
        """
        t0 = time.perf_counter()
        logger.info(f"🔍 多阶段检索: \"{query}\"")
        logger.info(f"   意图: {intent}")
        if strict_filters:
            logger.info(f"   严格过滤: {strict_filters}")

        # ── Stage 1: 严格过滤 ──────────────────────────────────────
        t1 = time.perf_counter()
        vec_strict, filter_too_strict = self._vector_search(
            query, top_k=15, filters=strict_filters,
            score_threshold=score_threshold,
        )
        have_enough_strict = len(vec_strict) >= 3
        logger.info(
            f"   🅰️  Stage 1 (严格): {len(vec_strict)} 条, "
            f"filter_too_strict={filter_too_strict}, "
            f"have_enough={have_enough_strict} "
            f"({time.perf_counter()-t1:.1f}s)"
        )

        need_stage2 = filter_too_strict and not have_enough_strict

        # ── Stage 2: 全局检索（回退，但保留意图文档类型限制） ─────
        if need_stage2:
            t2 = time.perf_counter()
            # Stage 2 回退时保留 intent 的 doc_type 限制（但不限制 company）
            stage2_filters = None
            intent_doc_types = INTENT_DOC_TYPES.get(intent)
            if intent_doc_types is not None:
                stage2_filters = {"doc_type": intent_doc_types[0]}
                logger.info(f"   🎯 Stage 2 意图限制: doc_type={intent_doc_types}")
            
            # Stage 2 向量搜索（使用 intent 限制或无限制）
            vec_global, _ = self._vector_search(
                query, top_k=20, filters=stage2_filters, score_threshold=0.0,
            )

            # 合并：Stage 1 结果 score * 1.2 加权，然后 union + dedup
            for r in vec_strict:
                r["score"] *= 1.2

            # 按 point_id 去重（Stage 1 优先）
            seen_pids = set()
            merged_vec = []
            for r in vec_strict + vec_global:
                pid = r["point_id"]
                if pid not in seen_pids:
                    seen_pids.add(pid)
                    merged_vec.append(r)
            vec_results = merged_vec
            logger.info(
                f"   🅱️  Stage 2 (全局): {len(vec_global)} 条, "
                f"合并后: {len(vec_results)} 条 "
                f"({time.perf_counter()-t2:.1f}s)"
            )
        else:
            vec_results = vec_strict
            logger.info(
                f"   ✅ Stage 1 结果足够, 跳过 Stage 2"
            )

        # ── BM25 检索（使用最终 filters） ──────────────────────────
        t_bm25 = time.perf_counter()
        self._load_bm25()
        # ⭐ BM25 缓存（第二个检索路径）
        bmk = f"bm25:{make_key(query, top_k=top_k_bm25, filters=str(strict_filters))}"
        cached_bm25 = bm25_cache.get(bmk)
        if cached_bm25 is not None:
            bm25_results_raw = cached_bm25
            logger.info(f"   ⚡ BM25 缓存命中")
        else:
            bm25_results_raw = self._bm25.search(query, top_k_bm25)
            bm25_cache.set(bmk, bm25_results_raw)
        # 用 strict_filters 过滤（如果有 Stage 1 匹配的话，就用原过滤）
        final_filters = strict_filters if not need_stage2 else None
        if final_filters:
            bm25_results = []
            for r in bm25_results_raw:
                payload = r.get("payload", {})
                match = True
                for k, v in final_filters.items():
                    if payload.get(k) != v:
                        match = False
                        break
                if match:
                    bm25_results.append(r)
            logger.info(
                f"   📖 BM25 过滤后: {len(bm25_results)} 条 "
                f"(原 {len(bm25_results_raw)} 条)"
            )
        else:
            bm25_results = bm25_results_raw
        logger.info(f"   📖 BM25 检索: {len(bm25_results)} 条 ({time.perf_counter()-t_bm25:.1f}s)")

        # ── RRF 融合 ──────────────────────────────────────────────
        t3 = time.perf_counter()
        fused = rrf_fusion(vec_results, bm25_results)
        logger.info(f"   🔗 RRF 融合: {len(fused)} 条 ({time.perf_counter()-t3:.1f}s)")

        # ── 文档级去重 ────────────────────────────────────────────
        t_dedup = time.perf_counter()
        deduped = self._dedup_by_doc(fused)
        dedup_count = len(fused) - len(deduped)
        if dedup_count > 0:
            logger.info(f"   🔄 文档去重: 移除 {dedup_count} 条重复文档")
        logger.info(f"   🔄 文档去重: {len(deduped)} 条 ({time.perf_counter()-t_dedup:.3f}s)")

        # ── 重排序（动态决策） ─────────────────────────────────────
        t4 = time.perf_counter()
        if use_reranker:
            pass  # 显式指定
        else:
            use_reranker = self._decide_reranker(query, deduped, intent)

        if use_reranker:
            self._load_reranker()
            reranked = self._reranker.rerank(query, deduped, top_k=top_k_final)
            logger.info(f"   ⚖️  Reranker 重排: {len(reranked)} 条 ({time.perf_counter()-t4:.1f}s)")
        else:
            reranked = deduped[:top_k_final]
            logger.info(f"   ⚖️  跳过 Reranker, 取前 {len(reranked)} 条 ({time.perf_counter()-t4:.1f}s)")

        # ── 取父块 ────────────────────────────────────────────────
        final_results = []
        seen_parent = {}
        for chunk in reranked:
            parent = None
            if with_parent and chunk.parent_id:
                parent = self._get_parent_chunk(chunk.parent_id)
                if chunk.parent_id in seen_parent:
                    continue
                seen_parent[chunk.parent_id] = True
            final_results.append(RetrievalResult(
                child_chunk=chunk,
                parent_chunk=parent,
                rerank_score=chunk.rank_rerank,
            ))

        logger.info(
            f"   ✅ 多阶段检索完成: {len(final_results)} 条结果 "
            f"({time.perf_counter()-t0:.1f}s)"
        )
        
        # 质量阈值过滤：低于 MIN_RESULT_SCORE 的不返回
        threshold = MIN_RESULT_SCORE
        before = len(final_results)
        final_results = [r for r in final_results if r.child_chunk.score >= threshold]
        filtered_out = before - len(final_results)
        if filtered_out:
            logger.info(f"   🚫 质量阈值过滤: 移除 {filtered_out} 条低分结果 (score<{threshold})")
        
        return final_results


# ══════════════════════════════════════════════════════════════════════
#  便捷函数：搜索 + 打印
# ══════════════════════════════════════════════════════════════════════

def format_results(results: list[RetrievalResult]) -> str:
    """格式化检索结果为可读字符串。"""
    lines = []
    for i, r in enumerate(results, 1):
        c = r.child_chunk
        tag = "📊" if c.has_table else "  "
        rerank_info = f" | rerank={r.rerank_score:.4f}" if r.rerank_score > 0 else ""
        lines.append(f"")
        lines.append(f"  [{i}] Score={c.score:.4f}{rerank_info} {tag}")
        lines.append(f"       {c.company} | {c.year} | {c.doc_type}")
        lines.append(f"       vec={c.rank_vector} bm25={c.rank_bm25}")
        lines.append(f"       {c.text[:200]}")

        if r.parent_chunk:
            p = r.parent_chunk
            lines.append(f"")
            lines.append(f"  ┌─ 父块 ({p.char_count}字) ──────────────────────")
            lines.append(f"  │ {p.text[:300]}")
            lines.append(f"  └───────────────────────────────────────────────")
    return "\n".join(lines)


def quick_search(query: str, config=None, **kwargs) -> list[RetrievalResult]:
    """一键搜索（用于 python -c 调试）。"""
    from scripts.ingestion_v2 import Config
    cfg = config or Config()
    retriever = HybridRetriever(cfg)
    retriever.build_bm25_index()  # 首次需要构建
    results = retriever.retrieve(query, **kwargs)
    print(format_results(results))
    return results


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")

    if len(sys.argv) > 1:
        query = " ".join(sys.argv[1:])
        quick_search(query, use_reranker=False)
    else:
        # 默认测试
        quick_search("五粮液2025年营业收入", use_reranker=False)
