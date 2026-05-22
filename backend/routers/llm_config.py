"""
finRAG API — LLM 配置管理路由
=============================
提供 LLM 配置的查看、更新和连接测试 API。
配置持久化到 data/llm_config.json（API key 使用 base64 简单混淆）。
"""
import base64
import json
import logging
import os
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from openai import OpenAI
from pydantic import BaseModel, Field

from backend.config import settings

logger = logging.getLogger("api.llm_config")
router = APIRouter(prefix="/api/v1", tags=["LLM Config"])

# ── 配置存储路径 ──────────────────────────────────────────
_CONFIG_DIR = settings.BASE_DIR / "data"
_CONFIG_FILE = _CONFIG_DIR / "llm_config.json"


# ── Pydantic 模型 ──────────────────────────────────────────

class ProfessionalConfig(BaseModel):
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    model: Optional[str] = None


class SafeConfig(BaseModel):
    base_url: Optional[str] = None
    model: Optional[str] = None


class LLMConfigUpdate(BaseModel):
    professional: Optional[ProfessionalConfig] = None
    safe: Optional[SafeConfig] = None


class LLMConfigResponse(BaseModel):
    professional: dict
    safe: dict


class LLMTestRequest(BaseModel):
    mode: str = Field(default="pro", description="测试模式: pro 或 safe")
    message: str = Field(default="Hi", description="测试消息")
    api_key: Optional[str] = Field(default=None, description="可选：内联 API key，不传则使用已保存的")
    base_url: Optional[str] = Field(default=None, description="可选：内联 Base URL，不传则使用已保存的")
    model: Optional[str] = Field(default=None, description="可选：内联模型名，不传则使用已保存的")


class LLMTestResponse(BaseModel):
    success: bool
    message: str


# ── base64 混淆工具 ────────────────────────────────────────

def _obfuscate(text: str) -> str:
    """简单混淆：base64 编码。"""
    return base64.b64encode(text.encode()).decode()


def _deobfuscate(text: str) -> str:
    """解混淆：base64 解码。"""
    try:
        return base64.b64decode(text.encode()).decode()
    except Exception:
        return text


def _mask_key(key: str) -> str:
    """脱敏显示 API key：只显示前 4 位和后 4 位。"""
    if not key or len(key) < 8:
        return "****"
    return key[:4] + "****" + key[-4:]


# ── 配置加载与保存 ─────────────────────────────────────────

def _load_config() -> dict:
    """从文件加载配置，若文件不存在则从 settings 初始化。"""
    if _CONFIG_FILE.exists():
        try:
            with open(_CONFIG_FILE, "r") as f:
                raw = json.load(f)
            # 解码 api_key
            if "professional" in raw and raw["professional"].get("api_key"):
                raw["professional"]["api_key"] = _deobfuscate(raw["professional"]["api_key"])
            return raw
        except (json.JSONDecodeError, KeyError) as e:
            logger.warning(f"⚠️  llm_config.json 解析失败: {e}，使用默认配置")

    # 从 settings 初始化
    return {
        "professional": {
            "api_key": settings.LLM_API_KEY or "",
            "base_url": settings.LLM_BASE_URL,
            "model": settings.LLM_MODEL,
        },
        "safe": {
            "base_url": settings.SAFE_LLM_BASE_URL,
            "model": settings.SAFE_LLM_MODEL,
        },
    }


def _save_config(cfg: dict) -> None:
    """保存配置到文件（api_key 先 base64 混淆）。"""
    _CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    to_save = {
        "professional": {
            "api_key": _obfuscate(cfg["professional"]["api_key"]),
            "base_url": cfg["professional"]["base_url"],
            "model": cfg["professional"]["model"],
        },
        "safe": {
            "base_url": cfg["safe"]["base_url"],
            "model": cfg["safe"]["model"],
        },
    }
    with open(_CONFIG_FILE, "w") as f:
        json.dump(to_save, f, indent=2, ensure_ascii=False)
    logger.info("✅ LLM 配置已持久化到 %s", _CONFIG_FILE)


def _apply_config_to_runtime(cfg: dict) -> None:
    """将配置应用到运行时环境（os.environ + settings）。"""
    pro = cfg["professional"]
    safe = cfg["safe"]

    # 更新 os.environ（llm.py 等模块读取的来源）
    if pro["api_key"]:
        os.environ["DEEPSEEK_API_KEY"] = pro["api_key"]
    os.environ["DEEPSEEK_BASE_URL"] = pro["base_url"]
    os.environ["DEEPSEEK_MODEL"] = pro["model"]
    os.environ["SAFE_LLM_BASE_URL"] = safe["base_url"]
    os.environ["SAFE_LLM_MODEL"] = safe["model"]

    # 更新 settings 对象
    settings.LLM_API_KEY = pro["api_key"]
    settings.LLM_BASE_URL = pro["base_url"]
    settings.LLM_MODEL = pro["model"]
    settings.SAFE_LLM_BASE_URL = safe["base_url"]
    settings.SAFE_LLM_MODEL = safe["model"]

    logger.info("⚙️  LLM 配置已应用到运行时")


# ── API 端点 ────────────────────────────────────────────────

@router.get("/llm/config", response_model=LLMConfigResponse)
async def get_llm_config():
    """获取当前 LLM 配置（API key 脱敏显示）。"""
    cfg = _load_config()
    return {
        "professional": {
            "api_key": _mask_key(cfg["professional"]["api_key"]),
            "base_url": cfg["professional"]["base_url"],
            "model": cfg["professional"]["model"],
        },
        "safe": {
            "base_url": cfg["safe"]["base_url"],
            "model": cfg["safe"]["model"],
        },
    }


@router.put("/llm/config", response_model=LLMConfigResponse)
async def update_llm_config(update: LLMConfigUpdate):
    """更新 LLM 配置，仅更新提供的字段，保留未提供的字段。"""
    cfg = _load_config()

    # 合并专业模式配置
    if update.professional:
        pro = update.professional
        if pro.api_key is not None:
            cfg["professional"]["api_key"] = pro.api_key
        if pro.base_url is not None:
            cfg["professional"]["base_url"] = pro.base_url
        if pro.model is not None:
            cfg["professional"]["model"] = pro.model

    # 合并安全模式配置
    if update.safe:
        s = update.safe
        if s.base_url is not None:
            cfg["safe"]["base_url"] = s.base_url
        if s.model is not None:
            cfg["safe"]["model"] = s.model

    # 持久化 + 应用到运行时
    _save_config(cfg)
    _apply_config_to_runtime(cfg)

    logger.info("✏️  LLM 配置已更新")

    return {
        "professional": {
            "api_key": _mask_key(cfg["professional"]["api_key"]),
            "base_url": cfg["professional"]["base_url"],
            "model": cfg["professional"]["model"],
        },
        "safe": {
            "base_url": cfg["safe"]["base_url"],
            "model": cfg["safe"]["model"],
        },
    }


@router.post("/llm/test", response_model=LLMTestResponse)
async def test_llm_connection(req: LLMTestRequest):
    """测试 LLM 连接，发送一条简单消息。支持内联参数（不传则使用已保存配置）。"""
    cfg = _load_config()

    if req.mode == "pro":
        api_key = req.api_key or cfg["professional"]["api_key"]
        base_url = req.base_url or cfg["professional"]["base_url"]
        model = req.model or cfg["professional"]["model"]
        if not api_key:
            raise HTTPException(status_code=400, detail="❌ 专业模式 API key 未配置")
    elif req.mode == "safe":
        api_key = req.api_key or "ollama"
        base_url = req.base_url or cfg["safe"]["base_url"]
        model = req.model or cfg["safe"]["model"]
    else:
        raise HTTPException(status_code=400, detail=f"不支持的模式: {req.mode}，应为 pro 或 safe")

    try:
        client = OpenAI(
            api_key=api_key,
            base_url=base_url,
        )
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": req.message}],
            max_tokens=50,
        )
        reply = resp.choices[0].message.content or "(empty response)"
        logger.info("🔌 LLM 连接测试成功 (%s): %s", req.mode, reply[:80])
        return LLMTestResponse(
            success=True,
            message=f"✅ 连接成功！LLM 回复: {reply[:200]}",
        )
    except Exception as e:
        logger.error("🔌 LLM 连接测试失败 (%s): %s", req.mode, e)
        return LLMTestResponse(
            success=False,
            message=f"❌ 连接失败: {e}",
        )
