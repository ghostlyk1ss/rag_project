"""
finRAG API — 配置
===============
集中管理所有配置，从环境变量或默认值读取。
"""
import os
from pathlib import Path
from typing import Optional

from pydantic_settings import BaseSettings


# ── 模式常量 ───────────────────────────────────────────────
MODE_PRO = "pro"
MODE_SAFE = "safe"


class Settings(BaseSettings):
    # ── 项目路径 ────────────────────────────────────────────────
    BASE_DIR: Path = Path(__file__).resolve().parents[1]
    RAW_DIR: Path = BASE_DIR / "data" / "raw"
    PARSED_DIR: Path = BASE_DIR / "data" / "parsed"
    QDRANT_PATH: Path = BASE_DIR / "data" / "qdrant_db"
    BM25_PATH: Path = BASE_DIR / "data" / "bm25_index.pkl"
    METADATA_PATH: Path = BASE_DIR / "data" / "metadata.json"

    # ── Qdrant (双集合) ────────────────────────────────────────
    QDRANT_COLLECTION: str = "finrag"
    QDRANT_COLLECTION_PRO: str = "finrag_pro"
    QDRANT_COLLECTION_SAFE: str = "finrag_safe"
    QDRANT_HOST: str = "localhost"
    QDRANT_PORT: int = 6333

    # ── LLM 专业模式 (DeepSeek API) ────────────────────────────
    LLM_API_KEY: Optional[str] = os.getenv("DEEPSEEK_API_KEY", "")
    LLM_MODEL: str = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
    LLM_BASE_URL: str = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")

    # ── LLM 安全模式 (本地 Ollama) ─────────────────────────────
    SAFE_LLM_BASE_URL: str = os.getenv("SAFE_LLM_BASE_URL", "http://localhost:11434")
    SAFE_LLM_MODEL: str = os.getenv("SAFE_LLM_MODEL", "qwen2.5:7b")
    SAFE_LLM_API_KEY: str = "ollama"  # Ollama 不需要真实 key

    def get_collection_name(self, mode: str = MODE_PRO) -> str:
        """根据模式返回对应的 Qdrant 集合名。"""
        if mode == MODE_SAFE:
            return self.QDRANT_COLLECTION_SAFE
        return self.QDRANT_COLLECTION_PRO

    # ── Embedding ───────────────────────────────────────────────
    EMBED_MODEL: str = os.getenv("EMBED_MODEL_PATH",
        os.path.expanduser("~/.cache/huggingface/hub/models--BAAI--bge-large-zh-v1.5/snapshots/79e7739b6ab944e86d6171e44d24c997fc1e0116")
    )
    EMBED_DIM: int = 1024
    EMBED_BATCH_SIZE: int = 32

    # ── Server ──────────────────────────────────────────────────
    API_HOST: str = "0.0.0.0"
    API_PORT: int = 8000
    CORS_ORIGINS: list[str] = [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost",       # Nginx gateway
        "http://127.0.0.1",      # Nginx gateway
    ]

    # ── 预算控制 ────────────────────────────────────────────────
    DAILY_BUDGET_YUAN: float = float(os.getenv("DAILY_BUDGET_YUAN", "50"))   # ¥50/天
    ALL_TIME_BUDGET_YUAN: float = float(os.getenv("ALL_TIME_BUDGET_YUAN", "200"))  # ¥200/累计
    SESSION_BUDGET_YUAN: float = float(os.getenv("SESSION_BUDGET_YUAN", "5"))  # ¥5/会话
    REQUEST_BUDGET_YUAN: float = float(os.getenv("REQUEST_BUDGET_YUAN", "1"))  # ¥1/请求
    BUDGET_DEGRADE: str = os.getenv("BUDGET_DEGRADE", "cache_only")  # 超限策略: cache_only / reject / warn

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"


settings = Settings()

# ── 运行时预算覆盖（可通过 API 动态修改） ─────────────────
_runtime_budget: dict[str, float | str] = {}


def get_budget(key: str, default: float | str = 0) -> float | str:
    """获取预算值，优先使用运行时覆盖。"""
    if key in _runtime_budget:
        return _runtime_budget[key]
    return getattr(settings, key, default)


def set_budget(**kwargs) -> None:
    """设置运行时预算覆盖。支持: daily, all_time, session, request, degrade"""
    mapping = {
        "daily": "DAILY_BUDGET_YUAN",
        "all_time": "ALL_TIME_BUDGET_YUAN",
        "session": "SESSION_BUDGET_YUAN",
        "request": "REQUEST_BUDGET_YUAN",
        "degrade": "BUDGET_DEGRADE",
    }
    for short_key, val in kwargs.items():
        full_key = mapping.get(short_key)
        if full_key:
            _runtime_budget[full_key] = val
            import logging
            logging.getLogger("config").info(f"💰 预算更新: {full_key}={val}")


def get_all_budget() -> dict[str, float | str]:
    """获取所有预算值（含运行时覆盖）。"""
    return {
        "daily_budget": get_budget("DAILY_BUDGET_YUAN", 50),
        "all_time_budget": get_budget("ALL_TIME_BUDGET_YUAN", 200),
        "session_budget": get_budget("SESSION_BUDGET_YUAN", 5),
        "request_budget": get_budget("REQUEST_BUDGET_YUAN", 1),
        "degrade_strategy": get_budget("BUDGET_DEGRADE", "cache_only"),
    }
