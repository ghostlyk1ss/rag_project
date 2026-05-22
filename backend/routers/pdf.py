"""
finRAG API — PDF 文件服务路由
==============================
GET /api/v1/pdf/{doc_id} — 根据 doc_id 返回对应的 PDF 文件（内联预览）
"""
import logging
import re
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, Response

from backend.config import settings

logger = logging.getLogger("api.pdf")
router = APIRouter(prefix="/api/v1", tags=["PDF"])


@router.get("/pdf/{doc_id}")
async def serve_pdf(doc_id: str):
    """
    根据 doc_id 返回对应的 PDF 文件。
    强制 Content-Disposition: inline，确保浏览器内联预览而非下载。
    """
    # ── 安全验证 ───────────────────────────────────────────
    if ".." in doc_id or "/" in doc_id or "\\" in doc_id:
        raise HTTPException(status_code=400, detail="无效的 doc_id：禁止路径遍历")

    if not re.match(r'^[\w\u4e00-\u9fff\-\.]+$', doc_id):
        raise HTTPException(status_code=400, detail="doc_id 包含非法字符")

    # ── 查找文件 ───────────────────────────────────────────
    pdf_path = settings.RAW_DIR / f"{doc_id}.pdf"

    if not pdf_path.exists():
        logger.warning(f"PDF 文件未找到: {pdf_path}")
        raise HTTPException(status_code=404, detail=f"PDF 文件 {doc_id}.pdf 未找到")

    if not pdf_path.is_file():
        raise HTTPException(status_code=400, detail="路径不是文件")

    # 安全校验：确认文件在 RAW_DIR 下
    try:
        resolved = pdf_path.resolve()
        raw_dir_resolved = settings.RAW_DIR.resolve()
        if not str(resolved).startswith(str(raw_dir_resolved)):
            raise HTTPException(status_code=403, detail="访问被拒绝：路径越界")
    except Exception as e:
        logger.error(f"PDF 路径解析失败: {e}")
        raise HTTPException(status_code=500, detail="文件访问错误")

    logger.info(f"📄 提供 PDF 文件(内联): {pdf_path.name}")

    # Content-Disposition: inline 确保浏览器内联预览
    # 使用 RFC 5987 编码让中文文件名正确显示
    from urllib.parse import quote
    encoded_name = quote(pdf_path.name, safe="")
    disposition = f'inline; filename="{encoded_name}"; filename*=UTF-8\'\'{encoded_name}'

    return FileResponse(
        path=pdf_path,
        media_type="application/pdf",
        headers={"Content-Disposition": disposition},
    )
