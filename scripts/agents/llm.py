"""
finRAG Agent — LLM 客户端封装

统一管理 DeepSeek (OpenAI-compatible) 的 API 调用。
支持流式输出、结构化输出、错误重试。
"""

import json
import logging
import os
import re
import time
from typing import Optional

from openai import AsyncOpenAI, OpenAI
from langsmith.wrappers import wrap_openai
from dotenv import load_dotenv

# 加载 .env（LangSmith 配置）
load_dotenv()

logger = logging.getLogger("agent.llm")

# ── 专业模式配置：从环境变量读取（必须在 .env 或环境变量中设置） ─────────
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
if not DEEPSEEK_API_KEY:
    logger.warning("⚠️  DEEPSEEK_API_KEY 未设置！请创建 .env 文件或设置环境变量")
DEEPSEEK_BASE_URL = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
DEEPSEEK_MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")

# ── 安全模式配置（本地 Ollama） ─────────────────────────────────────
SAFE_LLM_BASE_URL = os.environ.get("SAFE_LLM_BASE_URL", "http://localhost:11434/v1")
SAFE_LLM_MODEL = os.environ.get("SAFE_LLM_MODEL", "qwen2.5:1.5b")
SAFE_LLM_API_KEY = "ollama"  # Ollama 不需要真实 key

MODE_PRO = "pro"
MODE_SAFE = "safe"


class LLMClient:
    """LLM 客户端（带重试、结构化输出）"""

    def __init__(
        self,
        api_key: str = DEEPSEEK_API_KEY,
        base_url: str = DEEPSEEK_BASE_URL,
        model: str = DEEPSEEK_MODEL,
        max_retries: int = 3,
    ):
        self.client = wrap_openai(OpenAI(api_key=api_key, base_url=base_url, max_retries=max_retries))
        self.model = model
        self.max_retries = max_retries

    def chat(
        self,
        messages: list[dict],
        temperature: float = 0.1,
        max_tokens: int = 4096,
        json_mode: bool = False,
        stream: bool = False,
        session_id: str = "default",
    ) -> str:
        """
        调用 LLM 生成回答。

        参数:
            messages: OpenAI 格式消息列表
            temperature: 温度（0-1，事实问答用 0.1，创意用 0.7）
            max_tokens: 最大输出 token 数
            json_mode: 是否返回结构化 JSON
            stream: 是否流式输出
            session_id: 会话 ID（用于 token 追踪）

        返回:
            生成的文本（如 json_mode=True，返回 JSON 字符串）
        """
        kwargs = dict(
            model=self.model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=stream,
        )
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}

        last_error = None
        for attempt in range(self.max_retries):
            try:
                resp = self.client.chat.completions.create(**kwargs)
                if stream:
                    # 流式返回
                    collected = []
                    for chunk in resp:
                        delta = chunk.choices[0].delta.content or ""
                        collected.append(delta)
                    full_text = "".join(collected)
                    # 流式无法获取 token 数，按字符粗略估算
                    prompt_chars = sum(len(m.get("content", "")) for m in messages)
                    estimated_prompt = prompt_chars // 4
                    estimated_completion = len(full_text) // 4
                    self._report_usage(session_id, estimated_prompt, estimated_completion, caller="chat_stream")
                    return full_text
                else:
                    full_text = resp.choices[0].message.content or ""
                    # 从 API 响应中捕获 token 用量
                    usage = getattr(resp, "usage", None)
                    if usage:
                        prompt_tokens = usage.prompt_tokens or 0
                        completion_tokens = usage.completion_tokens or 0
                    else:
                        # 降级估算
                        prompt_tokens = sum(len(m.get("content", "")) for m in messages) // 4
                        completion_tokens = len(full_text) // 4
                    self._report_usage(session_id, prompt_tokens, completion_tokens, caller="chat")
                    return full_text

            except Exception as e:
                last_error = e
                wait = 2 ** attempt
                logger.warning(f"⚠️  LLM 调用失败 (尝试 {attempt+1}/{self.max_retries}): {e}")
                if attempt < self.max_retries - 1:
                    time.sleep(wait)

        raise RuntimeError(f"LLM 调用失败（已重试 {self.max_retries} 次）: {last_error}")

    def _report_usage(self, session_id: str, prompt_tokens: int, completion_tokens: int, caller: str = ""):
        """上报 token 用量到 cost_tracker。"""
        try:
            from backend.services.cost_tracker import track_call
            track_call(
                session_id=session_id,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                model=self.model,
                caller=caller,
            )
        except ImportError:
            pass  # cost_tracker 不可用时静默跳过

    def chat_json(self, messages: list[dict], temperature: float = 0.1, session_id: str = "default") -> dict:
        """调用 LLM 并解析 JSON 输出。"""
        text = self.chat(messages, temperature=temperature, json_mode=True, session_id=session_id)
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            logger.warning(f"⚠️  JSON 解析失败，尝试修复: {e}")
            # 尝试从文本中提取 JSON
            m = re.search(r"\{.*\}", text, re.DOTALL)
            if m:
                return json.loads(m.group())
            raise ValueError(f"无法解析 LLM 输出为 JSON: {text[:200]}")

    async def chat_stream(
        self,
        messages: list[dict],
        temperature: float = 0.1,
        max_tokens: int = 4096,
        timeout: float = 60.0,
    ):
        """Async generator that yields tokens one by one via streaming.

        Args:
            messages: OpenAI-format message list
            temperature: Sampling temperature (0-1)
            max_tokens: Maximum output tokens
            timeout: Per-request timeout in seconds (default 60s)

        Yields:
            str: Each token as it arrives from the API.

        Raises:
            RuntimeError: If all retries fail.
        """
        client = AsyncOpenAI(
            api_key=self.client.api_key,
            base_url=self.client.base_url,
            timeout=timeout,
            max_retries=self.max_retries,
        )

        last_error = None
        for attempt in range(self.max_retries):
            try:
                stream = await client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    stream=True,
                )

                async for chunk in stream:
                    delta = chunk.choices[0].delta.content or ""
                    if delta:
                        yield delta
                return  # 成功完成

            except Exception as e:
                last_error = e
                wait = 2 ** attempt
                logger.warning(f"⚠️  流式调用失败 (尝试 {attempt+1}/{self.max_retries}): {e}")
                if attempt < self.max_retries - 1:
                    import asyncio
                    await asyncio.sleep(wait)

        raise RuntimeError(f"流式 LLM 调用失败（已重试 {self.max_retries} 次）: {last_error}")


# ── 全局单例（懒加载） ──────────────────────────────────────────
_client: Optional[LLMClient] = None
_safe_client: Optional[LLMClient] = None


def get_llm() -> LLMClient:
    """获取全局 LLM 单例（专业模式 — DeepSeek API）。"""
    global _client
    if _client is None:
        _client = LLMClient()
    return _client


def get_safe_llm() -> LLMClient:
    """获取安全模式 LLM 单例（Ollama 本地模型）。"""
    global _safe_client
    if _safe_client is None:
        _safe_client = LLMClient(
            api_key=SAFE_LLM_API_KEY,
            base_url=SAFE_LLM_BASE_URL,
            model=SAFE_LLM_MODEL,
        )
    return _safe_client
