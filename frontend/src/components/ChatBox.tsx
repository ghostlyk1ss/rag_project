"use client";

import React, { useState, useRef, useEffect, useCallback } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { chatStream, type Citation, type MessageData } from "@/lib/api";
import GlossaryModal from "@/components/GlossaryModal";
import SettingsModal from "@/components/SettingsModal";
import {
  Search,
  Brain,
  Database,
  CheckCircle2,
  PanelLeft,
  FileText,
  Send,
  Plus,
  ChevronDown,
  ChevronRight,
  Sparkles,
  Loader2,
  BookOpen,
  Lightbulb,
  Layers,
  X,
  Clock,
  GripVertical,
  ListTree,
  Shield,
  Cloud,
  Settings2,
} from "lucide-react";

interface Message {
  role: "user" | "assistant";
  content: string;
  citations?: Citation[];
}

interface ChatBoxProps {
  onOpenPdf?: (docId: string, page?: number) => void;
  onToggleSidebar?: () => void;
  onTogglePdf?: () => void;
  showSidebar?: boolean;
  sessionId?: string;
  loadedMessages?: MessageData[];
  onSessionCreated?: (sessionId: string) => void;
  onMessageSent?: () => void;
}

// ── Agent step types ───────────────────────────────────────
interface AgentStep {
  text: string;
  type: "thought" | "search" | "data" | "done";
  timestamp: number;
}

const STEP_CONFIG: Record<string, { icon: React.ReactNode; color: string; bg: string; dot: string; label: string }> = {
  thought: {
    icon: <Brain className="w-3.5 h-3.5" />,
    color: "text-violet-600",
    bg: "bg-violet-50 border-violet-200",
    dot: "bg-violet-500",
    label: "推理",
  },
  search: {
    icon: <Search className="w-3.5 h-3.5" />,
    color: "text-blue-600",
    bg: "bg-blue-50 border-blue-200",
    dot: "bg-blue-500",
    label: "检索",
  },
  data: {
    icon: <Database className="w-3.5 h-3.5" />,
    color: "text-emerald-600",
    bg: "bg-emerald-50 border-emerald-200",
    dot: "bg-emerald-500",
    label: "数据",
  },
  done: {
    icon: <CheckCircle2 className="w-3.5 h-3.5" />,
    color: "text-emerald-600",
    bg: "bg-emerald-50 border-emerald-200",
    dot: "bg-emerald-500",
    label: "完成",
  },
};

function getStepConfig(t: AgentStep) {
  const lower = t.text.toLowerCase();
  if (lower.includes("检索") || lower.includes("查找") || lower.includes("search") || lower.includes("chunk"))
    return STEP_CONFIG.search;
  if (lower.includes("数据") || lower.includes("找到") || lower.includes("知识库"))
    return STEP_CONFIG.data;
  if (lower.includes("完成") || lower.includes("done") || lower.includes("回答"))
    return STEP_CONFIG.done;
  return STEP_CONFIG.thought;
}

// ── Citation Tooltip ───────────────────────────────────────
function CitationTooltip({
  citation,
  position,
}: {
  citation: Citation;
  position: { x: number; y: number };
}) {
  return (
    <div
      className="fixed z-50 glass-card-dark text-white text-xs p-3 max-w-xs pointer-events-none animate-scale-in"
      style={{
        left: Math.min(position.x, window.innerWidth - 320),
        top: position.y,
      }}
    >
      <div className="flex items-center gap-1.5 mb-1.5 font-medium text-primary-300">
        <FileText className="w-3 h-3" />
        <span>
          [{citation.index}] {citation.company} · {citation.year}
          {citation.page ? ` · 第${citation.page}页` : ""}
        </span>
      </div>
      <div className="text-neutral-300 leading-relaxed text-[11px]">
        {citation.text.length > 200
          ? citation.text.slice(0, 200) + "..."
          : citation.text}
      </div>
      {citation.doc_id && (
        <div className="mt-1.5 text-primary-400 flex items-center gap-1 text-[11px]">
          <FileText className="w-3 h-3" />
          点击查看原文
        </div>
      )}
    </div>
  );
}

// ── Agent Timeline ─────────────────────────────────────────
function AgentTimeline({
  steps,
  loading,
  onToggle,
  collapsed,
  elapsed,
}: {
  steps: AgentStep[];
  loading: boolean;
  onToggle: () => void;
  collapsed: boolean;
  elapsed: number;
}) {
  const formatTime = (ms: number) => {
    const s = Math.floor(ms / 1000);
    if (s < 60) return `${s}s`;
    return `${Math.floor(s / 60)}m${s % 60}s`;
  };

  return (
    <div className="bg-white border border-neutral-200 shadow-card rounded-xl overflow-hidden animate-fade-in-up">
      {/* Header */}
      <button
        onClick={onToggle}
        className="flex items-center justify-between w-full px-3.5 py-2.5 hover:bg-neutral-50 transition-colors"
      >
        <div className="flex items-center gap-2">
          <ListTree className="w-3.5 h-3.5 text-primary-500" />
          <span className="text-[11px] font-medium text-neutral-700 uppercase tracking-wider">
            Agent 流程
          </span>
        </div>
        <div className="flex items-center gap-2">
          {loading && (
            <span className="flex items-center gap-1 text-[10px] text-primary-600 bg-primary-50 px-1.5 py-0.5 rounded-full">
              <Loader2 className="w-2.5 h-2.5 animate-spin" />
              执行中...
            </span>
          )}
          {elapsed > 0 && (
            <span className="flex items-center gap-1 text-[10px] text-neutral-400">
              <Clock className="w-2.5 h-2.5" />
              {formatTime(elapsed)}
            </span>
          )}
          {collapsed ? (
            <ChevronRight className="w-3.5 h-3.5 text-neutral-400" />
          ) : (
            <ChevronDown className="w-3.5 h-3.5 text-neutral-400" />
          )}
        </div>
      </button>

      {/* Steps */}
      {!collapsed && steps.length > 0 && (
        <div className="px-3.5 pb-3">
          <div className="relative">
            {/* Vertical line */}
            <div className="absolute left-[11px] top-2 bottom-2 w-[1.5px] bg-gradient-to-b from-neutral-200 via-neutral-200 to-transparent" />

            <div className="space-y-0">
              {steps.map((step, i) => {
                const cfg = getStepConfig(step);
                return (
                  <div key={i} className="relative pl-8 pt-2 first:pt-0">
                    {/* Dot */}
                    <div
                      className={`absolute left-[3px] top-[7px] w-[18px] h-[18px] rounded-full flex items-center justify-center ring-2 ring-white ${cfg.dot} ${cfg.color}`}
                    >
                      <div className="text-white w-3 h-3 flex items-center justify-center">
                        {cfg.icon}
                      </div>
                    </div>
                    {/* Arrow connector */}
                    <div className="absolute left-[25px] top-[13px] w-3 h-px bg-neutral-200" />
                    {/* Content */}
                    <div
                      className={`rounded-lg px-2.5 py-1.5 border ${cfg.bg} ${cfg.color}`}
                    >
                      <div className="flex items-center gap-1.5 mb-0.5">
                        <span className="text-[10px] font-semibold uppercase tracking-wider opacity-70">
                          {cfg.label}
                        </span>
                      </div>
                      <p className="text-[11.5px] leading-relaxed text-neutral-700">
                        {step.text}
                      </p>
                    </div>
                  </div>
                );
              })}

              {/* Loading dots */}
              {loading && (
                <div className="relative pl-8 pt-3 pb-1">
                  <div className="flex items-center gap-1.5">
                    <span className="w-1.5 h-1.5 bg-primary-400 rounded-full pulse-dot" />
                    <span className="w-1.5 h-1.5 bg-primary-400 rounded-full pulse-dot" />
                    <span className="w-1.5 h-1.5 bg-primary-400 rounded-full pulse-dot" />
                    <span className="text-[10px] text-neutral-400 ml-1">思考中</span>
                  </div>
                </div>
              )}
            </div>
          </div>
        </div>
      )}

      {/* Empty state */}
      {!collapsed && steps.length === 0 && !loading && (
        <div className="px-3.5 pb-3 text-center">
          <div className="flex flex-col items-center gap-1 py-3 text-neutral-400">
            <Lightbulb className="w-4 h-4" />
            <span className="text-[11px]">等待提问...</span>
          </div>
        </div>
      )}
    </div>
  );
}

// ── ChatBox Main Component ────────────────────────────────
export default function ChatBox({
  onOpenPdf,
  onToggleSidebar,
  onTogglePdf,
  showSidebar,
  sessionId: externalSessionId,
  loadedMessages,
  onSessionCreated,
  onMessageSent,
}: ChatBoxProps) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [agentSteps, setAgentSteps] = useState<AgentStep[]>([]);
  const [showTimeline, setShowTimeline] = useState(true);
  const [elapsedMs, setElapsedMs] = useState(0);
  const [hoveredCitation, setHoveredCitation] = useState<{
    citation: Citation;
    x: number;
    y: number;
  } | null>(null);
  const [currentSessionId, setCurrentSessionId] = useState<string>("default");
  const [mode, setMode] = useState<"pro" | "safe">("pro");
  const [modeBound, setModeBound] = useState(false);
  const [showGlossary, setShowGlossary] = useState(false);
  const [showSettings, setShowSettings] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const elapsedTimerRef = useRef<ReturnType<typeof setInterval>>();

  // Sync external sessionId — including reset on new chat (undefined)
  useEffect(() => {
    if (externalSessionId) {
      setCurrentSessionId(externalSessionId);
    } else {
      // externalSessionId became undefined → new chat
      setMessages([]);
      setAgentSteps([]);
      setCurrentSessionId("default");
      setMode("pro");
      setModeBound(false);
    }
  }, [externalSessionId]);

  // Load messages when loadedMessages changes
  useEffect(() => {
    if (loadedMessages && loadedMessages.length > 0) {
      const restored: Message[] = loadedMessages.map((m) => ({
        role: m.role,
        content: m.content,
        citations: m.citations,
      }));
      setMessages(restored);
      setAgentSteps([]);
    } else if (loadedMessages && loadedMessages.length === 0) {
      setMessages([]);
      setAgentSteps([]);
    }
  }, [loadedMessages]);

  // Scroll to bottom
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, agentSteps]);

  // Auto-focus input on new chat
  useEffect(() => {
    if (!loadedMessages || loadedMessages.length === 0) {
      inputRef.current?.focus();
    }
  }, [loadedMessages]);

  // Elapsed timer
  useEffect(() => {
    if (loading) {
      const start = Date.now();
      elapsedTimerRef.current = setInterval(() => {
        setElapsedMs(Date.now() - start);
      }, 1000);
    } else {
      clearInterval(elapsedTimerRef.current);
    }
    return () => clearInterval(elapsedTimerRef.current);
  }, [loading]);

  const doStream = useCallback(
    async (q: string, sid: string, currentMode: "pro" | "safe" = "pro") => {
      const startTime = Date.now();
      let hasContent = false;
      try {
        let savedSessionId = sid;
        for await (const event of chatStream(q, sid, currentMode)) {
          if (event.sessionId && event.sessionId !== savedSessionId) {
            savedSessionId = event.sessionId;
            setCurrentSessionId(savedSessionId);
            onSessionCreated?.(savedSessionId);
          }
          if (event.type === "thought" || event.type === "search") {
            setAgentSteps((prev) => [
              ...prev,
              {
                text: event.content,
                type: event.type === "search" ? "search" : "thought",
                timestamp: Date.now(),
              },
            ]);
          } else if (event.type === "delta") {
            hasContent = true;
            if (event.content) {
              setMessages((prev) => {
                const msgs = [...prev];
                const last = msgs[msgs.length - 1];
                if (last.role === "assistant") {
                  last.content += event.content;
                }
                return msgs;
              });
            }
          } else if (event.type === "answer") {
            hasContent = true;
            if (event.content) {
              setMessages((prev) => {
                const msgs = [...prev];
                const last = msgs[msgs.length - 1];
                if (last.role === "assistant") {
                  last.content = event.content;
                  if (event.data?.citations) last.citations = event.data.citations;
                }
                return msgs;
              });
            } else if (event.data?.citations) {
              setMessages((prev) => {
                const msgs = [...prev];
                const last = msgs[msgs.length - 1];
                if (last.role === "assistant") last.citations = event.data!.citations;
                return msgs;
              });
            }
          } else if (event.type === "error") {
            setMessages((prev) => {
              const msgs = [...prev];
              const last = msgs[msgs.length - 1];
              if (last.role === "assistant") last.content = "Error: " + event.content;
              return [...prev];
            });
          }
        }
      } catch (e: any) {
        setMessages((prev) => {
          const last = prev[prev.length - 1];
          if (last.role === "assistant") last.content = "Network error: " + e.message;
          return [...prev];
        });
      }
      // Add completion step
      setAgentSteps((prev) => [
        ...prev,
        {
          text: hasContent
            ? `回答生成完成（${((Date.now() - startTime) / 1000).toFixed(1)}s）`
            : "未生成回答",
          type: "done",
          timestamp: Date.now(),
        },
      ]);
      setLoading(false);
    },
    [onSessionCreated]
  );

  const handleSubmit = async () => {
    const q = input.trim();
    if (!q || loading) return;
    setInput("");
    setAgentSteps([]);
    setElapsedMs(0);
    setLoading(true);

    // Bind mode on first message
    if (!modeBound) {
      setModeBound(true);
    }

    const sid = currentSessionId;
    const userMsg: Message = { role: "user", content: q };
    setMessages((prev) => [...prev, userMsg]);

    const assistantMsg: Message = { role: "assistant", content: "" };
    setMessages((prev) => [...prev, assistantMsg]);

    onMessageSent?.();
    await doStream(q, sid, mode);
  };

  const newChat = () => {
    setMessages([]);
    setAgentSteps([]);
    setCurrentSessionId("default");
    setMode("pro");
    setModeBound(false);
  };

  const handleCitationClick = (citation: Citation) => {
    if (citation.doc_id && onOpenPdf) {
      onOpenPdf(citation.doc_id, citation.page);
    }
  };

  const handleCitationHover = (e: React.MouseEvent, citation: Citation) => {
    const rect = (e.target as HTMLElement).getBoundingClientRect();
    setHoveredCitation({ citation, x: rect.left, y: rect.bottom + 4 });
  };

  const handleCitationLeave = () => {
    setHoveredCitation(null);
  };

  const renderAnswerWithCitations = (content: string, citations?: Citation[]) => {
    if (!citations || citations.length === 0) {
      return (
        <div className="markdown-content">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{content}</ReactMarkdown>
        </div>
      );
    }

    const citationMap = new Map<number, Citation>();
    citations.forEach((c) => citationMap.set(c.index, c));

    return (
      <div className="markdown-content">
        <ReactMarkdown
          remarkPlugins={[remarkGfm]}
          components={{
            p: ({ children, ...props }) => {
              const processed = React.Children.map(children, (child) => {
                if (typeof child === "string") {
                  const elements: React.ReactNode[] = [];
                  const regex = /\[来源(\d+)\]/g;
                  let lastIndex = 0;
                  let match;
                  while ((match = regex.exec(child)) !== null) {
                    if (match.index > lastIndex) {
                      elements.push(child.slice(lastIndex, match.index));
                    }
                    const idx = parseInt(match[1], 10);
                    const cit = citationMap.get(idx);
                    if (cit) {
                      elements.push(
                        <button
                          key={`cit-${idx}`}
                          className="citation-tag"
                          onClick={() => handleCitationClick(cit)}
                          onMouseEnter={(e) => handleCitationHover(e, cit)}
                          onMouseLeave={handleCitationLeave}
                        >
                          [{idx}] {cit.company}
                        </button>
                      );
                    } else {
                      elements.push(<span key={`cit-${idx}`}>[{idx}]</span>);
                    }
                    lastIndex = match.index + match[0].length;
                  }
                  if (lastIndex < child.length) {
                    elements.push(child.slice(lastIndex));
                  }
                  return elements.length > 0 ? elements : child;
                }
                return child;
              });
              return <p {...props}>{processed}</p>;
            },
          }}
        >
          {content}
        </ReactMarkdown>
      </div>
    );
  };

  return (
    <div className="flex-1 flex flex-col h-full">
      {/* Header */}
      <header className="glass border-b border-neutral-200/60 shrink-0 z-10">
        <div className="flex items-center justify-between px-3 py-1.5">
          <div className="flex items-center gap-1.5">
            <button
              onClick={onToggleSidebar}
              className="p-1.5 hover:bg-neutral-100 rounded-lg transition-colors"
              title={showSidebar ? "收起侧边栏" : "展开侧边栏"}
            >
              <PanelLeft className="w-4 h-4 text-neutral-500" />
            </button>
            <span className="w-px h-4 bg-neutral-200 mx-0.5" />
            <div className="flex items-center gap-1.5 px-1.5 py-1 rounded-lg bg-primary-50/60">
              <Sparkles className="w-3.5 h-3.5 text-primary-600" />
              <h1 className="text-xs font-semibold text-primary-700 tracking-tight">finRAG</h1>
            </div>
            {/* Mode badge — shown once mode is bound */}
            {modeBound && (
              <div className={`flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-medium ${
                mode === "pro"
                  ? "bg-blue-50 text-blue-700 border border-blue-200"
                  : "bg-emerald-50 text-emerald-700 border border-emerald-200"
              }`}>
                {mode === "pro" ? <Cloud className="w-3 h-3" /> : <Shield className="w-3 h-3" />}
                <span>{mode === "pro" ? "专业" : "安全"}</span>
              </div>
            )}
          </div>
          <div className="flex items-center gap-1.5">
            <button
              onClick={() => setShowGlossary(true)}
              className="flex items-center gap-1.5 px-3.5 py-2 text-sm text-neutral-600 hover:bg-neutral-100 rounded-lg transition-colors font-medium"
              title="术语速查"
            >
              <BookOpen className="w-4 h-4" />
              <span>术语</span>
            </button>
            <button
              onClick={() => setShowSettings(true)}
              className="p-2 text-neutral-500 hover:bg-neutral-100 rounded-lg transition-colors"
              title="LLM 配置"
            >
              <Settings2 className="w-4 h-4" />
            </button>
            <button
              onClick={newChat}
              className="btn-primary flex items-center gap-1.5 px-3 py-1.5 text-xs"
            >
              <Plus className="w-3.5 h-3.5" />
              <span>新对话</span>
            </button>
          </div>
        </div>
      </header>

      {/* Messages area */}
      <div className="flex-1 overflow-y-auto bg-gradient-to-b from-surface-secondary to-white">
        <div className="max-w-3xl mx-auto px-4 py-6 space-y-5">
          {/* Empty state */}
          {messages.length === 0 && !loading && (
            <div className="flex flex-col items-center justify-center min-h-[55vh] animate-fade-in-up">
              <div className="w-14 h-14 rounded-2xl bg-gradient-to-br from-primary-100 to-primary-50 flex items-center justify-center mb-5 shadow-sm ring-1 ring-primary-200/50">
                <Sparkles className="w-7 h-7 text-primary-600" />
              </div>
              <h2 className="text-lg font-semibold text-neutral-900 mb-1 tracking-tight">
                finRAG 金融智能问答
              </h2>
              <p className="text-sm text-neutral-400 text-center max-w-sm mb-6 leading-relaxed">
                财报分析 · 研报解读 · 宏观数据查询
              </p>
              <div className="grid grid-cols-1 gap-2 w-full max-w-md">
                {[
                ].map((q) => (
                  <button
                    key={q}
                    onClick={() => {
                      setInput(q);
                      inputRef.current?.focus();
                    }}
                    className="group text-left px-4 py-2.5 text-sm text-neutral-600 bg-white border border-neutral-200 rounded-xl hover:border-primary-300 hover:text-primary-700 hover:shadow-card-hover transition-all active:scale-[0.99]"
                  >
                    <span className="flex items-center gap-2">
                      <Lightbulb className="w-3.5 h-3.5 text-neutral-300 group-hover:text-primary-400 transition-colors" />
                      {q}
                    </span>
                  </button>
                ))}
              </div>
              {/* Mode selector — shown before mode is bound */}
              {!modeBound && (
                <div className="mt-6 flex items-center justify-center gap-3 animate-fade-in-up">
                  <button
                    onClick={() => setMode("pro")}
                    className={`flex items-center gap-2 px-4 py-2.5 rounded-xl text-xs font-medium transition-all border ${
                      mode === "pro"
                        ? "bg-blue-50 text-blue-700 border-blue-300 shadow-sm ring-1 ring-blue-200"
                        : "bg-white text-neutral-500 border-neutral-200 hover:border-blue-200 hover:text-blue-600"
                    }`}
                  >
                    <Cloud className="w-4 h-4" />
                    <div className="text-left">
                      <div className="font-semibold">专业模式</div>
                      <div className="text-[10px] opacity-70 mt-0.5">DeepSeek · 联网知识库</div>
                      <div className="text-[9px] opacity-50 mt-0.5 leading-tight">云端大模型，回答精准、支持复杂推理与计算</div>
                    </div>
                    {mode === "pro" && <div className="w-2 h-2 rounded-full bg-blue-500 ml-1.5" />}
                  </button>
                  <button
                    onClick={() => setMode("safe")}
                    className={`flex items-center gap-2 px-4 py-2.5 rounded-xl text-xs font-medium transition-all border ${
                      mode === "safe"
                        ? "bg-emerald-50 text-emerald-700 border-emerald-300 shadow-sm ring-1 ring-emerald-200"
                        : "bg-white text-neutral-500 border-neutral-200 hover:border-emerald-200 hover:text-emerald-600"
                    }`}
                  >
                    <Shield className="w-4 h-4" />
                    <div className="text-left">
                      <div className="font-semibold">安全模式</div>
                      <div className="text-[10px] opacity-70 mt-0.5">Ollama · 本地知识库</div>
                      <div className="text-[9px] opacity-50 mt-0.5 leading-tight">本地小模型，隐私安全，但回答质量有限、速度较慢</div>
                    </div>
                    {mode === "safe" && <div className="w-2 h-2 rounded-full bg-emerald-500 ml-1.5" />}
                  </button>
                </div>
              )}
            </div>
          )}

          {/* Messages */}
          {messages.map((msg, i) => (
            <div
              key={i}
              className={`flex ${msg.role === "user" ? "justify-end" : "justify-start"} animate-fade-in-up`}
              style={{ animationDelay: `${i * 50}ms` }}
            >
              {/* User bubbles */}
              {msg.role === "user" ? (
                <div className="max-w-[75%] chat-user-bubble">
                  <p className="text-sm leading-relaxed text-white/90">{msg.content}</p>
                </div>
              ) : (
                /* Assistant bubbles */
                <div className="max-w-[85%] chat-assistant-bubble">
                  {msg.content ? (
                    renderAnswerWithCitations(msg.content, msg.citations)
                  ) : (
                    <div className="flex items-center gap-1.5 py-0.5">
                      <span className="w-1.5 h-1.5 bg-primary-400 rounded-full pulse-dot" />
                      <span className="w-1.5 h-1.5 bg-primary-400 rounded-full pulse-dot" />
                      <span className="w-1.5 h-1.5 bg-primary-400 rounded-full pulse-dot" />
                    </div>
                  )}

                  {/* Citations pills */}
                  {msg.citations && msg.citations.length > 0 && (
                    <div className="mt-3 pt-3 border-t border-neutral-100">
                      <div className="flex items-center gap-1 mb-1.5">
                        <BookOpen className="w-3 h-3 text-neutral-400" />
                        <span className="text-[10px] text-neutral-400 font-medium uppercase tracking-wider">
                          来源 ({msg.citations.length})
                        </span>
                      </div>
                      <div className="flex flex-wrap gap-1.5">
                        {msg.citations.slice(0, 6).map((c, j) => (
                          <button
                            key={j}
                            className="citation-tag"
                            onClick={() => handleCitationClick(c)}
                            onMouseEnter={(e) => handleCitationHover(e, c)}
                            onMouseLeave={handleCitationLeave}
                          >
                            [{c.index}] {c.company} {c.year}
                          </button>
                        ))}
                        {msg.citations.length > 6 && (
                          <span className="text-[11px] text-neutral-400 px-1 self-center font-medium">
                            +{msg.citations.length - 6}
                          </span>
                        )}
                      </div>
                    </div>
                  )}
                </div>
              )}
            </div>
          ))}

          {/* Agent Timeline */}
          {agentSteps.length > 0 && (
            <div className="animate-fade-in-up">
              <AgentTimeline
                steps={agentSteps}
                loading={loading}
                collapsed={!showTimeline}
                onToggle={() => setShowTimeline(!showTimeline)}
                elapsed={elapsedMs}
              />
            </div>
          )}

          <div ref={bottomRef} />
        </div>
      </div>

      {/* Input */}
      <div className="border-t border-neutral-200/60 bg-white px-4 py-3 shrink-0">
        <div className="max-w-3xl mx-auto">
          <div className="flex gap-2 input-box px-3 py-2">
            <input
              ref={inputRef}
              className="flex-1 bg-transparent text-sm text-[#1f2937] placeholder:text-[#9ca3af] outline-none"
              placeholder="输入金融问题..."
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && !e.shiftKey && handleSubmit()}
              disabled={loading}
            />
            <button
              className="btn-primary p-2 shrink-0"
              onClick={handleSubmit}
              disabled={loading || !input.trim()}
            >
              {loading ? (
                <Loader2 className="w-4 h-4 animate-spin" />
              ) : (
                <Send className="w-4 h-4" />
              )}
            </button>
          </div>
        </div>
      </div>

      {/* Citation tooltip overlay */}
      {hoveredCitation && (
        <CitationTooltip
          citation={hoveredCitation.citation}
          position={{ x: hoveredCitation.x, y: hoveredCitation.y }}
        />
      )}

      {/* Glossary Modal */}
      {showGlossary && <GlossaryModal onClose={() => setShowGlossary(false)} />}

      {/* Settings Modal */}
      {showSettings && <SettingsModal onClose={() => setShowSettings(false)} />}
    </div>
  );
}
