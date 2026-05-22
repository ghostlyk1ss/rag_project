"""
finRAG API — 文档服务路由
===========================
GET /api/v1/doc/{doc_id}         — 智能路由：PDF 返回二进制，其他返回解析文本预览
GET /api/v1/doc/{doc_id}/preview — 返回解析后的 Markdown 文本（用于非 PDF 预览）
GET /api/v1/doc/{doc_id}/raw     — 返回原始文件（下载）
"""
import json
import logging
import re
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, Response

from backend.config import settings

logger = logging.getLogger("api.doc")
router = APIRouter(prefix="/api/v1", tags=["Doc"])

# ── 支持的文件类型 ──────────────────────────────────────────
SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".pptx", ".xlsx", ".md"}


def _resolve_doc(doc_id: str) -> tuple[Path, str]:
    """查找文档文件，返回 (path, extension)。

    按优先级查找：
      1.  doc_id 自带扩展名 → 直接查找
      2.  无扩展名 → 依次尝试 .pdf, .docx, .pptx, .xlsx
    """
    # 安全验证：只阻止路径遍历，不限制文件名中的合法字符
    if ".." in [p for p in Path(doc_id).parts]:
        raise HTTPException(status_code=400, detail="禁止路径遍历")
    if not re.match(r'^[\w\u4e00-\u9fff\-\. +～·—–«»""''「」【】《》、，,：:（）()！!？?；;]+$', doc_id):
        raise HTTPException(status_code=400, detail="doc_id 包含非法字符")

    raw_dir = settings.RAW_DIR
    stem = Path(doc_id).stem
    ext = Path(doc_id).suffix.lower()

    if ext in SUPPORTED_EXTENSIONS:
        path = raw_dir / doc_id
        if path.exists() and path.is_file():
            resolved = path.resolve()
            raw_resolved = raw_dir.resolve()
            if str(resolved).startswith(str(raw_resolved)):
                return path, ext
            raise HTTPException(status_code=403, detail="路径越界")

    # 尝试各种扩展名
    for try_ext in [".pdf", ".docx", ".pptx", ".xlsx"]:
        path = raw_dir / f"{stem}{try_ext}"
        if path.exists() and path.is_file():
            resolved = path.resolve()
            raw_resolved = raw_dir.resolve()
            if str(resolved).startswith(str(raw_resolved)):
                return path, try_ext
            raise HTTPException(status_code=403, detail="路径越界")

    raise HTTPException(status_code=404, detail=f"文档 {doc_id} 未找到")


def _get_parsed_md(doc_id: str) -> str | None:
    """获取解析后的 Markdown 文本。"""
    stem = Path(doc_id).stem
    md_path = settings.PARSED_DIR / f"{stem}.md"
    if md_path.exists():
        try:
            return md_path.read_text(encoding="utf-8")
        except Exception as e:
            logger.warning(f"读取解析文件失败: {md_path} - {e}")
    return None


@router.get("/doc/{doc_id}")
async def serve_doc(doc_id: str):
    """智能文档服务。

    根据文件类型自动选择展示方式：
      - .pdf  → 内联 PDF 预览
      - 其他  → 返回解析后的 Markdown 文本（HTML 渲染）
    """
    path, ext = _resolve_doc(doc_id)
    stem = Path(doc_id).stem

    # ── PDF → 内联二进制 ──
    if ext == ".pdf":
        encoded_name = quote(path.name, safe="")
        disposition = f'inline; filename="{encoded_name}"; filename*=UTF-8\'\'{encoded_name}'
        return FileResponse(
            path=path,
            media_type="application/pdf",
            headers={"Content-Disposition": disposition},
        )

    # ── 非 PDF：检查有无解析文本，优先展示解析预览 ──
    md_content = _get_parsed_md(doc_id)
    if md_content is None:
        # 没有解析文本，提供原始文件下载
        encoded_name = quote(path.name, safe="")
        disposition = f'attachment; filename="{encoded_name}"; filename*=UTF-8\'\'{encoded_name}'
        return FileResponse(
            path=path,
            media_type="application/octet-stream",
            headers={"Content-Disposition": disposition},
        )

    file_size_kb = path.stat().st_size / 1024
    mime_map = {".docx": "Word", ".pptx": "PPT", ".xlsx": "Excel"}
    file_type = mime_map.get(ext, ext.upper().lstrip("."))

    # 构建信息头 + Markdown 内容的 HTML
    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{stem} - 文档预览</title>
<style>
  * {{ margin:0; padding:0; box-sizing:border-box; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Noto Sans SC", sans-serif;
         background: #f9fafb; color: #374151; font-size: 14px; line-height: 1.7; }}
  .header {{ background: white; border-bottom: 1px solid #e5e7eb; padding: 12px 24px;
             display: flex; align-items: center; justify-content: space-between; }}
  .header-left {{ display: flex; align-items: center; gap: 10px; }}
  .file-badge {{ display: inline-flex; align-items: center; gap: 4px; padding: 3px 10px;
                 border-radius: 6px; font-size: 12px; font-weight: 600; }}
  .badge-docx {{ background: #dbeafe; color: #1d4ed8; }}
  .badge-pptx {{ background: #fef3c7; color: #d97706; }}
  .badge-xlsx {{ background: #d1fae5; color: #059669; }}
  .badge-pdf {{ background: #fee2e2; color: #dc2626; }}
  .download-btn {{ display: inline-flex; align-items: center; gap: 6px; padding: 6px 14px;
                   background: linear-gradient(135deg,#6366f1,#8b5cf6); color: white;
                   border-radius: 10px; font-size: 13px; font-weight: 500;
                   text-decoration: none; transition: all .2s; }}
  .download-btn:hover {{ opacity: .9; transform: translateY(-1px); }}
  .meta {{ padding: 10px 24px; background: white; border-bottom: 1px solid #e5e7eb;
           display: flex; gap: 20px; font-size: 12px; color: #6b7280; }}
  .content {{ max-width: 800px; margin: 24px auto; padding: 24px 32px;
              background: white; border-radius: 12px; box-shadow: 0 1px 3px rgba(0,0,0,.06); }}
  .content h1, .content h2, .content h3 {{ color: #1f2937; margin-top: 1.2em; margin-bottom: .5em; }}
  .content h1 {{ font-size: 1.4em; }}
  .content h2 {{ font-size: 1.2em; }}
  .content p {{ margin-bottom: .8em; }}
  .content table {{ border-collapse: collapse; width: 100%; margin: 12px 0; font-size: 13px; }}
  .content th, .content td {{ border: 1px solid #e5e7eb; padding: 8px 12px; text-align: left; }}
  .content th {{ background: #f9fafb; font-weight: 600; }}
  .content code {{ background: #f3f4f6; padding: 1px 5px; border-radius: 4px; font-size: 12px; }}
  .content ul, .content ol {{ padding-left: 20px; margin-bottom: .8em; }}
  .spinner {{ display: flex; align-items: center; justify-content: center; height: 60vh; }}
</style>
</head>
<body>
<div class="header">
  <div class="header-left">
    <span class="file-badge badge-{ext.lstrip('.')}">📄 {file_type}</span>
    <span style="font-weight:600;color:#1f2937">{stem}</span>
  </div>
  <a class="download-btn" href="/api/v1/doc/{quote(doc_id, safe='')}/raw" download>
    ⬇ 下载原始文件
  </a>
</div>
<div class="meta">
  <span>类型: {file_type}</span>
  <span>大小: {file_size_kb:.1f} KB</span>
  <span>格式: {ext.upper().lstrip('.')}</span>
</div>
<div class="content">
  {_md_to_html(md_content)}
</div>
</body></html>"""
    return HTMLResponse(content=html)


@router.get("/doc/{doc_id}/preview")
async def doc_preview(doc_id: str):
    """返回解析后的 Markdown 文本（纯文本格式）。"""
    md_content = _get_parsed_md(doc_id)
    if md_content is None:
        raise HTTPException(status_code=404, detail="没有可用的预览内容")
    return Response(content=md_content, media_type="text/plain; charset=utf-8")


@router.get("/doc/{doc_id}/raw")
async def doc_raw(doc_id: str):
    """返回原始文件（下载）。"""
    path, ext = _resolve_doc(doc_id)
    encoded_name = quote(path.name, safe="")
    disposition = f'attachment; filename="{encoded_name}"; filename*=UTF-8\'\'{encoded_name}'
    mime_map = {".pdf": "application/pdf", ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
                ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}
    media_type = mime_map.get(ext, "application/octet-stream")
    return FileResponse(
        path=path,
        media_type=media_type,
        headers={"Content-Disposition": disposition},
    )


def _md_to_html(md: str) -> str:
    """简单的 Markdown → HTML 转换（不依赖外部库）。

    只支持基础语法：标题、段落、列表、表格、代码、粗体、行内代码。
    """
    import html as html_mod
    import re as re_mod
    lines = md.split("\n")
    html_parts = []
    in_table = False
    in_code = False
    code_buf = []

    for line in lines:
        # 代码块
        if line.strip().startswith("```"):
            if in_code:
                escaped_code = html_mod.escape("\n".join(code_buf))
                html_parts.append(f"<pre><code>{escaped_code}</code></pre>")
                code_buf = []
                in_code = False
            else:
                in_code = True
            continue
        if in_code:
            code_buf.append(line)
            continue

        stripped = line.strip()

        # 跳过表格分隔行
        if stripped.startswith("|---") or stripped.startswith("|--"):
            continue

        # 表格行
        if stripped.startswith("|") and stripped.endswith("|"):
            cells = [c.strip() for c in stripped.strip("|").split("|")]
            if not in_table:
                html_parts.append("<table><thead><tr>" +
                                  "".join(f"<th>{html_mod.escape(c)}</th>" for c in cells) +
                                  "</tr></thead><tbody>")
                in_table = True
            else:
                html_parts.append("<tr>" +
                                  "".join(f"<td>{html_mod.escape(c)}</td>" for c in cells) +
                                  "</tr>")
            continue
        if in_table:
            html_parts.append("</tbody></table>")
            in_table = False

        # 标题
        if stripped.startswith("### "):
            html_parts.append(f"<h3>{html_mod.escape(stripped[4:])}</h3>")
        elif stripped.startswith("## "):
            html_parts.append(f"<h2>{html_mod.escape(stripped[3:])}</h2>")
        elif stripped.startswith("# "):
            html_parts.append(f"<h1>{html_mod.escape(stripped[2:])}</h1>")

        # 水平线
        elif stripped in ("---", "***", "___"):
            html_parts.append("<hr>")

        # 列表项
        elif stripped.startswith("- ") or stripped.startswith("* "):
            html_parts.append(f"<li>{html_mod.escape(stripped[2:])}</li>")
        elif stripped.startswith("1. ") or stripped.startswith("2. "):
            html_parts.append(f"<li>{html_mod.escape(stripped[3:])}</li>")

        # 段落
        elif stripped:
            # 行内代码
            text = html_mod.escape(stripped)
            text = re_mod.sub(r'`([^`]+)`', r'<code>\1</code>', text)
            text = re_mod.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', text)
            html_parts.append(f"<p>{text}</p>")

    if in_code:
        escaped_code = html_mod.escape("\n".join(code_buf))
        html_parts.append(f"<pre><code>{escaped_code}</code></pre>")
    if in_table:
        html_parts.append("</tbody></table>")

    return "\n".join(html_parts)


# 保留旧端点兼容
@router.get("/pdf/{doc_id}")
async def serve_pdf_legacy(doc_id: str):
    """兼容旧版 PDF 端点（重定向到新版）。"""
    return await serve_doc(doc_id)
