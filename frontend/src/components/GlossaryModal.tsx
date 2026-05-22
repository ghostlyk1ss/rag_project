"use client";

import { useState, useEffect, useCallback } from "react";
import { X, Search, BookOpen, Trash2, Loader2, AlertCircle } from "lucide-react";
import { queryGlossary, queryGlossaryConfirm, listGlossaryCache, clearGlossaryCache } from "@/lib/api";

interface GlossaryModalProps {
  onClose: () => void;
}

export default function GlossaryModal({ onClose }: GlossaryModalProps) {
  const [term, setTerm] = useState("");
  const [result, setResult] = useState<{ definition: string; cached?: boolean } | null>(null);
  const [history, setHistory] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showConfirm, setShowConfirm] = useState(false);  // 本地模型不可用时弹出确认

  // 加载历史
  useEffect(() => {
    listGlossaryCache().then(setHistory).catch(() => {});
  }, []);

  const handleSearch = useCallback(async () => {
    const q = term.trim();
    if (!q) return;
    setLoading(true);
    setError(null);
    setResult(null);
    setShowConfirm(false);

    try {
      const resp = await queryGlossary(q);
      if (resp.fallback) {
        // 本地模型不可用，弹出确认对话框
        setShowConfirm(true);
        setLoading(false);
        return;
      }
      setResult({ definition: resp.definition || "", cached: resp.cached });
      if (!history.includes(q)) setHistory((prev) => [q, ...prev]);
    } catch (e: any) {
      setError(e.message || "查询失败");
    }
    setLoading(false);
  }, [term, history]);

  const handleConfirmRemote = useCallback(async () => {
    const q = term.trim();
    if (!q) return;
    setLoading(true);
    setError(null);
    setShowConfirm(false);

    try {
      const resp = await queryGlossaryConfirm(q);
      setResult({ definition: resp.definition, cached: resp.cached });
      if (!history.includes(q)) setHistory((prev) => [q, ...prev]);
    } catch (e: any) {
      setError(e.message || "查询失败");
    }
    setLoading(false);
  }, [term, history]);

  const handleClear = useCallback(async () => {
    await clearGlossaryCache();
    setHistory([]);
    setResult(null);
  }, []);

  const handleHistoryClick = useCallback((t: string) => {
    setTerm(t);
    // 自动查询历史条目
    (async () => {
      setLoading(true);
      setError(null);
      setResult(null);
      try {
        const resp = await queryGlossary(t);
        if (resp.fallback) {
          setShowConfirm(true);
          setLoading(false);
          return;
        }
        setResult({ definition: resp.definition || "", cached: resp.cached });
      } catch (e: any) {
        setError(e.message || "查询失败");
      }
      setLoading(false);
    })();
  }, []);

  const handleKeyDown = useCallback((e: React.KeyboardEvent) => {
    if (e.key === "Enter") handleSearch();
    if (e.key === "Escape") onClose();
  }, [handleSearch, onClose]);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 backdrop-blur-sm">
      <div
        className="bg-white rounded-2xl shadow-2xl w-full max-w-lg max-h-[75vh] flex flex-col mx-4 animate-fade-in-up"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-3.5 border-b border-neutral-100">
          <div className="flex items-center gap-2.5">
            <div className="w-8 h-8 rounded-xl bg-amber-50 flex items-center justify-center">
              <BookOpen className="w-4 h-4 text-amber-600" />
            </div>
            <span className="text-sm font-semibold text-[#1f2937]">术语速查</span>
          </div>
          <button onClick={onClose} className="p-1.5 hover:bg-neutral-100 rounded-lg transition-colors">
            <X className="w-4 h-4 text-[#9ca3af]" />
          </button>
        </div>

        {/* Search Bar */}
        <div className="px-5 py-3">
          <div className="flex gap-2">
            <div className="flex-1 relative">
              <input
                type="text"
                value={term}
                onChange={(e) => setTerm(e.target.value)}
                onKeyDown={handleKeyDown}
                placeholder="输入金融术语，如 ROE、PB、杜邦分析…"
                className="input-box w-full pr-3 pl-3 py-2 text-sm"
                autoFocus
              />
            </div>
            <button
              onClick={handleSearch}
              disabled={loading || !term.trim()}
              className="btn-primary flex items-center gap-1.5 px-3.5 py-2 text-xs disabled:opacity-50"
            >
              {loading ? (
                <Loader2 className="w-3.5 h-3.5 animate-spin" />
              ) : (
                <Search className="w-3.5 h-3.5" />
              )}
              <span>查询</span>
            </button>
          </div>
        </div>

        {/* Confirm Dialog (local model unavailable) */}
        {showConfirm && (
          <div className="mx-5 mb-3 p-3 rounded-xl bg-amber-50 border border-amber-200">
            <div className="flex items-start gap-2.5">
              <AlertCircle className="w-4 h-4 text-amber-600 mt-0.5 shrink-0" />
              <div>
                <p className="text-xs text-amber-800 font-medium mb-2">
                  当前没有部署本地模型，术语速查会调用API消耗token，是否仍然询问？
                </p>
                <div className="flex gap-2">
                  <button
                    onClick={handleConfirmRemote}
                    className="px-3 py-1.5 text-xs font-medium bg-amber-600 text-white rounded-lg hover:bg-amber-700 transition-colors"
                  >
                    仍然询问
                  </button>
                  <button
                    onClick={() => setShowConfirm(false)}
                    className="px-3 py-1.5 text-xs font-medium text-amber-700 bg-amber-100 rounded-lg hover:bg-amber-200 transition-colors"
                  >
                    取消
                  </button>
                </div>
              </div>
            </div>
          </div>
        )}

        {/* Result */}
        <div className="flex-1 overflow-y-auto px-5 pb-3 min-h-0">
          {error && (
            <div className="p-3 rounded-xl bg-red-50 border border-red-200">
              <p className="text-xs text-red-600">{error}</p>
            </div>
          )}

          {result && (
            <div className="p-4 rounded-xl bg-neutral-50 border border-neutral-100 mb-3">
              {result.cached && (
                <span className="inline-block px-2 py-0.5 mb-2 text-[10px] font-medium text-emerald-700 bg-emerald-100 rounded-full">
                  缓存结果
                </span>
              )}
              <div className="text-xs text-[#374151] leading-relaxed whitespace-pre-wrap font-[system-ui] glossary-content">
                {result.definition}
              </div>
            </div>
          )}

          {loading && !showConfirm && (
            <div className="flex items-center justify-center py-8">
              <Loader2 className="w-5 h-5 text-primary-500 animate-spin" />
              <span className="ml-2 text-xs text-[#6b7280]">正在查询…</span>
            </div>
          )}

          {/* History */}
          {history.length > 0 && !result && !loading && (
            <div>
              <div className="flex items-center justify-between mb-2">
                <span className="text-[11px] text-[#9ca3af] font-medium">历史查询</span>
                <button
                  onClick={handleClear}
                  className="flex items-center gap-1 text-[10px] text-red-400 hover:text-red-500 transition-colors"
                >
                  <Trash2 className="w-3 h-3" />
                  <span>清空缓存</span>
                </button>
              </div>
              <div className="flex flex-wrap gap-1.5">
                {history.map((t) => (
                  <button
                    key={t}
                    onClick={() => handleHistoryClick(t)}
                    className="px-2.5 py-1 text-[11px] text-primary-700 bg-primary-50 rounded-lg hover:bg-primary-100 transition-colors"
                  >
                    {t}
                  </button>
                ))}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
