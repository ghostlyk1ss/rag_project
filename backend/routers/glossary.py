"""
finRAG API — 术语速查路由器
============================
POST /api/v1/glossary/query      — 查术语（先缓存 → 本地模型 → fallback）
POST /api/v1/glossary/query/confirm — 用户确认后走远程 API
GET  /api/v1/glossary/list       — 已缓存术语列表
DELETE /api/v1/glossary/cache    — 清空缓存
"""
import json
import logging
from pathlib import Path

import httpx
from fastapi import APIRouter, HTTPException
from openai import OpenAI
from pydantic import BaseModel

from backend.config import settings

logger = logging.getLogger("api.glossary")
router = APIRouter(prefix="/api/v1", tags=["Glossary"])

# ── 缓存路径 ──────────────────────────────────────────────
CACHE_PATH = Path(settings.BASE_DIR) / "data" / "glossary_cache.json"


# ── 请求/响应模型 ──────────────────────────────────────────
class TermQuery(BaseModel):
    term: str


class TermResponse(BaseModel):
    term: str
    explanation: str
    cached: bool = False
    fallback: bool = False
    tip: str = ""


class CacheListResponse(BaseModel):
    terms: list[str]
    count: int


class CacheClearResponse(BaseModel):
    msg: str


# ── 缓存工具函数 ────────────────────────────────────────────
def _load_cache() -> dict[str, str]:
    """加载术语缓存。"""
    if CACHE_PATH.exists():
        try:
            with open(CACHE_PATH, encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("词汇缓存损坏，重置: %s", exc)
    return {}


def _save_cache(cache: dict[str, str]) -> None:
    """持久化术语缓存。"""
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)


# ── 本地模型可用性检查 ─────────────────────────────────────
async def _check_local_model() -> bool:
    """通过 POST /v1/chat/completions 检查本地 Ollama 是否可用（超时 5s）。"""
    url = f"{settings.SAFE_LLM_BASE_URL.rstrip('/')}/v1/chat/completions"
    payload = {
        "model": settings.SAFE_LLM_MODEL,
        "messages": [{"role": "user", "content": "ping"}],
        "max_tokens": 1,
    }
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(url, json=payload)
            return resp.status_code == 200
    except (httpx.TimeoutException, httpx.RequestError) as exc:
        logger.debug("本地模型不可用: %s", exc)
        return False


# ── 本地模型生成 ────────────────────────────────────────────
async def _generate_local(term: str) -> str:
    """使用本地 Ollama 模型生成术语解释。"""
    client = OpenAI(
        base_url=f"{settings.SAFE_LLM_BASE_URL.rstrip('/')}/v1",
        api_key=settings.SAFE_LLM_API_KEY,
    )
    prompt = f'请用中文解释金融术语"{term}"，包括定义、计算公式（如有）、意义、使用场景。格式为 Markdown。'
    try:
        response = client.chat.completions.create(
            model=settings.SAFE_LLM_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
        )
        return response.choices[0].message.content.strip()
    except Exception as exc:
        logger.error("本地模型生成失败: %s", exc)
        raise HTTPException(status_code=502, detail=f"本地模型生成失败: {exc}")


# ── 远程 API 生成 ───────────────────────────────────────────
async def _generate_remote(term: str) -> str:
    """使用远程 API（如 DeepSeek）生成术语解释。"""
    if not settings.LLM_API_KEY:
        raise HTTPException(status_code=400, detail="远程 API Key 未配置")
    client = OpenAI(
        base_url=settings.LLM_BASE_URL,
        api_key=settings.LLM_API_KEY,
    )
    prompt = f'请用中文解释金融术语"{term}"，包括定义、计算公式（如有）、意义、使用场景。格式为 Markdown。'
    try:
        response = client.chat.completions.create(
            model=settings.LLM_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
        )
        return response.choices[0].message.content.strip()
    except Exception as exc:
        logger.error("远程 API 生成失败: %s", exc)
        raise HTTPException(status_code=502, detail=f"远程 API 生成失败: {exc}")


# ── 端点 ─────────────────────────────────────────────────────

@router.post("/glossary/query", response_model=TermResponse)
async def query_term(req: TermQuery):
    """
    术语速查。

    流程：
    1. 查缓存 → 命中直接返回
    2. 检查本地模型可用性 → 不可用返回 fallback 提示
    3. 本地模型生成 → 写入缓存后返回
    """
    term = (req.term or "").strip()
    if not term:
        raise HTTPException(status_code=400, detail="术语不能为空")

    # 1. 查缓存
    cache = _load_cache()
    if term in cache:
        return TermResponse(term=term, explanation=cache[term], cached=True)

    # 2. 检查本地模型
    local_ok = await _check_local_model()
    if not local_ok:
        return TermResponse(
            term=term,
            explanation="",
            fallback=True,
            tip="本地模型暂不可用，请确认后使用远程 API 查询。",
        )

    # 3. 本地生成
    explanation = await _generate_local(term)
    cache[term] = explanation
    _save_cache(cache)
    return TermResponse(term=term, explanation=explanation, cached=False)


@router.post("/glossary/query/confirm", response_model=TermResponse)
async def query_term_confirm(req: TermQuery):
    """
    用户确认后通过远程 API 查询术语。

    适用于本地模型不可用时的 fallback 路径。
    """
    term = (req.term or "").strip()
    if not term:
        raise HTTPException(status_code=400, detail="术语不能为空")

    # 查缓存
    cache = _load_cache()
    if term in cache:
        return TermResponse(term=term, explanation=cache[term], cached=True)

    # 远程生成
    explanation = await _generate_remote(term)
    cache[term] = explanation
    _save_cache(cache)
    return TermResponse(term=term, explanation=explanation, cached=False)


@router.get("/glossary/list", response_model=CacheListResponse)
async def list_cached_terms():
    """返回已缓存的术语列表。"""
    cache = _load_cache()
    terms = sorted(cache.keys())
    return CacheListResponse(terms=terms, count=len(terms))


@router.delete("/glossary/cache", response_model=CacheClearResponse)
async def clear_cache():
    """清空术语缓存。"""
    if CACHE_PATH.exists():
        CACHE_PATH.unlink()
        logger.info("词汇缓存已清空")
    return CacheClearResponse(msg="词汇缓存已清空")
