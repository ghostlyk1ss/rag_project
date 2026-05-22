"use client";

import { useState, useCallback, useRef, useEffect } from "react";
import dynamic from "next/dynamic";
import ChatBox from "@/components/ChatBox";
import Sidebar from "@/components/Sidebar";
import MetricsPanel from "@/components/MetricsPanel";
import { fetchConversation, fetchSources, type MessageData } from "@/lib/api";
import { BarChart3, PanelLeft, Sparkles } from "lucide-react";

const DocViewer = dynamic(() => import("@/components/DocViewer"), { ssr: false });

export default function ClientPage() {
  const [showSidebar, setShowSidebar] = useState(true);
  const [showPdf, setShowPdf] = useState(false);
  const [selectedDocId, setSelectedDocId] = useState<string | null>(null);
  const [selectedDocTitle, setSelectedDocTitle] = useState<string>("");

  const [currentSessionId, setCurrentSessionId] = useState<string | undefined>();
  const [loadedMessages, setLoadedMessages] = useState<MessageData[] | undefined>();
  const refreshTriggerRef = useRef(0);
  const [selectedPage, setSelectedPage] = useState<number | undefined>();
  const [showMetrics, setShowMetrics] = useState(false);
  const [docCount, setDocCount] = useState(0);

  // Load doc count for status bar
  useEffect(() => {
    fetchSources().then(s => setDocCount(s.length)).catch(() => {});
  }, []);

  const handleOpenPdf = (docId: string, page?: number) => {
    setSelectedDocId(docId);
    setSelectedPage(page);
    setShowPdf(true);
  };

  const handleToggleSidebar = () => setShowSidebar((prev) => !prev);
  const handleTogglePdf = () => setShowPdf((prev) => !prev);
  const handleClosePdf = () => {
    setShowPdf(false);
    setSelectedDocId(null);
    setSelectedDocTitle("");
  };

  const handleSelectConversation = useCallback(async (sessionId: string) => {
    try {
      const detail = await fetchConversation(sessionId);
      setCurrentSessionId(detail.session_id);
      setLoadedMessages(detail.messages);
    } catch {
      setCurrentSessionId(sessionId);
      setLoadedMessages([]);
    }
  }, []);

  const handleNewChat = useCallback(() => {
    setCurrentSessionId(undefined);
    setLoadedMessages(undefined);
  }, []);

  const handleSessionCreated = useCallback((sessionId: string) => {
    setCurrentSessionId(sessionId);
    setLoadedMessages(undefined);
  }, []);

  const handleMessageSent = useCallback(() => {
    refreshTriggerRef.current += 1;
  }, []);

  const handleUploadComplete = useCallback(() => {
    refreshTriggerRef.current += 1;
    fetchSources().then(s => setDocCount(s.length)).catch(() => {});
  }, []);

  return (
    <div className="h-screen flex flex-col overflow-hidden">
      {/* Top status bar */}
      <div className="flex items-center justify-between px-4 py-1 bg-[#f9fafb] border-b border-gray-200/60 shrink-0">
        <div className="flex items-center gap-3">
          <div className="flex items-center gap-1.5">
            <div className="w-2 h-2 rounded-full bg-emerald-500 shadow-sm shadow-emerald-300" />
            <span className="text-xs text-[#6b7280] font-medium">finRAG v2.0</span>
          </div>
          <span className="text-[#d1d5db]">|</span>
          <span className="text-xs text-[#9ca3af]">
            {docCount} 份文档已加载
          </span>
          <span className="text-[#d1d5db]">·</span>
          <span className="text-xs text-[#9ca3af]">
            模型: DeepSeek V4 / Ollama
          </span>
        </div>
        <div className="flex items-center gap-1">
          <span className="text-[10px] text-[#9ca3af] font-mono tracking-wide">STATUS · OK</span>
        </div>
      </div>

      <div className="flex flex-1 overflow-hidden">
        {/* Sidebar */}
        {showSidebar && (
          <Sidebar
            onSelectDoc={handleOpenPdf}
            onSelectConversation={handleSelectConversation}
            onNewChat={handleNewChat}
            onClose={() => setShowSidebar(false)}
            onUploadComplete={handleUploadComplete}
          />
        )}

        {/* Chat area */}
        <div className="flex-1 flex flex-col min-w-0">
          <ChatBox
            onOpenPdf={handleOpenPdf}
            onToggleSidebar={handleToggleSidebar}
            onTogglePdf={handleTogglePdf}
            showSidebar={showSidebar}
            sessionId={currentSessionId}
            loadedMessages={loadedMessages}
            onSessionCreated={handleSessionCreated}
            onMessageSent={handleMessageSent}
          />
        </div>

        {/* PDF Viewer */}
        {showPdf && (
          <div className="w-1/2 min-w-[400px] max-w-[800px] border-l border-neutral-200">
            <DocViewer
              docId={selectedDocId}
              title={selectedDocTitle || selectedDocId || ""}
              onClose={handleClosePdf}
              page={selectedPage}
            />
          </div>
        )}
      </div>

      {/* Status bar */}
      <footer className="glass border-t border-neutral-200/60 shrink-0 z-10">
        <div className="flex items-center justify-between px-4 py-1.5">
          <div className="flex items-center gap-2">
            <Sparkles className="w-3.5 h-3.5 text-primary-500" />
            <span className="text-[11px] text-[#6b7280] font-medium tracking-wide">finRAG</span>
            <span className="text-[#d1d5db]">·</span>
            <span className="text-[10px] text-[#9ca3af]">金融文档智能问答</span>
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={() => setShowMetrics(!showMetrics)}
              className={`flex items-center gap-1 px-2 py-1 rounded-lg text-[11px] transition-all ${
                showMetrics
                  ? "bg-primary-50 text-primary-700 font-medium"
                  : "text-[#6b7280] hover:text-[#4b5563] hover:bg-gray-100"
              }`}
            >
              <BarChart3 className="w-3 h-3" />
              <span>状态</span>
            </button>
            <span className="w-px h-3 bg-gray-200" />
            <span className="text-[10px] text-[#9ca3af] font-mono">v2.0</span>
          </div>
        </div>
        {showMetrics && (
          <div className="absolute bottom-full right-4 mb-2 z-50 animate-fade-in-up">
            <MetricsPanel onClose={() => setShowMetrics(false)} />
          </div>
        )}
      </footer>
    </div>
  );
}
