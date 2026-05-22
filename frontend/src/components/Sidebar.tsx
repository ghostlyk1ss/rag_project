"use client";

import { useState, useEffect, useRef } from "react";
import {
  fetchSources,
  fetchConversations,
  fetchConversation,
  deleteConversation,
  deleteSource,
  uploadPdf,
  pollIngestStatus,
  type SourceDocument,
  type ConversationPreview,
} from "@/lib/api";
import {
  PanelLeftClose,
  FileText,
  TrendingUp,
  Newspaper,
  Scale,
  History,
  Search,
  BookOpen,
  ChevronDown,
  ChevronRight,
  Upload,
  Trash2,
  X,
  Loader2,
  CheckCircle2,
  AlertCircle,
  Plus,
  FolderOpen,
  MessageSquare,
  Ellipsis,
  Cloud,
  Shield,
} from "lucide-react";

interface SidebarProps {
  onSelectDoc?: (docId: string) => void;
  onSelectConversation?: (sessionId: string) => void;
  onNewChat?: () => void;
  onClose?: () => void;
  collapsed?: boolean;
  onUploadComplete?: () => void;
}

const DOC_TYPE_CONFIG: Record<string, { icon: React.ReactNode; label: string; color: string }> = {
  research: { icon: <TrendingUp className="w-3.5 h-3.5" />, label: "研报", color: "text-blue-600 bg-blue-50 border-blue-200" },
  report: { icon: <Newspaper className="w-3.5 h-3.5" />, label: "公告", color: "text-amber-600 bg-amber-50 border-amber-200" },
  financial: { icon: <FileText className="w-3.5 h-3.5" />, label: "财报", color: "text-emerald-600 bg-emerald-50 border-emerald-200" },
  legal: { icon: <Scale className="w-3.5 h-3.5" />, label: "法律", color: "text-violet-600 bg-violet-50 border-violet-200" },
};

function getDocTypeConfig(docType: string) {
  return DOC_TYPE_CONFIG[docType] || {
    icon: <FileText className="w-3.5 h-3.5" />,
    label: docType,
    color: "text-neutral-600 bg-neutral-50 border-neutral-200",
  };
}

function ConfirmModal({
  open, title, message, onConfirm, onCancel, danger,
}: {
  open: boolean; title: string; message: string;
  onConfirm: () => void; onCancel: () => void; danger?: boolean;
}) {
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-[100] flex items-center justify-center"
      onClick={onCancel}
    >
      <div className="absolute inset-0 bg-black/40 backdrop-blur-sm" />
      <div className="relative bg-white rounded-2xl shadow-2xl p-6 w-80 max-w-[90vw] border border-neutral-200 animate-in fade-in zoom-in duration-150"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center gap-3 mb-3">
          {danger
            ? <AlertCircle className="w-6 h-6 text-red-500" />
            : <AlertCircle className="w-6 h-6 text-amber-500" />
          }
          <h3 className="text-base font-semibold text-neutral-900">{title}</h3>
        </div>
        <p className="text-sm text-neutral-600 mb-5 leading-relaxed">{message}</p>
        <div className="flex gap-2 justify-end">
          <button onClick={onCancel}
            className="px-4 py-2 text-sm font-medium text-neutral-600 bg-neutral-100 hover:bg-neutral-200 rounded-xl transition-colors"
          >
            取消
          </button>
          <button onClick={onConfirm}
            className={`px-4 py-2 text-sm font-medium text-white rounded-xl transition-colors shadow-sm ${
              danger
                ? "bg-red-500 hover:bg-red-600"
                : "bg-primary-500 hover:bg-primary-600"
            }`}
          >
            确认{danger ? "删除" : ""}
          </button>
        </div>
      </div>
    </div>
  );
}

export default function Sidebar({
  onSelectDoc,
  onSelectConversation,
  onNewChat,
  onClose,
  collapsed,
  onUploadComplete,
}: SidebarProps) {
  const [documents, setDocuments] = useState<SourceDocument[]>([]);
  const [conversations, setConversations] = useState<ConversationPreview[]>([]);
  const [loadingDocs, setLoadingDocs] = useState(true);
  const [loadingConvs, setLoadingConvs] = useState(true);
  const [showDocs, setShowDocs] = useState(true);
  const [showConvs, setShowConvs] = useState(true);

  const [searchQuery, setSearchQuery] = useState("");
  const [recentDocIds, setRecentDocIds] = useState<string[]>([]);

  // Upload state
  const [uploading, setUploading] = useState(false);
  const [uploadProgress, setUploadProgress] = useState("");
  const [uploadProgressPercent, setUploadProgressPercent] = useState(0);
  const [uploadTaskId, setUploadTaskId] = useState<string | null>(null);
  const [uploadCompleted, setUploadCompleted] = useState(false);
  const [uploadError, setUploadError] = useState("");
  const [dragOver, setDragOver] = useState(false);
  const [collections, setCollections] = useState<{ pro: boolean; safe: boolean }>({ pro: true, safe: false });
  const [generateOutline, setGenerateOutline] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const dropZoneRef = useRef<HTMLDivElement>(null);

  // Confirm modal state
  const [confirmModal, setConfirmModal] = useState<{
    title: string; message: string; danger?: boolean;
    onConfirm: () => void;
  } | null>(null);

  // Native DOM drag-and-drop
  useEffect(() => {
    const el = dropZoneRef.current;
    if (!el) return;
    const onDragOver = (e: DragEvent) => { e.preventDefault(); e.stopPropagation(); setDragOver(true); };
    const onDragLeave = (e: DragEvent) => { e.preventDefault(); e.stopPropagation(); setDragOver(false); };
    const onDrop = (e: DragEvent) => {
      e.preventDefault(); e.stopPropagation(); setDragOver(false);
      const file = e.dataTransfer?.files?.[0];
      if (file) handleFileSelect(file);
    };
    el.addEventListener("dragenter", onDragOver);
    el.addEventListener("dragover", onDragOver);
    el.addEventListener("dragleave", onDragLeave);
    el.addEventListener("drop", onDrop);
    return () => {
      el.removeEventListener("dragenter", onDragOver);
      el.removeEventListener("dragover", onDragOver);
      el.removeEventListener("dragleave", onDragLeave);
      el.removeEventListener("drop", onDrop);
    };
  }, []);

  const loadData = () => {
    setLoadingDocs(true); setLoadingConvs(true);
    fetchSources().then(setDocuments).catch(() => setDocuments([])).finally(() => setLoadingDocs(false));
    fetchConversations().then(setConversations).catch(() => setConversations([])).finally(() => setLoadingConvs(false));
  };

  useEffect(() => { loadData(); }, []);

  // Poll upload status
  useEffect(() => {
    if (!uploadTaskId) return;
    let attempts = 0;
    const MAX_POLL_ATTEMPTS = 600;  // 15 min (1.5s * 600)
    const interval = setInterval(async () => {
      attempts++;
      if (attempts > MAX_POLL_ATTEMPTS) {
        clearInterval(interval);
        setUploadTaskId(null); setUploading(false); setUploadProgressPercent(0);
        setUploadProgress("处理超时，请重试");
        return;
      }
      try {
        const res = await fetch(`${process.env.NEXT_PUBLIC_API_URL || ""}/api/v1/ingest/${uploadTaskId}`);
        if (!res.ok) {
          clearInterval(interval);
          setUploadTaskId(null); setUploading(false); setUploadProgressPercent(0);
          setUploadProgress(`任务已丢失（后端可能重启）`);
          return;
        }
        const data = await res.json();
        setUploadProgress(data.message || "");
        setUploadProgressPercent(Math.round((data.progress || 0) * 100));
        if (data.status === "completed") {
          clearInterval(interval);
          setUploadTaskId(null); setUploading(false);
          setUploadProgressPercent(100); setUploadCompleted(true);
          loadData(); onUploadComplete?.();
        } else if (data.status === "error") {
          clearInterval(interval);
          setUploadTaskId(null); setUploading(false); setUploadProgressPercent(0);
          setUploadError(data.error || "处理失败");
        }
      } catch { /* retry */ }
    }, 1500);
    return () => clearInterval(interval);
  }, [uploadTaskId]);

  const handleFileSelect = async (file: File) => {
    const name = file.name.toLowerCase();
    const validExts = [".pdf", ".docx", ".pptx", ".xlsx", ".md", ".txt"];
    if (!validExts.some(e => name.endsWith(e))) {
      setUploadError("仅支持 PDF / Word / PPT / Excel / Markdown / TXT");
      return;
    }
    // Build collections string from selected targets
    const targetCollections = [];
    if (collections.pro) targetCollections.push("pro");
    if (collections.safe) targetCollections.push("safe");
    if (targetCollections.length === 0) {
      setUploadError("请至少选择一个目标库");
      return;
    }
    const collectionsStr = targetCollections.join(",");
    setUploading(true); setUploadCompleted(false); setUploadError("");
    setUploadProgressPercent(5); setUploadProgress("上传中...");
    try {
      const result = await uploadPdf(file, collectionsStr, generateOutline);
      setUploadTaskId(result.task_id); setUploadProgressPercent(20); setUploadProgress("解析中...");
    } catch (e: any) {
      setUploading(false); setUploadError(e.message || "上传失败");
    }
  };

  const handleDeleteConv = (e: React.MouseEvent, sessionId: string) => {
    e.stopPropagation();
    setConfirmModal({
      title: "删除对话",
      message: "确定删除此对话？删除后不可恢复。",
      danger: true,
      onConfirm: async () => {
        setConfirmModal(null);
        await deleteConversation(sessionId);
        loadData();
      },
    });
  };

  const handleDeleteDoc = (e: React.MouseEvent, docId: string, docTitle: string) => {
    e.stopPropagation();
    setConfirmModal({
      title: "删除文档",
      message: `确定删除文档「${docTitle}」？此操作不可撤销。`,
      danger: true,
      onConfirm: async () => {
        setConfirmModal(null);
        await deleteSource(docId);
        loadData();
      },
    });
  };

  const handleDocSelect = (doc: SourceDocument) => {
    setRecentDocIds((prev) => [doc.doc_id, ...prev.filter((id) => id !== doc.doc_id)].slice(0, 20));
    onSelectDoc?.(doc.doc_id);
  };

  const filteredDocs = documents
    .filter((doc) => {
      if (!searchQuery.trim()) return true;
      const q = searchQuery.toLowerCase();
      return (doc.title || "").toLowerCase().includes(q) || (doc.doc_id || "").toLowerCase().includes(q) ||
        (doc.company || "").toLowerCase().includes(q) || (doc.year || "").includes(q) ||
        (doc.doc_type || "").toLowerCase().includes(q);
    })
    .sort((a, b) => {
      const ai = recentDocIds.indexOf(a.doc_id), bi = recentDocIds.indexOf(b.doc_id);
      if (ai !== -1 && bi !== -1) return ai - bi;
      if (ai !== -1) return -1; if (bi !== -1) return 1;
      return 0;
    });

  if (collapsed) return null;

  return (
    <aside className="w-64 bg-white border-r border-neutral-200 flex flex-col h-full shrink-0">
      {/* Header */}
      <div className="flex items-center justify-between px-3 py-2 border-b border-neutral-100">
        <div className="flex items-center gap-2">
          <BookOpen className="w-4 h-4 text-primary-600" />
          <span className="text-sm font-semibold text-neutral-900 tracking-tight">知识库</span>
        </div>
        <div className="flex items-center gap-1.5">
          <span className="text-[10px] bg-neutral-100 text-neutral-500 px-1.5 py-0.5 rounded-full font-mono font-medium">
            {documents.length}
          </span>
          {onClose && (
            <button onClick={onClose} className="p-1 hover:bg-neutral-100 rounded-lg transition-colors" title="收起侧边栏">
              <PanelLeftClose className="w-3.5 h-3.5 text-neutral-400" />
            </button>
          )}
        </div>
      </div>

      <div className="flex-1 overflow-y-auto">
        {/* Upload area */}
        <div className="border-b border-neutral-100 p-3">
          <div
            ref={dropZoneRef}
            className={`rounded-xl p-3 text-center transition-all cursor-pointer border-2 border-dashed ${
              dragOver ? "border-primary-400 bg-primary-50 shadow-sm" :
              uploading ? "border-primary-300 bg-primary-50/50" :
              "border-neutral-200 hover:border-primary-300 hover:bg-neutral-50"
            }`}
            onClick={() => !uploading && fileInputRef.current?.click()}
          >
            <input ref={fileInputRef} type="file" accept=".pdf,.docx,.pptx,.xlsx,.md,.txt" className="hidden"
              onChange={(e) => { const f = e.target.files?.[0]; if (f) handleFileSelect(f); e.target.value = ""; }}
              disabled={uploading} />
            {uploading ? (
              <div className="flex flex-col items-center gap-2">
                <div className="w-full bg-neutral-200 rounded-full h-1.5 overflow-hidden">
                  <div className="bg-primary-500 h-full rounded-full transition-all duration-500 ease-out" style={{ width: `${uploadProgressPercent}%` }} />
                </div>
                <div className="flex items-center gap-1.5">
                  <Loader2 className="w-3 h-3 text-primary-500 animate-spin" />
                  <span className="text-xs text-neutral-500">{uploadProgress}</span>
                </div>
              </div>
            ) : uploadCompleted ? (
              <div className="flex flex-col items-center gap-1.5">
                <CheckCircle2 className="w-5 h-5 text-emerald-500" />
                <span className="text-xs text-emerald-600 font-medium">文档已就绪</span>
                <span className="text-[10px] text-neutral-400">{uploadProgress}</span>
                <button onClick={() => { setUploadCompleted(false); setUploadProgress(""); }}
                  className="text-[10px] text-neutral-400 underline hover:text-neutral-600 mt-0.5">
                  继续上传
                </button>
              </div>
            ) : (
              <div className="flex flex-col items-center gap-1">
                <Upload className="w-4 h-4 text-neutral-400" />
                <span className="text-xs text-neutral-400">拖拽或点击上传</span>
              </div>
            )}
          </div>
          <div className="mt-1 text-center text-[10px] text-neutral-400">
            支持 PDF · DOCX · PPTX · XLSX · MD · TXT
          </div>
            {uploadError && !uploading && (
              <div className="mt-1.5 text-xs text-red-500 text-center flex items-center justify-center gap-1">
                <AlertCircle className="w-3 h-3" />{uploadError}
                <button className="underline hover:text-red-700" onClick={() => setUploadError("")}>关闭</button>
              </div>
            )}
          {/* Target collection selector */}
          {!uploading && !uploadCompleted && (
            <>
              <div className="flex items-center justify-center gap-3 mt-2 text-[10px] text-neutral-500">
                <label className="flex items-center gap-1 cursor-pointer hover:text-blue-600 transition-colors">
                  <input type="checkbox" checked={collections.pro}
                    onChange={(e) => setCollections(prev => ({ ...prev, pro: e.target.checked }))}
                    className="w-3 h-3 rounded accent-blue-500" />
                  <Cloud className="w-3 h-3" />
                  <span>联网库</span>
                </label>
                <label className="flex items-center gap-1 cursor-pointer hover:text-emerald-600 transition-colors">
                  <input type="checkbox" checked={collections.safe}
                    onChange={(e) => setCollections(prev => ({ ...prev, safe: e.target.checked }))}
                    className="w-3 h-3 rounded accent-emerald-500" />
                  <Shield className="w-3 h-3" />
                  <span>安全库</span>
                </label>
              </div>
              <div className="flex items-center justify-center mt-1.5 text-[10px] text-neutral-400">
                <label className="flex items-center gap-1 cursor-pointer hover:text-amber-600 transition-colors group">
                  <input type="checkbox" checked={generateOutline}
                    onChange={(e) => setGenerateOutline(e.target.checked)}
                    className="w-3 h-3 rounded accent-amber-500" />
                  <FileText className="w-3 h-3" />
                  <span>生成结构纲要</span>
                  <span className="inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded bg-amber-50 text-amber-600 border border-amber-200 group-hover:bg-amber-100 transition-colors"
                    title="AI 逐章节生成概要索引，大幅提升宏观总结类问题的回答质量。会调用 DeepSeek API，花费约 ¥0.01~¥1.10/份文档。">
                    ⓘ 约 ¥0.01~1.10
                  </span>
                </label>
              </div>
            </>
          )}
        </div>

        {/* Document search */}
        <div className="border-b border-neutral-100 px-3 py-2">
          <div className="flex items-center gap-2 bg-neutral-50 border border-neutral-200 rounded-lg px-2.5 py-1.5 focus-within:border-primary-400 focus-within:ring-2 focus-within:ring-primary-100/60 transition-all">
            <Search className="w-3.5 h-3.5 text-neutral-400 shrink-0" />
            <input className="flex-1 bg-transparent text-xs text-neutral-900 placeholder:text-neutral-400 outline-none"
              placeholder="搜索文档..." value={searchQuery} onChange={(e) => setSearchQuery(e.target.value)} />
            {searchQuery && (
              <button onClick={() => setSearchQuery("")} className="p-0.5 hover:bg-neutral-200 rounded-full transition-colors">
                <X className="w-3 h-3 text-neutral-400" />
              </button>
            )}
          </div>
        </div>

        {/* Documents section */}
        <div className="border-b border-neutral-100">
          <button
            className="flex items-center justify-between w-full px-3 py-2.5 text-sm text-neutral-500 hover:bg-neutral-50 transition-colors"
            onClick={() => setShowDocs(!showDocs)}
          >
            <div className="flex items-center gap-2">
              <FileText className="w-3.5 h-3.5 text-neutral-400" />
              <span className="font-medium text-neutral-800">文档</span>
            </div>
            <div className="flex items-center gap-1">
              <span className="text-[10px] text-neutral-400 font-mono">
                {searchQuery ? `${filteredDocs.length}/${documents.length}` : documents.length}
              </span>
              {showDocs ? <ChevronDown className="w-3.5 h-3.5 text-neutral-400" /> : <ChevronRight className="w-3.5 h-3.5 text-neutral-400" />}
            </div>
          </button>
          {showDocs && (
            <div className="pb-1">
              {loadingDocs ? (
                <div className="px-3 py-3 text-xs text-neutral-400 flex items-center gap-2">
                  <Loader2 className="w-3 h-3 animate-spin" />加载中...
                </div>
              ) : filteredDocs.length === 0 ? (
                <div className="px-3 py-3 text-xs text-neutral-400 text-center">
                  {searchQuery ? "未找到匹配的文档" : "暂无文档，请上传 PDF"}
                </div>
              ) : (
                filteredDocs.map((doc) => {
                  const cfg = getDocTypeConfig(doc.doc_type);
                  return (
                    <div key={doc.doc_id}
                      className="sidebar-item group"
                      onClick={() => handleDocSelect(doc)}
                    >
                      <div className={`p-1 rounded-md border ${cfg.color} shrink-0`}>
                        {cfg.icon}
                      </div>
                      <div className="flex-1 min-w-0">
                        <div className="truncate text-xs font-medium text-neutral-800">{doc.title}</div>
                        <div className="flex flex-wrap items-center gap-1 text-[10px] text-neutral-400 mt-0.5">
                          <span>{doc.year}</span>
                          <span className="px-1 py-0.5 bg-neutral-100 rounded text-neutral-500">{cfg.label}</span>
                          {doc.modes && doc.modes.includes('pro') && (
                            <span className="px-1 py-0.5 bg-blue-50 text-blue-600 rounded font-medium">Pro</span>
                          )}
                          {doc.modes && doc.modes.includes('safe') && (
                            <span className="px-1 py-0.5 bg-emerald-50 text-emerald-600 rounded font-medium">Safe</span>
                          )}
                          {doc.has_outline && (
                            <span className="px-1 py-0.5 bg-amber-50 text-amber-600 rounded font-medium" title="已生成结构纲要">纲要</span>
                          )}
                        </div>
                      </div>
                      <button onClick={(e) => handleDeleteDoc(e, doc.doc_id, doc.title)}
                        className="p-1.5 text-neutral-300 hover:text-red-500 hover:bg-red-50 rounded-lg transition-all shrink-0"
                        title="删除文档">
                        <Trash2 className="w-3.5 h-3.5" />
                      </button>
                    </div>
                  );
                })
              )}
            </div>
          )}
        </div>

        {/* Conversation history */}
        <div>
          <button
            className="flex items-center justify-between w-full px-3 py-2.5 text-sm text-neutral-500 hover:bg-neutral-50 transition-colors"
            onClick={() => setShowConvs(!showConvs)}
          >
            <div className="flex items-center gap-2">
              <History className="w-3.5 h-3.5 text-neutral-400" />
              <span className="font-medium text-neutral-800">历史</span>
            </div>
            <div className="flex items-center gap-1">
              <span className="text-[10px] text-neutral-400 font-mono">{conversations.length}</span>
              {showConvs ? <ChevronDown className="w-3.5 h-3.5 text-neutral-400" /> : <ChevronRight className="w-3.5 h-3.5 text-neutral-400" />}
            </div>
          </button>
          {showConvs && (
            <div className="pb-1">
              {loadingConvs ? (
                <div className="px-3 py-3 text-xs text-neutral-400 flex items-center gap-2">
                  <Loader2 className="w-3 h-3 animate-spin" />加载中...
                </div>
              ) : conversations.length === 0 ? (
                <div className="px-3 py-3 text-xs text-neutral-400 text-center">暂无历史对话</div>
              ) : (
                conversations.map((conv) => (
                  <div key={conv.session_id}
                    className="sidebar-item group"
                    onClick={() => onSelectConversation?.(conv.session_id)}
                  >
                    <MessageSquare className="w-3.5 h-3.5 text-neutral-400 shrink-0" />
                    <div className="flex-1 min-w-0">
                      <div className="truncate text-xs font-medium text-neutral-800">{conv.title}</div>
                      <div className="text-[10px] text-neutral-400 mt-0.5">{conv.message_count} 条消息</div>
                    </div>
                    <button onClick={(e) => handleDeleteConv(e, conv.session_id)}
                      className="p-1.5 text-neutral-300 hover:text-red-500 hover:bg-red-50 rounded-lg transition-all shrink-0"
                      title="删除对话">
                      <Trash2 className="w-3.5 h-3.5" />
                    </button>
                  </div>
                ))
              )}
            </div>
          )}
        </div>
      </div>

      {/* Confirm Modal */}
      <ConfirmModal
        open={confirmModal !== null}
        title={confirmModal?.title || ""}
        message={confirmModal?.message || ""}
        danger={confirmModal?.danger}
        onConfirm={() => confirmModal?.onConfirm?.()}
        onCancel={() => setConfirmModal(null)}
      />

      {/* New chat button */}
      <div className="border-t border-neutral-100 p-3">
        <button
          onClick={onNewChat}
          className="btn-primary w-full flex items-center justify-center gap-1.5 px-3 py-2 text-xs"
        >
          <Plus className="w-3.5 h-3.5" />
          <span>新对话</span>
        </button>
      </div>
    </aside>
  );
}
