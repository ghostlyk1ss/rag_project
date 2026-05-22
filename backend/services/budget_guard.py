"""
finRAG — 预算守卫
=================
检查 Token 消耗是否接近/超过预算阈值，决定降级策略。

策略：
  - warn:        仅日志警告，不拦截（默认行为）
  - cache_only:  超过后只返回缓存结果，不调 LLM
  - reject:      超过后直接拒绝请求

用法:
    from backend.services.budget_guard import check_budget, get_degrade_action

    action = check_budget(session_id="abc")
    if action == "reject":
        return {"error": "今日预算已用完"}
    elif action == "cache_only":
        # 只查缓存不调 LLM
        result = check_cache(query)
"""

import logging

from backend.config import get_budget, get_all_budget
from backend.services.cost_tracker import get_daily_stats, get_session_stats

logger = logging.getLogger("budget_guard")

# ── 阈值系数（到达多少 % 时触发对应行为） ─────────────────────
WARN_THRESHOLD = 0.8       # 80% 时 warn
CACHE_ONLY_THRESHOLD = 0.95  # 95% 时 cache_only
REJECT_THRESHOLD = 1.0     # 100% 时 reject


def check_budget(session_id: str = "default") -> dict:
    """检查当前预算状态。

    Returns:
        {
            "action": "proceed" | "warn" | "cache_only" | "reject",
            "daily": {...},
            "session": {...},
            "message": "..."
        }
    """
    daily = get_daily_stats(days=1)
    session = get_session_stats(session_id)

    daily_cost = daily.get("today", {}).get("cost", 0.0)
    session_cost = session.get("cost", 0.0)

    daily_ratio = daily_cost / float(get_budget("DAILY_BUDGET_YUAN", 50)) if float(get_budget("DAILY_BUDGET_YUAN", 50)) > 0 else 0
    session_ratio = session_cost / float(get_budget("SESSION_BUDGET_YUAN", 5)) if float(get_budget("SESSION_BUDGET_YUAN", 5)) > 0 else 0

    # 超预算最严重的一方决定行为
    max_ratio = max(daily_ratio, session_ratio)
    degrade = get_budget("BUDGET_DEGRADE", "cache_only")

    if max_ratio >= REJECT_THRESHOLD and degrade in ("reject", "cache_only"):
        action = "reject"
        msg = f"⚠️ 预算已耗尽（日¥{daily_cost:.4f}/{float(get_budget('DAILY_BUDGET_YUAN', 50))}，会话¥{session_cost:.4f}/{float(get_budget('SESSION_BUDGET_YUAN', 5))}）"
    elif max_ratio >= CACHE_ONLY_THRESHOLD and degrade == "cache_only":
        action = "cache_only"
        msg = f"⚠️ 预算接近上限（日¥{daily_cost:.4f}/{float(get_budget('DAILY_BUDGET_YUAN', 50))}，仅使用缓存）"
    elif max_ratio >= WARN_THRESHOLD:
        action = "warn"
        msg = f"⚠️ 预算使用率 {max_ratio:.0%}（日¥{daily_cost:.4f}/{float(get_budget('DAILY_BUDGET_YUAN', 50))}）"
    else:
        action = "proceed"
        msg = ""

    result = {
        "action": action,
        "daily_cost": daily_cost,
        "daily_budget": float(get_budget("DAILY_BUDGET_YUAN", 50)),
        "daily_ratio": round(daily_ratio, 4),
        "session_cost": session_cost,
        "session_budget": float(get_budget("SESSION_BUDGET_YUAN", 5)),
        "session_ratio": round(session_ratio, 4),
        "message": msg,
    }

    if action != "proceed":
        logger.warning(msg)

    return result


def get_budget_summary() -> dict:
    """获取预算摘要（用于 metrics 端点）。"""
    daily = get_daily_stats(days=1)
    daily_cost = daily.get("today", {}).get("cost", 0.0)
    all_time_cost = daily.get("all_time", {}).get("cost", 0.0)
    daily_ratio = daily_cost / float(get_budget("DAILY_BUDGET_YUAN", 50)) if float(get_budget("DAILY_BUDGET_YUAN", 50)) > 0 else 0
    all_time_ratio = all_time_cost / float(get_budget("ALL_TIME_BUDGET_YUAN", 200)) if float(get_budget("ALL_TIME_BUDGET_YUAN", 200)) > 0 else 0
    return {
        "daily_budget": float(get_budget("DAILY_BUDGET_YUAN", 50)),
        "all_time_budget": float(get_budget("ALL_TIME_BUDGET_YUAN", 200)),
        "session_budget": float(get_budget("SESSION_BUDGET_YUAN", 5)),
        "request_budget": float(get_budget("REQUEST_BUDGET_YUAN", 1)),
        "degrade_strategy": str(get_budget("BUDGET_DEGRADE", "cache_only")),
        "daily_cost": daily_cost,
        "daily_ratio": round(daily_ratio, 4),
        "daily_remaining": round(float(get_budget("DAILY_BUDGET_YUAN", 50)) - daily_cost, 6),
        "all_time_cost": round(all_time_cost, 6),
        "all_time_ratio": round(all_time_ratio, 4),
        "all_time_remaining": round(float(get_budget("ALL_TIME_BUDGET_YUAN", 200)) - all_time_cost, 6),
    }
