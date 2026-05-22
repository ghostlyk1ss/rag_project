"""
finRAG API — FastAPI 主应用
============================
优化启动速度：模型在首次请求时懒加载（不是启动时加载）。
"""
import logging
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# 将项目根目录加入 sys.path（确保 scripts/ 可导入）
_BASE = Path(__file__).resolve().parents[1]
if str(_BASE) not in sys.path:
    sys.path.insert(0, str(_BASE))

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
)
# 抑制第三方库日志
for lib in ("httpx", "urllib3", "openai", "sentence_transformers",
             "transformers", "qdrant_client", "httpcore"):
    logging.getLogger(lib).setLevel(logging.WARNING)

logger = logging.getLogger("finrag")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期。
    
    启动：不加载模型（首次请求时懒加载）
    关闭：清理资源
    """
    logger.info("🚀 finRAG API 启动中...")
    logger.info("   模型将在首次请求时懒加载（首次~15s）")

    # 应用持久化的 LLM 配置到运行时（使 llm.py 能读到正确的 env vars）
    try:
        from backend.routers.llm_config import _load_config, _apply_config_to_runtime
        cfg = _load_config()
        _apply_config_to_runtime(cfg)
        logger.info("   ✅ LLM 配置已从文件加载")
    except Exception as e:
        logger.warning(f"   ⚠️  LLM 配置加载失败: {e}")

    yield
    logger.info("🛑 finRAG API 关闭")


app = FastAPI(
    title="finRAG API",
    description="金融文档智能问答系统 — FastAPI 后端",
    version="2.0.0",
    lifespan=lifespan,
)

# ── CORS ─────────────────────────────────────────────────────────
from backend.config import settings

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

        # ── 注册路由 ──────────────────────────────────────────────────────
from backend.routers import chat, ingest, pdf, doc, sources, conversations, metrics, glossary, llm_config

app.include_router(chat.router)
app.include_router(ingest.router)
app.include_router(pdf.router)
app.include_router(doc.router)
app.include_router(sources.router)
app.include_router(conversations.router)
app.include_router(metrics.router)
app.include_router(glossary.router)
app.include_router(llm_config.router)


@app.get("/health")
async def health():
    """健康检查端点。"""
    return {"status": "ok", "version": "2.0.0"}


@app.get("/")
async def root():
    """API 根路径 — 返回基本信息。"""
    return {
        "name": "finRAG API",
        "version": "2.0.0",
        "docs": "/docs",
        "endpoints": {
            "chat": "POST /api/v1/chat (支持SSE流式)",
            "ingest": "POST /api/v1/ingest",
            "sources": "GET /api/v1/sources",
            "sources_detail": "GET /api/v1/sources/{doc_id}",
            "pdf": "GET /api/v1/pdf/{doc_id}",
        },
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "backend.main:app",
        host=settings.API_HOST,
        port=settings.API_PORT,
        reload=True,
        log_level="info",
    )
