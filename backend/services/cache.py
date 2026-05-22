"""
finRAG 缓存层 — 内存 LRU 多级缓存
===================================

架构：
  Layer 1: Embedding 缓存     → _encode_query()           ← 减少 BGE 重复编码
  Layer 2: LLM 调用缓存       → query_rewriter / router   ← 相同 query 跳过 LLM
  Layer 3: BM25 检索缓存      → _bm25.search()            ← 相同 query 跳过
  Layer 4: 精确查询缓存       → rag_query() 入口          ← 整个 pipeline 结果

每层独立 TTL + LRU 淘汰，线程安全。
"""

import hashlib
import json
import threading
import time
from collections import OrderedDict
from typing import Any, Optional


# ══════════════════════════════════════════════════════════════════════
#  核心 LRU 缓存实现
# ══════════════════════════════════════════════════════════════════════

class LRUCache:
    """线程安全的 LRU 缓存，支持 TTL 过期和命中率统计。

    用法:
        cache = LRUCache(maxsize=256, ttl=300)
        cache.set("key", value)
        val = cache.get("key")  # None if expired or missing
        cache.invalidate("prefix")  # 按前缀批量清除
        cache.stats  # -> {"hits": 10, "misses": 3, "evictions": 1, "hit_rate": 0.77, "size": 5}
    """

    def __init__(self, maxsize: int = 256, ttl: int = 300):
        self._lock = threading.Lock()
        self._maxsize = maxsize
        self._ttl = ttl
        self._cache: OrderedDict[str, Any] = OrderedDict()
        self._timestamps: dict[str, float] = {}
        # ── 计数器 ───────────────────────────────────────────
        self._hits = 0
        self._misses = 0
        self._evictions = 0

    def get(self, key: str) -> Optional[Any]:
        now = time.time()
        with self._lock:
            if key not in self._cache:
                self._misses += 1
                return None
            expire_at = self._timestamps.get(key, 0)
            if 0 < expire_at < now:
                # 过期
                del self._cache[key]
                self._timestamps.pop(key, None)
                self._misses += 1
                return None
            # 命中
            self._hits += 1
            # LRU: 移动到末尾（最近使用）
            self._cache.move_to_end(key)
            return self._cache[key]

    def set(self, key: str, value: Any, ttl: Optional[int] = None) -> None:
        with self._lock:
            self._cache[key] = value
            if ttl is not None and ttl > 0:
                self._timestamps[key] = time.time() + ttl
            elif self._ttl > 0:
                self._timestamps[key] = time.time() + self._ttl
            else:
                self._timestamps.pop(key, None)  # 无 TTL
            self._cache.move_to_end(key)
            # LRU 淘汰
            while len(self._cache) > self._maxsize:
                oldest, _ = self._cache.popitem(last=False)
                self._timestamps.pop(oldest, None)
                self._evictions += 1

    def invalidate(self, prefix: str = "") -> None:
        """按 key 前缀失效缓存。prefix="" 清空全部。"""
        with self._lock:
            if not prefix:
                self._cache.clear()
                self._timestamps.clear()
                return
            keys = [k for k in self._cache if k.startswith(prefix)]
            for k in keys:
                del self._cache[k]
                self._timestamps.pop(k, None)

    @property
    def size(self) -> int:
        with self._lock:
            return len(self._cache)

    @property
    def stats(self) -> dict:
        """返回缓存统计信息。"""
        with self._lock:
            total = self._hits + self._misses
            return {
                "layer": getattr(self, "_layer_name", "unknown"),
                "size": len(self._cache),
                "maxsize": self._maxsize,
                "ttl": self._ttl,
                "hits": self._hits,
                "misses": self._misses,
                "total_requests": total,
                "hit_rate": round(self._hits / total, 4) if total > 0 else 0.0,
                "evictions": self._evictions,
            }

    def reset_stats(self) -> None:
        """重置计数器。"""
        with self._lock:
            self._hits = 0
            self._misses = 0
            self._evictions = 0


# ══════════════════════════════════════════════════════════════════════
#  哈希工具
# ══════════════════════════════════════════════════════════════════════

def make_key(*args, **kwargs) -> str:
    """生成一致的缓存 key。

    支持：
        make_key("query_text")
        make_key({"query": "xxx", "filters": {"company": "五粮液"}, "top_k": 8})
    """
    if len(args) == 1 and not kwargs and isinstance(args[0], str):
        raw = args[0]
    else:
        parts = {}
        for i, a in enumerate(args):
            if isinstance(a, dict):
                parts.update(a)
            else:
                parts[str(i)] = a
        parts.update(kwargs)
        raw = json.dumps(parts, sort_keys=True, ensure_ascii=False)
    return hashlib.md5(raw.encode()).hexdigest()


# ══════════════════════════════════════════════════════════════════════
#  全局缓存实例
# ══════════════════════════════════════════════════════════════════════

# Layer 1: Embedding 查询向量缓存（无 TTL，重启清空即可）
# key = md5(query_text), value = list[float]
embedding_cache = LRUCache(maxsize=512, ttl=0)
embedding_cache._layer_name = "embedding"

# Layer 2: LLM 调用缓存（如 query_rewriter / intent_router 的输出）
# key = md5(prompt), value = LLM 返回
llm_cache = LRUCache(maxsize=256, ttl=600)
llm_cache._layer_name = "llm"

# Layer 3: BM25 检索结果缓存
# key = md5(query + filters), value = list[dict]
bm25_cache = LRUCache(maxsize=128, ttl=300)
bm25_cache._layer_name = "bm25"

# Layer 4: 精确查询缓存（整个 pipeline 结果）
# key = md5(query + top_k + intent), value = dict(answer, citations, ...)
query_cache = LRUCache(maxsize=64, ttl=300)
query_cache._layer_name = "query"
