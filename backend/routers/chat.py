"""
finRAG API — Chat 路由
======================
POST /api/v1/chat — 核心检索+生成接口（支持流式SSE）
自动保存对话到文件存储（data/conversations/）。
"""
import json
import logging

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from backend.models.schemas import ChatRequest, ChatResponse
from backend.services.rag_engine import rag_query, rag_stream
from backend.routers.conversations import (
    _load_conv,
    add_message,
    conversation_exists,
    create_conversation,
)

logger = logging.getLogger("api.chat")
router = APIRouter(prefix="/api/v1", tags=["Chat"])


@router.post("/chat")
async def chat_endpoint(req: ChatRequest):
    """
    核心检索与生成接口。

    - stream=true (默认): SSE 流式返回，含 thought→search→answer→done
    - stream=false: 阻塞返回完整 JSON
    - mode: pro=专业(DeepSeek+联网库), safe=安全(Ollama+本地库)

    自动管理对话:
    - session_id="default" → 自动创建新 session 并返回（绑定 mode）
    - 已有 session → 追加消息（使用绑定的 mode）
    """
    # ── 对话管理 ─────────────────────────────────────
    sid = req.session_id
    if sid == "default" or not conversation_exists(sid):
        sid = create_conversation(mode=req.mode)
    else:
        # 已有 session → 读取其绑定的 mode（忽略请求中的 mode）
        conv_data = _load_conv(sid)
        if conv_data:
            req.mode = conv_data.get("mode", "pro")
    # 保存用户问题
    add_message(sid, "user", req.query)

    if req.stream:
        # 流式：在 StreamingResponse 中异步保存
        async def _stream_with_save():
            full_answer = ""
            citations = []
            async for event in rag_stream(req.query, top_k=req.top_k, session_id=sid, mode=req.mode):
                data_part = None
                try:
                    # event 已经是 "data: {...}\n\n" 格式
                    body = event.removeprefix("data: ").strip()
                    parsed = json.loads(body)
                    if parsed.get("type") == "answer" and parsed.get("data"):
                        data_part = parsed["data"]
                        if "citations" in parsed["data"]:
                            citations = parsed["data"]["citations"]
                    if parsed.get("type") == "answer" and parsed.get("content"):
                        full_answer = parsed["content"]
                except Exception:
                    pass
                yield event

            # 流结束后保存助手回答
            if full_answer:
                add_message(sid, "assistant", full_answer, citations)
            else:
                add_message(sid, "assistant", "(无回答)", citations)

        return StreamingResponse(
            _stream_with_save(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
                "X-Session-Id": sid,  # 返回新 session_id
            },
        )

    # ── 阻塞模式 ─────────────────────────────────────
    try:
        result = rag_query(req.query, top_k=req.top_k, stream=False, session_id=sid, mode=req.mode)
        answer = result["answer"]
        citations = result["citations"]

        # 保存助手回答
        add_message(sid, "assistant", answer, citations)

        return ChatResponse(
            answer=answer,
            citations=citations,
            intent=result["intent"],
            elapsed_sec=result["elapsed_sec"],
        )
    except Exception as e:
        logger.error(f"Chat error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/chat/health")
async def health_check():
    """健康检查。"""
    return {"status": "ok", "service": "finRAG Chat API"}
