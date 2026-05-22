"""
finRAG — Token & Cost Tracker
==============================
追踪每次 LLM 调用的 token 消耗和费用。

分层追踪：
  - Call 级：单次 LLM 调用的 prompt/completion tokens
  - Session 级：单次用户会话累计
  - Daily 级：当日所有请求累计

用法:
    from backend.services.cost_tracker import track_call, get_session_stats, get_daily_stats

    # 在 LLM 调用后上报
    track_call(session_id="abc123", prompt_tokens=150, completion_tokens=50, model="deepseek-chat")

    # 查询
    stats = get_session_stats("abc123")
    daily = get_daily_stats()
"""

import json
import logging
import os
import threading
import time
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger("cost_tracker")

# ── 持久化路径 ──────────────────────────────────────────────
_COST_DATA_FILE = Path(__file__).resolve().parents[2] / "data" / "cost_history.json"


def _load_persisted() -> tuple[list, dict, dict]:
    """从 JSON 文件中加载持久化的成本数据。"""
    if not _COST_DATA_FILE.exists():
        return [], defaultdict(lambda: {"prompt_tokens": 0, "completion_tokens": 0,
                                         "total_tokens": 0, "cost": 0.0, "call_count": 0}), {}
    try:
        with open(_COST_DATA_FILE, "r") as f:
            raw = json.load(f)
        call_log = raw.get("call_log", [])
        daily_totals = defaultdict(
            lambda: {"prompt_tokens": 0, "completion_tokens": 0,
                     "total_tokens": 0, "cost": 0.0, "call_count": 0},
            {k: v for k, v in raw.get("daily_totals", {}).items()}
        )
        session_totals = raw.get("session_totals", {})
        for sid, s in session_totals.items():
            for k in ("prompt_tokens", "completion_tokens", "total_tokens", "call_count"):
                s[k] = int(s.get(k, 0))
            s["cost"] = float(s.get("cost", 0))
        logger.info(f"📂 加载成本历史: {len(call_log)} 条调用, "
                     f"{len(daily_totals)} 天记录")
        return call_log, daily_totals, session_totals
    except Exception as e:
        logger.warning(f"⚠️ 成本历史加载失败: {e}，使用空数据")
        return [], defaultdict(lambda: {"prompt_tokens": 0, "completion_tokens": 0,
                                         "total_tokens": 0, "cost": 0.0, "call_count": 0}), {}


def _save_persisted(call_log, daily_totals, session_totals):
    """将成本数据持久化到 JSON 文件。"""
    try:
        _COST_DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "call_log": call_log,
            "daily_totals": dict(daily_totals),
            "session_totals": dict(session_totals),
        }
        with open(_COST_DATA_FILE, "w") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"❌ 成本历史持久化失败: {e}")

# ── DeepSeek 定价（¥/1M tokens） ──────────────────────────────
# DeepSeek API 参考价：输入 ¥0.5/M, 输出 ¥2/M
PRICING: dict[str, dict[str, float]] = {
    "deepseek-chat": {
        "input_per_m": 0.5,
        "output_per_m": 2.0,
    },
    "deepseek-coder": {
        "input_per_m": 0.5,
        "output_per_m": 2.0,
    },
}

DEFAULT_PRICE = {"input_per_m": 0.5, "output_per_m": 2.0}


def _calc_cost(prompt_tokens: int, completion_tokens: int, model: str) -> float:
    """计算单次调用的费用（¥）。"""
    p = PRICING.get(model, DEFAULT_PRICE)
    cost = (prompt_tokens / 1_000_000) * p["input_per_m"]
    cost += (completion_tokens / 1_000_000) * p["output_per_m"]
    return round(cost, 6)


# ── 数据存储（线程安全） ─────────────────────────────────────
_lock = threading.Lock()

# 从 JSON 文件加载持久化数据
_call_log, _daily_totals, _session_totals_raw = _load_persisted()

# 每次调用的完整记录（保留最近 10000 条）
# _call_log 已从文件加载

# 按 session 累计（从加载的 session_totals 重建）
_session_totals: dict[str, dict] = defaultdict(lambda: {
    "prompt_tokens": 0,
    "completion_tokens": 0,
    "total_tokens": 0,
    "cost": 0.0,
    "call_count": 0,
    "first_call_at": "",
    "last_call_at": "",
})
for sid, s in _session_totals_raw.items():
    _session_totals[sid] = s

# 每日累计（按 YYYY-MM-DD 分桶）— _daily_totals 已从文件加载为 defaultdict
# Session 摘要缓存（避免重复计算）
_session_summaries: dict[str, dict] = {}


def track_call(
    session_id: str = "default",
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    model: str = "deepseek-chat",
    caller: str = "",
) -> dict:
    """记录一次 LLM 调用的 token 消耗。

    Args:
        session_id: 会话 ID
        prompt_tokens: 输入 token 数
        completion_tokens: 输出 token 数
        model: 模型名（用于定价）
        caller: 调用方标识（如 "intent_router", "query_rewriter"）

    Returns:
        {"cost": 本次费用, "prompt_tokens": ..., "completion_tokens": ...}
    """
    now = datetime.now()
    day_key = now.strftime("%Y-%m-%d")
    now_iso = now.isoformat()

    cost = _calc_cost(prompt_tokens, completion_tokens, model)

    with _lock:
        # Call 日志（保留最近 10000 条）
        _call_log.append({
            "session_id": session_id,
            "model": model,
            "caller": caller,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
            "cost": cost,
            "timestamp": now_iso,
        })
        if len(_call_log) > 10000:
            _call_log.pop(0)

        # Session 累计
        s = _session_totals[session_id]
        s["prompt_tokens"] += prompt_tokens
        s["completion_tokens"] += completion_tokens
        s["total_tokens"] += prompt_tokens + completion_tokens
        s["cost"] = round(s["cost"] + cost, 6)
        s["call_count"] += 1
        if not s["first_call_at"]:
            s["first_call_at"] = now_iso
        s["last_call_at"] = now_iso

        # 日累计
        d = _daily_totals[day_key]
        d["prompt_tokens"] += prompt_tokens
        d["completion_tokens"] += completion_tokens
        d["total_tokens"] += prompt_tokens + completion_tokens
        d["cost"] = round(d["cost"] + cost, 6)
        d["call_count"] += 1

        # 持久化到文件
        _save_persisted(_call_log, _daily_totals, dict(_session_totals))

    logger.debug(
        f"💰 [{model}] prompt={prompt_tokens} comp={completion_tokens} "
        f"¥{cost:.6f} (session={session_id[:8]})"
    )

    return {"cost": cost, "prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens}


def get_session_stats(session_id: str) -> dict:
    """获取指定会话的累计 token 消耗。"""
    with _lock:
        s = _session_totals.get(session_id)
        if not s:
            return {
                "session_id": session_id,
                "prompt_tokens": 0, "completion_tokens": 0,
                "total_tokens": 0, "cost": 0.0, "call_count": 0,
            }
        return {
            "session_id": session_id,
            **s,
        }


def get_all_session_stats() -> list[dict]:
    """获取所有会话的摘要（按最后调用时间降序）。"""
    with _lock:
        return sorted(
            [{"session_id": sid, **s} for sid, s in _session_totals.items()],
            key=lambda x: x["last_call_at"],
            reverse=True,
        )


def get_daily_stats(days: int = 7) -> dict:
    """获取每日统计摘要。

    Args:
        days: 返回最近 N 天的数据

    Returns:
        {"today": {...}, "history": [...], "total_all_time": {...}}
    """
    today = datetime.now().strftime("%Y-%m-%d")
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

    with _lock:
        today_data = dict(_daily_totals.get(today, {}))
        today_data["date"] = today

        history = []
        for day_key in sorted(_daily_totals.keys(), reverse=True):
            if day_key >= cutoff:
                history.append({"date": day_key, **_daily_totals[day_key]})
            else:
                break  # 已排序，超过时间范围

        # 全量累计
        all_time = {"prompt_tokens": 0, "completion_tokens": 0,
                     "total_tokens": 0, "cost": 0.0, "call_count": 0}
        for d in _daily_totals.values():
            for k in all_time:
                all_time[k] += d[k]
        all_time["cost"] = round(all_time["cost"], 6)

    return {
        "today": today_data,
        "history": history,
        "all_time": all_time,
    }


def get_recent_calls(limit: int = 50) -> list[dict]:
    """获取最近的 LLM 调用记录。"""
    with _lock:
        return list(_call_log[-limit:])


def reset_all() -> None:
    """重置所有统计数据，清空历史文件。"""
    with _lock:
        _call_log.clear()
        _session_totals.clear()
        _daily_totals.clear()
        _session_summaries.clear()
        # 删除持久化文件
        try:
            if _COST_DATA_FILE.exists():
                _COST_DATA_FILE.unlink()
                logger.info("🗑️ 成本历史文件已删除")
        except Exception as e:
            logger.error(f"❌ 删除成本历史文件失败: {e}")
