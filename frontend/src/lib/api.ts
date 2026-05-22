const API_BASE = process.env.NEXT_PUBLIC_API_URL || "";

export interface Citation {
  index: number;
  company: string;
  year: string;
  doc_type: string;
  text: string;
  doc_id?: string;
  page?: number;
}

export interface SourceDocument {
  doc_id: string;
  file_name: string;
  title: string;
  company: string;
  year: string;
  doc_type: string;
  chunks: number;
  modes?: string[];
  has_outline?: boolean;
}

export interface SSEEvent {
  type: "thought" | "search" | "delta" | "answer" | "error" | "done";
  content: string;
  data?: { citations?: Citation[]; intent?: string; elapsed_sec?: number };
}

// ── Conversation Types ──────────────────────────────────

export interface MessageData {
  role: "user" | "assistant";
  content: string;
  citations?: Citation[];
}

export interface ConversationPreview {
  session_id: string;
  title: string;
  message_count: number;
  created_at: string;
  updated_at: string;
}

export interface ConversationDetail {
  session_id: string;
  title: string;
  messages: MessageData[];
  created_at: string;
  updated_at: string;
}

// ── Metrics Types ─────────────────────────────────────────

export interface CacheLayer {
  layer: string;
  size: number;
  maxsize: number;
  hits: number;
  misses: number;
  hit_rate: number;
  evictions: number;
}

export interface CostToday {
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  cost: number;
  call_count: number;
}

export interface MetricsData {
  caches: CacheLayer[];
  cost: {
    today: CostToday;
    all_time: CostToday;
  };
  budget: {
    daily_budget: number;
    daily_cost: number;
    daily_ratio: number;
    daily_remaining: number;
    all_time_budget: number;
    all_time_cost: number;
    all_time_ratio: number;
    all_time_remaining: number;
    degrade_strategy: string;
  };
}

// ── Chat API ────────────────────────────────────────────

export async function* chatStream(
  query: string,
  sessionId = "default",
  mode = "pro"
): AsyncGenerator<SSEEvent & { sessionId?: string }> {
  const response = await fetch(`${API_BASE}/api/v1/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ query, session_id: sessionId, stream: true, top_k: 8, mode }),
  });

  if (!response.ok) throw new Error(`HTTP ${response.status}`);

  // Extract session_id from response headers (auto-created sessions)
  const newSessionId = response.headers.get("X-Session-Id") || sessionId;

  if (!response.body) throw new Error("No response body");

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop() || "";

    for (const line of lines) {
      if (line.startsWith("data: ")) {
        try {
          const event: SSEEvent = JSON.parse(line.slice(6));
          yield { ...event, sessionId: newSessionId };
        } catch {
          /* skip malformed */
        }
      }
    }
  }
}

export async function chatBlocking(query: string, sessionId = "default") {
  const res = await fetch(`${API_BASE}/api/v1/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ query, session_id: sessionId, stream: false, top_k: 8 }),
  });
  return res.json();
}

// ── Sources API ─────────────────────────────────────────

export async function fetchSources(): Promise<SourceDocument[]> {
  const res = await fetch(`${API_BASE}/api/v1/sources`);
  const data = await res.json();
  return data.documents || [];
}

export async function deleteSource(docId: string): Promise<boolean> {
  const res = await fetch(`${API_BASE}/api/v1/sources/${docId}`, {
    method: "DELETE",
  });
  return res.ok;
}

// ── Conversations API ───────────────────────────────────

export async function fetchConversations(): Promise<ConversationPreview[]> {
  const res = await fetch(`${API_BASE}/api/v1/conversations`);
  const data = await res.json();
  return data.conversations || [];
}

export async function fetchConversation(
  sessionId: string
): Promise<ConversationDetail> {
  const res = await fetch(`${API_BASE}/api/v1/conversations/${sessionId}`);
  return res.json();
}

export async function deleteConversation(sessionId: string): Promise<boolean> {
  const res = await fetch(`${API_BASE}/api/v1/conversations/${sessionId}`, {
    method: "DELETE",
  });
  return res.ok;
}

// ── Metrics API ───────────────────────────────────────────

export async function fetchMetrics(): Promise<MetricsData> {
  const res = await fetch(`${API_BASE}/api/v1/metrics`);
  return res.json();
}

export async function resetCostHistory(): Promise<void> {
  await fetch(`${API_BASE}/api/v1/metrics/cost`, { method: "DELETE" });
}

export async function updateBudget(body: {
  daily?: number;
  all_time?: number;
  session?: number;
  request?: number;
  degrade?: string;
}): Promise<void> {
  await fetch(`${API_BASE}/api/v1/metrics/budget`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

// ── Ingest API ──────────────────────────────────────────

export async function uploadPdf(
  file: File,
  collections = "pro",
  generateOutline = false,
  onProgress?: (pct: number) => void
): Promise<{ task_id: string; file_name: string }> {
  const form = new FormData();
  form.append("file", file);

  const params = new URLSearchParams();
  params.set("collections", collections);
  if (generateOutline) params.set("generate_outline", "true");
  const url = `${API_BASE}/api/v1/ingest?${params.toString()}`;
  const res = await fetch(url, {
    method: "POST",
    body: form,
  });

  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || `上传失败: HTTP ${res.status}`);
  }

  const result = await res.json();
  return result;
}

export async function pollIngestStatus(
  taskId: string,
  onUpdate?: (status: string, message: string) => void
): Promise<string> {
  const MAX_ATTEMPTS = 120;  // ~2 min timeout
  let attempts = 0;
  return new Promise((resolve, reject) => {
    const poll = async () => {
      attempts++;
      if (attempts > MAX_ATTEMPTS) {
        reject(new Error("处理超时（2分钟），请重试"));
        return;
      }
      try {
        const res = await fetch(`${API_BASE}/api/v1/ingest/${taskId}`);
        if (!res.ok) {
          // Task not found → likely backend restarted
          if (res.status === 404) {
            reject(new Error("上传任务已丢失（后端可能已重启）"));
            return;
          }
          throw new Error(`HTTP ${res.status}`);
        }
        const data = await res.json();
        onUpdate?.(data.status, data.message || "");
        if (data.status === "completed") {
          resolve(data.message || "完成");
        } else if (data.status === "error") {
          reject(new Error(data.error || "处理失败"));
        } else {
          setTimeout(poll, 1000);
        }
      } catch (e) {
        reject(e);
      }
    };
    poll();
  });
}

// ── Glossary API ─────────────────────────────────────────

export async function queryGlossary(
  term: string
): Promise<{ term: string; definition?: string; cached?: boolean; fallback?: boolean; tip?: string }> {
  const res = await fetch(`${API_BASE}/api/v1/glossary/query`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ term }),
  });
  return res.json();
}

export async function queryGlossaryConfirm(
  term: string
): Promise<{ term: string; definition: string; cached: boolean }> {
  const res = await fetch(`${API_BASE}/api/v1/glossary/query/confirm`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ term }),
  });
  return res.json();
}

export async function listGlossaryCache(): Promise<string[]> {
  const res = await fetch(`${API_BASE}/api/v1/glossary/list`);
  const data = await res.json();
  return data.terms || [];
}

export async function clearGlossaryCache(): Promise<void> {
  await fetch(`${API_BASE}/api/v1/glossary/cache`, { method: "DELETE" });
}

// ── LLM Config API ───────────────────────────────────────

export async function fetchLlmConfig(): Promise<{ professional: any; safe: any }> {
  const res = await fetch(`${API_BASE}/api/v1/llm/config`);
  return res.json();
}

export async function updateLlmConfig(body: Record<string, any>): Promise<void> {
  await fetch(`${API_BASE}/api/v1/llm/config`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export async function testLlmConnection(body: {
  mode: string;
  model: string;
  base_url: string;
  api_key?: string;
}): Promise<{ success: boolean; message?: string; error?: string }> {
  const res = await fetch(`${API_BASE}/api/v1/llm/test`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return res.json();
}
