"""
finRAG API — Metrics 路由
=========================
GET /api/v1/metrics — 缓存命中率 + Token 消耗概览
GET /api/v1/metrics/cache — 缓存各层详细统计
GET /api/v1/metrics/cost — Token 消耗和费用明细
"""
import logging

from fastapi import APIRouter

logger = logging.getLogger("api.metrics")
router = APIRouter(prefix="/api/v1/metrics", tags=["Metrics"])


@router.get("")
async def metrics_overview():
    """缓存 + 费用概览。"""
    cache_stats = _get_all_cache_stats()
    cost = _get_all_cost_stats()
    return {
        "caches": cache_stats,
        "cost": cost,
        "budget": _get_budget_summary(),
        "summary": {
            "total_cache_layers": len(cache_stats),
            "total_cache_size": sum(c["size"] for c in cache_stats),
            "total_llm_calls_today": cost.get("today", {}).get("call_count", 0),
            "total_cost_today": cost.get("today", {}).get("cost", 0),
        },
    }


@router.get("/cache")
async def cache_stats():
    """各缓存层详细统计。"""
    return {"layers": _get_all_cache_stats()}


@router.get("/cost")
async def cost_stats():
    """Token 消耗和费用明细。"""
    return _get_all_cost_stats()


@router.delete("/cost")
async def reset_cost_history():
    """清空所有成本历史记录。"""
    try:
        from backend.services.cost_tracker import reset_all
        reset_all()
        return {"status": "ok", "message": "成本历史已清空"}
    except Exception as e:
        logger.error(f"重置成本历史失败: {e}")
        return {"status": "error", "message": str(e)}


@router.put("/budget")
async def update_budget(body: dict):
    """动态修改预算设置（运行时生效，不持久化）。"""
    from backend.config import set_budget
    allowed = {"daily", "all_time", "session", "request", "degrade"}
    updated = {}
    for key, val in body.items():
        if key in allowed:
            set_budget(**{key: val})
            updated[key] = val
    logger.info(f"💰 预算配置已更新: {updated}")
    from backend.config import get_all_budget
    return {"status": "ok", "updated": updated, "budget": get_all_budget()}


# ── 辅助函数 ─────────────────────────────────────────────────


def _get_all_cache_stats() -> list[dict]:
    """收集所有缓存层的统计信息。"""
    try:
        from backend.services.cache import (
            embedding_cache, llm_cache, bm25_cache, query_cache,
        )
        return [
            embedding_cache.stats,
            llm_cache.stats,
            bm25_cache.stats,
            query_cache.stats,
        ]
    except ImportError:
        return []


def _get_all_cost_stats() -> dict:
    """收集费用统计信息。"""
    try:
        from backend.services.cost_tracker import get_daily_stats, get_recent_calls
        daily = get_daily_stats(days=7)
        calls = get_recent_calls(limit=20)
        return {**daily, "recent_calls": calls}
    except ImportError:
        return {"today": {}, "history": [], "all_time": {}, "recent_calls": []}


def _get_budget_summary() -> dict:
    """收集预算摘要。"""
    try:
        from backend.services.budget_guard import get_budget_summary
        return get_budget_summary()
    except ImportError:
        return {}
