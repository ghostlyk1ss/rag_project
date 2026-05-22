"""
finRAG API — Conversations 路由
================================
基于文件的对话存储（生产环境应换 Redis / SQLite）。
GET    /api/v1/conversations        — 列出所有对话
GET    /api/v1/conversations/{sid}  — 获取对话详情（含 messages）
DELETE /api/v1/conversations/{sid}  — 删除对话
"""
import json
import logging
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException

from backend.config import settings
from backend.models.schemas import (
    ConversationDetail,
    ConversationListResponse,
    ConversationMessage,
    ConversationPreview,
)

logger = logging.getLogger("api.conversations")
router = APIRouter(prefix="/api/v1", tags=["Conversations"])

CONV_DIR: Path = settings.BASE_DIR / "data" / "conversations"


def _ensure_dir():
    CONV_DIR.mkdir(parents=True, exist_ok=True)


def _conv_path(session_id: str) -> Path:
    return CONV_DIR / f"{session_id}.json"


def _load_conv(session_id: str) -> Optional[dict]:
    path = _conv_path(session_id)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning(f"读取对话 {session_id} 失败: {e}")
        return None


def _save_conv(data: dict):
    _ensure_dir()
    path = _conv_path(data["session_id"])
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _list_convs() -> list[dict]:
    _ensure_dir()
    convs = []
    for p in sorted(CONV_DIR.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            convs.append(data)
        except Exception as e:
            logger.warning(f"读取 {p.name} 失败: {e}")
    return convs


def _to_preview(data: dict) -> ConversationPreview:
    msgs = data.get("messages", [])
    return ConversationPreview(
        session_id=data["session_id"],
        title=data.get("title", "未命名对话"),
        message_count=len(msgs),
        created_at=data.get("created_at", ""),
        updated_at=data.get("updated_at", ""),
        mode=data.get("mode", "pro"),
    )


# ── 公共方法（供 chat.py 调用） ───────────────────────────


def create_conversation(session_id: Optional[str] = None, mode: str = "pro") -> str:
    """创建新对话，返回 session_id。模式将绑定到该对话。"""
    sid = session_id or str(uuid.uuid4())[:8]
    now = datetime.now().isoformat()
    data = {
        "session_id": sid,
        "title": "新对话",
        "mode": mode,
        "messages": [],
        "created_at": now,
        "updated_at": now,
    }
    _save_conv(data)
    return sid


def add_message(session_id: str, role: str, content: str, citations: list = None):
    """追加消息到已有对话。"""
    data = _load_conv(session_id)
    if not data:
        return
    msg = {"role": role, "content": content, "citations": citations or []}
    data["messages"].append(msg)
    data["updated_at"] = datetime.now().isoformat()
    # 自动更新标题（取第一条用户消息）
    if data["title"] == "新对话" and role == "user":
        data["title"] = content[:30] + ("..." if len(content) > 30 else "")
    _save_conv(data)


def conversation_exists(session_id: str) -> bool:
    return _conv_path(session_id).exists()


# ── 路由 ───────────────────────────────────────────────────


@router.get("/conversations", response_model=ConversationListResponse)
async def list_conversations():
    """列出所有对话（降序排列）。"""
    convs = _list_convs()
    previews = [_to_preview(c) for c in convs]
    return ConversationListResponse(total=len(previews), conversations=previews)


@router.get("/conversations/{session_id}", response_model=ConversationDetail)
async def get_conversation(session_id: str):
    """获取对话详情（含 messages）。"""
    data = _load_conv(session_id)
    if not data:
        raise HTTPException(status_code=404, detail="对话不存在")
    return ConversationDetail(
        session_id=data["session_id"],
        title=data.get("title", "未命名对话"),
        messages=[ConversationMessage(**m) for m in data.get("messages", [])],
        created_at=data.get("created_at", ""),
        updated_at=data.get("updated_at", ""),
        mode=data.get("mode", "pro"),
    )


@router.delete("/conversations/{session_id}")
async def delete_conversation(session_id: str):
    """删除对话。"""
    path = _conv_path(session_id)
    if not path.exists():
        raise HTTPException(status_code=404, detail="对话不存在")
    path.unlink()
    logger.info(f"🗑️ 删除对话: {session_id}")
    return {"status": "ok", "message": "对话已删除"}
