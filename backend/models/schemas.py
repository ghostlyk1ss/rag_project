"""
finRAG API — Pydantic 数据模型
"""
from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


# ── Chat ─────────────────────────────────────────────────────────

class ChatRequest(BaseModel):
    query: str = Field(..., description="用户问题", min_length=1, max_length=2000)
    session_id: str = Field(default="default", description="会话ID（用于多轮对话）")
    stream: bool = Field(default=True, description="是否流式返回")
    top_k: int = Field(default=8, ge=1, le=20, description="检索结果数")
    mode: str = Field(default="pro", description="模式：pro=专业(DeepSeek+联网库), safe=安全(Ollama+本地库)")


class ChatResponse(BaseModel):
    answer: str
    citations: list[dict] = []
    intent: str = "factual"
    elapsed_sec: float = 0.0


# ── SSE 事件类型 ────────────────────────────────────────────────

class SSEEvent(BaseModel):
    """SSE 消息包类型"""
    type: str = Field(..., pattern="^(thought|tool|search|answer|error|done)$")
    content: str = ""
    data: Optional[dict] = None


# ── Ingest ───────────────────────────────────────────────────────

class IngestResponse(BaseModel):
    status: str = Field(..., pattern="^(processing|completed|error)$")
    task_id: str = ""
    message: str = ""
    file_name: str = ""
    chunks_count: int = 0
    error: Optional[str] = None


class IngestStage(str, Enum):
    """入库阶段，用于前端更细粒度的进度显示"""
    QUEUED = "queued"
    PARSING = "parsing"
    INDEXING = "indexing"
    FINALIZING = "finalizing"
    DONE = "done"


class IngestStatus(BaseModel):
    task_id: str
    status: str = Field(..., pattern="^(queued|processing|completed|error)$")
    file_name: str
    progress: float = 0.0
    message: str = ""
    stage: IngestStage = IngestStage.QUEUED
    chunks_count: int = 0
    error: Optional[str] = None


# ── Sources ──────────────────────────────────────────────────────

class SourceDocument(BaseModel):
    doc_id: str
    file_name: str
    title: str = ""
    company: str = ""
    year: str = ""
    doc_type: str = ""
    char_count: int = 0
    page_count: int = 0
    has_table: bool = False
    parsed_at: Optional[str] = None
    error: Optional[str] = None


class SourceChunk(BaseModel):
    chunk_id: str
    text: str
    score: float = 0.0
    is_parent: bool = False
    payload: dict = {}


class SourceDetail(BaseModel):
    document: SourceDocument
    chunks: list[SourceChunk] = []


# ── Evaluate ─────────────────────────────────────────────────────

class EvaluateRequest(BaseModel):
    benchmark_path: str = Field(
        default="benchmark/finrag_benchmark.json",
        description="Benchmark JSON 路径",
    )
    question_ids: Optional[list[str]] = Field(
        default=None,
        description="指定问题子集（null=全部）",
    )


class EvaluateMetric(BaseModel):
    name: str
    score: float = 0.0
    weight: float = 1.0


class EvaluateResult(BaseModel):
    question_id: str
    question: str
    gold_answer: str
    predicted_answer: str = ""
    retrieval_recall: float = 0.0
    retrieval_mrr: float = 0.0
    grounding_faithfulness: float = 0.0
    answer_exact_match: bool = False
    answer_f1: float = 0.0
    financial_number_accuracy: float = 0.0
    final_score: float = 0.0
    error: Optional[str] = None


class EvaluateSummary(BaseModel):
    total: int = 0
    completed: int = 0
    avg_final_score: float = 0.0
    avg_recall: float = 0.0
    avg_faithfulness: float = 0.0
    avg_f1: float = 0.0
    avg_number_accuracy: float = 0.0
    by_difficulty: dict = {}
    by_capability: dict = {}
    results: list[EvaluateResult] = []


# ── Conversations ──────────────────────────────────────────────────

class ConversationMessage(BaseModel):
    role: str = Field(..., pattern="^(user|assistant)$")
    content: str
    citations: list[dict] = []


class ConversationPreview(BaseModel):
    session_id: str
    title: str
    message_count: int
    created_at: str
    updated_at: str
    mode: str = "pro"


class ConversationDetail(BaseModel):
    session_id: str
    title: str
    messages: list[ConversationMessage] = []
    created_at: str
    updated_at: str
    mode: str = "pro"


class ConversationListResponse(BaseModel):
    total: int
    conversations: list[ConversationPreview]
