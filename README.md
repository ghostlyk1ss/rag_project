<p align="center">
  <img src="https://img.shields.io/badge/Python-3.11+-blue?logo=python" alt="Python">
  <img src="https://img.shields.io/badge/TypeScript-5.3-blue?logo=typescript" alt="TypeScript">
  <img src="https://img.shields.io/badge/FastAPI-0.111-009688?logo=fastapi" alt="FastAPI">
  <img src="https://img.shields.io/badge/Next.js-14-000000?logo=nextdotjs" alt="Next.js">
  <img src="https://img.shields.io/badge/Qdrant-1.10-0099FF?logo=qdrant" alt="Qdrant">
  <img src="https://img.shields.io/badge/LangGraph-0.3-FF6B6B" alt="LangGraph">
  <img src="https://img.shields.io/badge/License-MIT-green" alt="License">
</p>

<h1 align="center">📊 finRAG</h1>
<h3 align="center">金融文档智能问答系统 · 基于检索增强生成（RAG）</h3>

<p align="center">
  <strong>专业模式 · 安全模式</strong> 双引擎架构，覆盖年报、研报、宏观数据、监管文件等金融文档的智能解析与问答。
  <br />
  <a href="#快速开始"><strong>快速开始 »</strong></a>
  &nbsp;·&nbsp;
  <a href="#功能特性"><strong>功能特性</strong></a>
  &nbsp;·&nbsp;
  <a href="#项目架构"><strong>架构说明</strong></a>
  &nbsp;·&nbsp;
  <a href="#开发指南"><strong>开发指南</strong></a>
</p>

---

## 📋 目录

- [项目简介](#项目简介)
- [功能特性](#功能特性)
- [技术栈](#技术栈)
- [项目架构](#项目架构)
- [快速开始](#快速开始)
- [配置说明](#配置说明)
- [开发指南](#开发指南)
- [API 文档](#api-文档)
- [项目结构](#项目结构)
- [License](#license)

---

## 📖 项目简介

**finRAG** 是一款面向金融领域的智能文档问答系统，基于 **检索增强生成（RAG）** 技术构建。系统可处理年报、研究报告、宏观经济数据、监管政策文件等多格式金融文档，支持用户上传文档后通过自然语言进行交互式问答。

系统提供 **双运行模式**：

| 模式 | 说明 | 适用场景 |
|------|------|----------|
| 🚀 **Pro（专业模式）** | 调用云端 DeepSeek V4 Flash API，推理能力强、速度快 | 日常分析、复杂推理 |
| 🔒 **Safe（安全模式）** | 使用本地 Ollama 模型（如 qwen2.5:7b），数据不出本机 | 涉密数据、监管合规 |

> 💡 两种模式运行时共享相同的检索管道与知识库，仅在生成环节使用不同的大语言模型。

---

## ✨ 功能特性

### 1. 双引擎模式

一键切换 **Pro 模式**（DeepSeek 云端 API）与 **Safe 模式**（Ollama 本地模型），兼顾性能与数据安全。模式在首次发送消息后永久绑定到当前会话。

### 2. 混合检索

融合 **稠密向量检索**（BGE 中文嵌入模型，512 维）与 **稀疏关键词检索**（BM25），通过 **RRF（Reciprocal Rank Fusion）** 算法融合排序，显著提升召回质量。支持基于意图的检索策略自适应（事实查询走轻量路径，分析/对比走宽召回）。

### 3. 智能 Agent 管道

基于 **LangGraph** 构建的多节点 Agent 工作流：

```
用户查询 → 查询重写 → 意图路由 → 并行实体检索 → 代码执行 → 答案生成 → 引用验证
```

- **查询重写**：关键词扩展 + LLM 指代消解（多轮对话时自动启用）
- **意图路由**：事实查询 / 对比分析 / 计算查询 / 总结归纳，自动路由到不同检索策略
- **多实体并行检索**：涉及多家公司时自动拆分为独立子查询并行执行
- **财务感知增强**：识别"净利润""ROE"等关键词时自动补充对应财报类型的检索结果
- **代码执行**：嵌入数值计算与财务指标提取节点
- **引用验证**：自动校验答案中的每个 `[来源N]` 引用是否真实存在于检索结果中

### 4. 精准引用溯源

答案中以 `[来源N]` 格式标注信息来源，鼠标悬停可预览原文片段，点击直接跳转到 PDF 对应页面，确保每个结论都有据可查。

### 5. 多轮对话记忆

- 自动保存每次对话到文件存储
- 侧边栏「历史」区域列出所有历史会话
- 点击历史会话加载完整上下文
- LLM 自动注入最近 4 轮历史消息，支持指代消解和上下文延续

### 6. 术语速查

内置独立术语查询面板：
- 输入金融术语（ROE、PB、杜邦分析等）即时获取专业解释
- 支持定义、计算公式、意义、使用场景
- 本地模型（Ollama）优先，不可用时远程 API fallback
- 查询结果自动缓存，重复查询秒出

### 7. 结构纲要生成

上传文档时可选择生成结构纲要（可选，按需消耗 DeepSeek API）：
- AI 逐章节生成概要索引
- 大幅提升宏观总结类问题的回答质量
- 费用约 ¥0.01~¥1.10/份文档

### 8. 多格式文档支持

支持 PDF、DOCX、PPTX、XLSX、Markdown、纯文本六种文档格式的上传与解析。上传时可选择目标集合（专业库/安全库/双库）。

### 9. 流式响应 + Agent 可视化

基于 SSE 的流式输出，用户可实时看到 **Agent 思维过程**（推理→检索→数据→完成），每个步骤独立渲染，直观理解回答生成过程。

### 10. 预算控制

内置三层预算管理体系：

| 层级 | 说明 | 默认限制 |
|------|------|----------|
| 📅 日预算 | 每日 LLM 调用总费用上限 | ¥50 |
| 💬 会话预算 | 单次会话可消耗的最大额度 | ¥10 |
| 🔄 请求预算 | 单次请求的可接受成本 | ¥0.5 |

超预算时自动拒绝请求并提示，防止意外消耗。

### 11. LLM 配置管理

前端内置 LLM 配置面板，支持：
- 查看/修改专业模式（DeepSeek）和安全模式（Ollama）的模型、地址、API Key
- AES 加密存储 API Key
- 一键测试连接

### 12. 运行状态面板

前端底部「状态」按钮打开实时监控面板：
- 多层缓存命中率
- 当日/历史 Token 消耗与费用
- 预算使用情况与剩余额度

---

## 🛠 技术栈

| 层次 | 技术 | 用途 |
|------|------|------|
| **前端** | Next.js 14 + Tailwind CSS v3 | 用户界面 |
| **后端** | FastAPI + LangGraph + Python 3.11+ | API 服务与 Agent 管道 |
| **向量数据库** | Qdrant（磁盘持久化 / Server 模式） | 向量存储与检索 |
| **嵌入模型** | BAAI/bge-small-zh-v1.5（512 维） | 中文文本向量化 |
| **大语言模型（Pro）** | DeepSeek V4 Flash API | 云端推理 |
| **大语言模型（Safe）** | Ollama 本地模型（如 qwen2.5:7b） | 本地推理 |
| **关键词检索** | BM25（自定义索引） | 稀疏检索 |
| **API 网关** | Nginx | 路由转发 |
| **部署** | Docker Compose | 容器化编排 |

---

## 🏗 项目架构

### 系统架构图

```
┌─────────────────────────────────────────────────────────────┐
│                        用户                                 │
└──────────────────┬─────────────────────────────────────────┘
                   │ HTTP/HTTPS
                   ▼
┌─────────────────────────────────────────────────────────────┐
│                    Nginx API Gateway (端口 80)               │
│        / → Next.js 前端  │  /api/* → FastAPI 后端          │
└──────────────────┬─────────────────────────────────────────┘
                   │
         ┌─────────┴────────────┐
         ▼                      ▼
┌─────────────────┐    ┌─────────────────────────────────────┐
│  Next.js 前端   │    │          FastAPI 后端 (8000)          │
│  (端口 3000)    │    │  ┌─────────────────────────────────┐ │
│                 │    │  │   Routes                        │ │
│  - 聊天界面     │    │  │   ├─ /chat (SSE 流式问答)      │ │
│  - 文件上传     │    │  │   ├─ /ingest (文档上传索引)     │ │
│  - PDF 内联预览 │    │  │   ├─ /sources (文档管理)        │ │
│  - 术语速查     │    │  │   ├─ /glossary (术语速查)       │ │
│  - 指标面板     │    │  │   ├─ /conversations (对话历史)  │ │
│  - LLM 配置     │    │  │   ├─ /metrics (运行指标)        │ │
│  - Agent 流程   │    │  │   └─ /llm_config (LLM配置)     │ │
│  - 历史对话     │    │  └─────────────────────────────────┘ │
└─────────────────┘    │                                       │
                       │  ┌─────────────────────────────────┐ │
                       │  │   Services                      │ │
                       │  │   ├─ RAG Engine (检索+生成)     │ │
                       │  │   ├─ Budget Guard (三层预算)    │ │
                       │  │   ├─ Cache (多级响应缓存)       │ │
                       │  │   ├─ Citation Verifier (引用核验)│ │
                       │  │   └─ Cost Tracker (费用追踪)    │ │
                       │  └─────────────────────────────────┘ │
                       │                                       │
                       │  ┌──────────┐  ┌──────────────────┐  │
                       │  │  Qdrant  │  │  BM25 索引       │  │
                       │  │ 向量数据库│  │ 关键词检索       │  │
                       │  └──────────┘  └──────────────────┘  │
                       │                                       │
                       │  ┌──────────────────────────────────┐ │
                       │  │  LLM 引擎                        │ │
                       │  │  ├─ DeepSeek API（Pro 模式）     │ │
                       │  │  └─ Ollama 本地（Safe 模式）      │ │
                       │  └──────────────────────────────────┘ │
                       └─────────────────────────────────────┘
```

### 数据处理流程

```
文档上传 → 格式解析(.pdf/.docx/.pptx/.xlsx/.md/.txt)
          → 元数据提取(公司/年份/类型)
          → Parent-Child 分块
          → 编码向量化 → Qdrant 索引 + BM25 索引
          → [可选] 结构纲要生成
                                         ↓
用户提问 → 查询重写(关键词扩展/LLM指代消解)
         → 意图路由(事实/对比/分析/计算/总结)
         → 混合检索(向量+BM25+RRF融合)
         → 多实体并行检索(如涉及多家公司)
         → 财务感知增强(自动补充财报类型检索)
         → LLM 生成答案(含对话历史注入+引用标注)
         → 引用验证(逐条核验[来源N]真实性)
         → SSE 流式返回
```

### 多轮对话流程

```
首次提问(无session_id) → 后端自动创建会话 → 绑定模式(pro/safe)
                      → 保存用户消息 + LLM 回答到 data/conversations/{sid}.json
                      
后续提问(携带session_id) → 加载历史消息(最近4轮)
                        → 注入 LLM 上下文进行指代消解
                        → 追加保存本轮问答
                        
侧边栏「历史」→ 列出所有会话 → 点击加载完整对话上下文
```

---

## 🚀 快速开始

### 前提条件

- Docker & Docker Compose v2+
- NVIDIA GPU 驱动（可选，用于本地模型加速）

### 使用 Docker 一键部署（推荐）

```bash
# 1. 克隆仓库
git clone https://github.com/ghostlyk1ss/rag_project.git
cd rag_project

# 2. 创建配置文件
cp .env.example .env

# 3. 编辑 .env 文件，填入你的 DeepSeek API Key
#    vim .env 或 nano .env

# 4. 构建并启动所有服务
docker compose build
docker compose up -d

# 5. 访问系统
#    浏览器打开 http://localhost
```

系统启动后：
- **前端界面**：http://localhost
- **后端 API**：http://localhost/api
- **API 文档**：http://localhost/api/docs
- **Qdrant 控制台**：http://localhost:6333/dashboard

### 手动启动（开发模式）

参见下方 [开发指南](#开发指南)。

---

## ⚙️ 配置说明

### 环境变量

| 变量名 | 说明 | 必填 | 默认值 |
|--------|------|------|--------|
| `LLM_API_KEY` | DeepSeek / LLM API 密钥 | ✅ | — |
| `LLM_MODEL` | LLM 模型名称（Pro 模式） | 否 | `deepseek-chat` |
| `LLM_BASE_URL` | LLM API 地址 | 否 | `https://api.deepseek.com` |
| `QDRANT_HOST` | Qdrant 服务器地址 | 否 | `localhost` |
| `QDRANT_PORT` | Qdrant 服务端口 | 否 | `6333` |
| `DAILY_BUDGET_YUAN` | 每日 LLM 调用预算（元） | 否 | `50` |
| `SESSION_BUDGET_YUAN` | 单次会话预算（元） | 否 | `10` |
| `REQUEST_BUDGET_YUAN` | 单次请求预算（元） | 否 | `0.5` |
| `SAFE_LLM_MODEL` | Safe 模式使用的本地模型 | 否 | `qwen2.5:7b` |
| `SAFE_LLM_BASE_URL` | Ollama 服务地址 | 否 | `http://host.docker.internal:11434` |
| `EMBEDDING_MODEL` | 嵌入模型名称 | 否 | `BAAI/bge-small-zh-v1.5` |
| `LOG_LEVEL` | 日志级别 | 否 | `INFO` |

### 配置示例（.env）

```bash
# LLM 配置（Pro 模式）
LLM_API_KEY=sk-your-api-key-here
LLM_MODEL=deepseek-chat
LLM_BASE_URL=https://api.deepseek.com

# Qdrant 配置
QDRANT_HOST=qdrant
QDRANT_PORT=6333

# 预算控制
DAILY_BUDGET_YUAN=50
SESSION_BUDGET_YUAN=10
REQUEST_BUDGET_YUAN=0.5

# 安全模式
SAFE_LLM_MODEL=qwen2.5:7b
SAFE_LLM_BASE_URL=http://host.docker.internal:11434

# 嵌入模型
EMBEDDING_MODEL=BAAI/bge-small-zh-v1.5
```

---

## 💻 开发指南

### 本地开发环境搭建

#### 1. 克隆与配置

```bash
git clone https://github.com/ghostlyk1ss/rag_project.git
cd rag_project
cp .env.example .env
# 编辑 .env 填入 LLM API Key
```

#### 2. 启动基础设施（Qdrant）

```bash
# 使用 Docker 启动 Qdrant（无需手动安装）
docker run -d \
  --name qdrant \
  -p 6333:6333 \
  -p 6334:6334 \
  -v $(pwd)/data/qdrant_storage:/qdrant/storage \
  qdrant/qdrant
```

#### 3. 启动后端

```bash
cd backend

# 创建虚拟环境
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

# 安装依赖
pip install -r ../requirements.txt

# 启动开发服务器（热重载）
uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000
```

#### 4. 启动前端

```bash
cd frontend

# 安装依赖
npm install

# 启动开发服务器
npm run dev
```

#### 5. 启动 Ollama（Safe 模式需要）

```bash
# 安装并启动 Ollama
# 详见 https://ollama.com/download

# 拉取模型
ollama pull qwen2.5:7b

# 启动服务（默认端口 11434）
ollama serve
```

#### 6. 访问

- 前端：http://localhost:3000
- 后端 API：http://localhost:8000
- API 文档：http://localhost:8000/docs

### 常用命令

```bash
# Docker 构建与启动
docker compose build
docker compose up -d

# 查看日志
docker compose logs -f backend
docker compose logs -f frontend

# 重启单个服务
docker compose restart backend

# 重新构建前端（更新源码后）
docker compose build frontend
docker compose up -d frontend
```

---

## 📚 API 文档

### 核心接口

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/api/v1/chat` | 发送聊天消息（流式 SSE 响应） |
| `POST` | `/api/v1/ingest` | 上传并索引文档（可选生成纲要） |
| `GET` | `/api/v1/ingest/{task_id}` | 查询入库任务状态 |
| `GET` | `/api/v1/sources` | 获取已索引的文档列表 |
| `DELETE` | `/api/v1/sources/{doc_id}` | 删除指定文档 |
| `POST` | `/api/v1/sources/refresh` | 刷新文档列表缓存 |
| `GET` | `/api/v1/sources/{doc_id}` | 获取文档详情与 Chunks |
| `GET` | `/api/v1/pdf/{doc_id}` | 获取原始 PDF 文件（流式） |
| `POST` | `/api/v1/glossary/query` | 术语速查（自动 fallback） |
| `POST` | `/api/v1/glossary/query/confirm` | 术语速查（确认后走远程 API） |
| `GET` | `/api/v1/glossary/list` | 获取已缓存术语列表 |
| `DELETE` | `/api/v1/glossary/cache` | 清空术语缓存 |
| `GET` | `/api/v1/conversations` | 列出所有历史对话 |
| `GET` | `/api/v1/conversations/{sid}` | 获取对话详情（含消息） |
| `DELETE` | `/api/v1/conversations/{sid}` | 删除指定对话 |
| `GET` | `/api/v1/metrics` | 获取运行指标（缓存/费用/预算） |
| `PUT` | `/api/v1/metrics/budget` | 更新预算配置 |
| `DELETE` | `/api/v1/metrics/cost` | 重置费用历史 |
| `GET` | `/api/v1/llm/config` | 获取 LLM 配置 |
| `PUT` | `/api/v1/llm/config` | 更新 LLM 配置 |
| `POST` | `/api/v1/llm/test` | 测试 LLM 连接 |
| `GET` | `/api/v1/health` | 健康检查 |

### 调用示例

```bash
# 聊天问答（流式）
curl -N -X POST http://localhost/api/v1/chat \
  -H "Content-Type: application/json" \
  -d '{
    "query": "贵州茅台2025年营收是多少？",
    "mode": "pro"
  }'

# 文档上传
curl -X POST http://localhost/api/v1/ingest \
  -F "file=@/path/to/report.pdf" \
  -F "collections=pro"

# 术语速查
curl -X POST http://localhost/api/v1/glossary/query \
  -H "Content-Type: application/json" \
  -d '{"term": "ROE"}'

# 获取指标面板数据
curl http://localhost/api/v1/metrics
```

详细的 API 文档请参考启动后的 Swagger UI：http://localhost/api/docs。

---

## 📁 项目结构

```
finrag/
├── backend/                  # FastAPI 后端服务
│   ├── main.py              # 应用入口 + 路由注册
│   ├── config.py            # 全局配置（环境变量加载）
│   ├── routers/             # API 路由
│   │   ├── chat.py          #   流式/阻塞问答接口
│   │   ├── ingest.py        #   文档上传与索引(含纲要生成)
│   │   ├── sources.py       #   文档列表/详情/删除
│   │   ├── pdf.py           #   PDF 文件服务(FileResponse)
│   │   ├── doc.py           #   PDF 文件下载
│   │   ├── glossary.py      #   术语速查(缓存+本地+远程)
│   │   ├── conversations.py #   多轮对话记忆(CRUD)
│   │   ├── metrics.py       #   运行指标(缓存率/费用/预算)
│   │   └── llm_config.py    #   LLM配置管理(加密存储API Key)
│   ├── services/            # 核心服务
│   │   ├── rag_engine.py    #   RAG引擎(检索+生成+流式)
│   │   ├── cache.py         #   多级响应缓存(LRU)
│   │   ├── budget_guard.py  #   三层预算控制系统
│   │   ├── cost_tracker.py  #   Token/费用追踪
│   │   ├── citation_verifier.py # 引用真实性核验
│   │   └── guardrail.py     #   安全守卫(拒答检查)
│   └── models/
│       └── schemas.py       #   Pydantic 数据模型
│
├── frontend/                # Next.js 14 前端
│   └── src/
│       ├── app/             #   页面路由
│       │   ├── page.tsx     #   入口页
│       │   ├── ClientPage.tsx # 主页面(侧边栏+聊天+PDF)
│       │   └── globals.css  #   全局样式
│       ├── components/      #   React 组件
│       │   ├── ChatBox.tsx  #       聊天界面+Agent流程可视化
│       │   ├── Sidebar.tsx  #       侧边栏(文档+历史+上传)
│       │   ├── DocViewer.tsx #      PDF 内联预览
│       │   ├── GlossaryModal.tsx #  术语速查弹窗
│       │   ├── SettingsModal.tsx #  LLM 配置弹窗
│       │   └── MetricsPanel.tsx #   运行指标面板
│       └── lib/
│           └── api.ts       #   API 客户端封装
│
├── scripts/                 # 核心 Python 逻辑
│   ├── agents/              # LangGraph Agent 节点
│   │   ├── query_rewriter.py #   查询重写
│   │   ├── intent_router.py #    意图路由
│   │   └── llm.py          #   LLM 封装
│   ├── retriever.py         # 混合检索引擎(向量+BM25+RRF)
│   ├── ingestion_v2.py      # 文档解析+分块+索引流水线
│   ├── generate_outlines.py # 结构纲要生成(DeepSeek API)
│   ├── kb_meta.py           # 知识库元数据索引
│   └── annotate_sources.py  # 文档标签标注
│
├── gateway/                 # Nginx API 网关配置
│   └── nginx.conf
│
├── docker/                  # Docker 部署脚本
│   ├── Dockerfile.frontend  # 前端镜像构建
│   ├── Dockerfile.backend   # 后端镜像构建
│   └── setup.sh             # 初始化脚本
│
├── data/                    # 运行时数据(.gitignore)
│   ├── conversations/       #   多轮对话历史
│   ├── glossary_cache.json  #   术语缓存
│   └── llm_config.json      #   LLM配置(加密)
│
├── docker-compose.yml       # Docker Compose 编排(4服务)
├── Dockerfile.backend       # 后端 Docker 镜像
├── Dockerfile.frontend      # 前端 Docker 镜像
├── requirements.txt         # Python 依赖
├── start.sh                 # 容器启动脚本
└── .env.example             # 环境变量模板
```

> 📝 `data/`、`docker/data/`、`docker/cache/` 等目录在 `.gitignore` 中排除，运行时会自动生成。系统启动时知识库为空，需要用户自行上传文档。

---

## 🤝 参与贡献

欢迎通过 Issue 和 Pull Request 参与贡献。

### 贡献指南

1. Fork 本仓库
2. 创建特性分支：`git checkout -b feat/amazing-feature`
3. 提交更改：`git commit -m 'feat: add amazing feature'`
4. 推送到分支：`git push origin feat/amazing-feature`
5. 提交 Pull Request

---

## 📄 License

本项目基于 **MIT License** 开源。

```
MIT License

Copyright (c) 2024-present ghostlyk1ss

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHER DEALINGS IN THE
SOFTWARE.
```

---

