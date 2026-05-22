"use client";

import { useState, useEffect, useMemo } from "react";
import { X, FileText, AlertCircle, FileSpreadsheet, File, Download } from "lucide-react";

interface DocViewerProps {
  docId: string | null;
  title?: string;
  onClose?: () => void;
  page?: number;
}

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "";

const EXT_ICONS: Record<string, { icon: React.ReactNode; label: string; color: string }> = {
  pdf:  { icon: <FileText className="w-4 h-4" />, label: "PDF",  color: "text-red-600 bg-red-50" },
  docx: { icon: <FileText className="w-4 h-4" />, label: "Word", color: "text-blue-600 bg-blue-50" },
  pptx: { icon: <FileText className="w-4 h-4" />, label: "PPT",  color: "text-amber-600 bg-amber-50" },
  xlsx: { icon: <FileSpreadsheet className="w-4 h-4" />, label: "Excel", color: "text-emerald-600 bg-emerald-50" },
};

function getExt(docId: string): string {
  const m = docId.match(/\.(\w+)$/);
  return m ? m[1].toLowerCase() : "";
}

export default function DocViewer({ docId, title, onClose, page }: DocViewerProps) {
  const [error, setError] = useState<string | null>(null);
  const [isPdf, setIsPdf] = useState<boolean | null>(null); // null = loading

  useEffect(() => {
    setError(null);
    setIsPdf(null);
    if (!docId) return;
    // 通过 HEAD 请求检测是否为 PDF
    const url = `${API_BASE}/api/v1/doc/${encodeURIComponent(docId)}`;
    fetch(url, { method: "HEAD" })
      .then((r) => {
        if (!r.ok) throw new Error("文档不存在");
        const ct = r.headers.get("content-type") || "";
        const cd = r.headers.get("content-disposition") || "";
        setIsPdf(ct.includes("application/pdf") || cd.includes(".pdf"));
      })
      .catch(() => setIsPdf(false));
  }, [docId]);

  if (!docId) return null;

  const docUrl = `${API_BASE}/api/v1/doc/${encodeURIComponent(docId)}`;
  const downloadUrl = `${docUrl}/raw`;
  const urlWithPage = page ? `${docUrl}#page=${page}` : docUrl;

  // ── 加载中 ──
  if (isPdf === null) {
    return (
      <div className="h-full flex items-center justify-center bg-white">
        <div className="text-sm text-neutral-400">加载中...</div>
      </div>
    );
  }

  // ── PDF: iframe 内联（含页码跳转） ──
  if (isPdf) {
    return (
      <div className="h-full flex flex-col bg-white">
        <div className="flex items-center justify-between px-3 py-2 border-b border-neutral-200 bg-white shrink-0">
          <div className="flex items-center gap-2 min-w-0 flex-1">
            <div className="p-1.5 rounded-lg text-red-600 bg-red-50"><FileText className="w-4 h-4" /></div>
            <span className="text-xs font-semibold text-[#1f2937] truncate">{title || docId}</span>
          </div>
          <div className="flex items-center gap-1">
            {onClose && (
              <button onClick={onClose} className="p-1.5 hover:bg-gray-100 rounded-lg transition-colors" title="关闭">
                <X className="w-4 h-4 text-[#9ca3af]" />
              </button>
            )}
          </div>
        </div>
        <div className="flex-1 bg-neutral-50 overflow-hidden">
          {error ? (
            <div className="flex flex-col items-center justify-center h-full text-[#6b7280]">
              <AlertCircle className="w-8 h-8 text-red-400 mb-2" />
              <span className="text-sm text-red-500">{error}</span>
            </div>
          ) : (
            <iframe
              src={urlWithPage}
              className="w-full h-full"
              onError={() => setError("无法加载 PDF 文件")}
              title={docId}
            />
          )}
        </div>
        <div className="flex items-center justify-center px-3 py-1.5 bg-neutral-50 text-[#9ca3af] text-[10px] border-t border-neutral-200 shrink-0">
          浏览器原生 PDF 预览 · 支持搜索/翻页/缩放
        </div>
      </div>
    );
  }

  // ── 非 PDF: 使用后端渲染的 HTML 预览 ──
  const ext = getExt(docId);
  const extConfig = EXT_ICONS[ext] || { icon: <File className="w-4 h-4" />, label: ext.toUpperCase() || "文件", color: "text-gray-600 bg-gray-50" };
  return (
    <div className="h-full flex flex-col bg-white">
      <div className="flex items-center justify-between px-3 py-2 border-b border-neutral-200 bg-white shrink-0">
        <div className="flex items-center gap-2 min-w-0 flex-1">
          <div className={`p-1.5 rounded-lg ${extConfig.color}`}>{extConfig.icon}</div>
          <span className="text-xs font-semibold text-[#1f2937] truncate">{title || docId}</span>
        </div>
        <div className="flex items-center gap-1">
          <a
            href={downloadUrl}
            download
            className="flex items-center gap-1 px-2.5 py-1.5 text-xs font-medium btn-primary"
          >
            <Download className="w-3 h-3" />
            <span>下载</span>
          </a>
          {onClose && (
            <button onClick={onClose} className="p-1.5 hover:bg-gray-100 rounded-lg transition-colors" title="关闭">
              <X className="w-4 h-4 text-[#9ca3af]" />
            </button>
          )}
        </div>
      </div>
      <div className="flex-1 bg-[#f9fafb] overflow-hidden">
        {error ? (
          <div className="flex flex-col items-center justify-center h-full text-[#6b7280]">
            <AlertCircle className="w-8 h-8 text-red-400 mb-2" />
            <span className="text-sm text-red-500">{error}</span>
          </div>
        ) : (
          <iframe
            src={docUrl}
            className="w-full h-full"
            onError={() => setError("无法加载文档预览")}
            title={docId}
          />
        )}
      </div>
      <div className="flex items-center justify-center px-3 py-1.5 bg-neutral-50 text-[#9ca3af] text-[10px] border-t border-neutral-200 shrink-0">
        {extConfig.label} 文档解析预览 · 下载原始文件可查看完整格式
      </div>
    </div>
  );
}
