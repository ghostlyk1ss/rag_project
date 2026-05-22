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
一键切换 **Pro 模式**（DeepSeek 云端 API）与 **Safe 模式**（Ollama 本地模型），兼顾性能与数据安全。

### 2. 混合检索
融合 **稠密向量检索**（BGE 中文嵌入模型，512 维）与 **稀疏关键词检索**（BM25），通过 **RRF（Reciprocal Rank Fusion）** 算法融合排序，显著提升召回质量。

### 3. 智能 Agent 管道
基于 **LangGraph** 构建的多节点 Agent 工作流：

```
用户查询 → 查询重写 → 意图路由 → 混合检索 → 代码执行 → 答案生成
```

- **查询重写**：HyDE 假设文档嵌入 + 多查询扩展 + 指代消解
- **意图路由**：事实查询 / 对比分析 / 计算查询 / 总结归纳，自动路由到不同处理策略
- **代码执行**：支持在答案中嵌入数值计算与财务指标提取

### 4. 精准引用溯源
答案中以 `[SourceN]` 标注信息来源，并自动链接到原始 PDF 文件的对应页面，确保每个结论都有据可查。

### 5. 多格式文档支持
支持 PDF、DOCX、PPTX、XLSX、Markdown、纯文本等多种文档格式的上传与解析。

### 6. 流式响应
基于 SSE（Server-Sent Events）的流式输出，用户可实时看到思考过程与逐步生成的答案。

### 7. 预算控制
内置三层预算管理体系：

| 层级 | 说明 | 默认限制 |
|------|------|----------|
| 📅 日预算 | 每日 LLM 调用总费用上限 | ¥50 |
| 💬 会话预算 | 单次会话可消耗的最大额度 | ¥10 |
| 🔄 请求预算 | 单次请求的可接受成本 | ¥0.5 |

### 8. 金融指标自动提取
自动从文档中识别并提取关键财务指标（营收、净利润、毛利率、ROE 等），支持对比分析与趋势呈现。

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
| **API 网关** | Nginx | 路由转发、负载均衡 |
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
│                 │    │  │   LangGraph Agent Pipeline       │ │
│  - 文件上传     │    │  │   ├─ 查询重写（HyDE + 多查询） │ │
│  - 聊天界面     │    │  │   ├─ 意图路由                  │ │
│  - 源文档查看   │    │  │   ├─ 混合检索                  │ │
│  - 模式切换     │    │  │   ├─ 代码执行                  │ │
└─────────────────┘    │  │   └─ 答案生成                  │ │
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
文档上传 → 格式解析 → 文本分块 → 嵌入向量化 → Qdrant 索引 + BM25 索引
                                                                       ↓
用户提问 → 查询重写 → 意图路由 → 混合检索（向量 + 关键词）
                                  → RRF 融合排序
                                  → LLM 生成答案（含引用标注）
                                  → SSE 流式返回
```

---

## 🚀 快速开始

### 前提条件

- Docker & Docker Compose v2+
- NVIDIA GPU 驱动（可选，用于本地模型加速）

### 使用 Docker 一键部署（推荐）

```bash
# 1. 克隆仓库
git clone https://github.com/wenli/finrag.git
cd finrag

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
| `DEEPSEEK_API_KEY` | DeepSeek API 密钥 | ✅ | — |
| `DEEPSEEK_MODEL` | DeepSeek 模型名称 | 否 | `deepseek-chat` |
| `DEEPSEEK_BASE_URL` | DeepSeek API 地址 | 否 | `https://api.deepseek.com` |
| `QDRANT_HOST` | Qdrant 服务器地址 | 否 | `localhost` |
| `QDRANT_PORT` | Qdrant 服务端口 | 否 | `6333` |
| `DAILY_BUDGET_YUAN` | 每日 LLM 调用预算（元） | 否 | `50` |
| `SESSION_BUDGET_YUAN` | 单次会话预算（元） | 否 | `10` |
| `REQUEST_BUDGET_YUAN` | 单次请求预算（元） | 否 | `0.5` |
| `SAFE_LLM_MODEL` | Safe 模式使用的本地模型 | 否 | `qwen2.5:7b` |
| `OLLAMA_HOST` | Ollama 服务地址 | 否 | `http://host.docker.internal:11434` |
| `EMBEDDING_MODEL` | 嵌入模型名称 | 否 | `BAAI/bge-small-zh-v1.5` |
| `CHUNK_SIZE` | 文档分块大小（字符数） | 否 | `512` |
| `CHUNK_OVERLAP` | 分块重叠大小 | 否 | `128` |
| `TOP_K` | 检索返回文档数量 | 否 | `5` |
| `LOG_LEVEL` | 日志级别 | 否 | `INFO` |

### 配置示例（.env）

```bash
# DeepSeek 配置
DEEPSEEK_API_KEY=sk-your-deepseek-api-key-here
DEEPSEEK_MODEL=deepseek-chat
DEEPSEEK_BASE_URL=https://api.deepseek.com

# Qdrant 配置
QDRANT_HOST=qdrant
QDRANT_PORT=6333

# 预算控制
DAILY_BUDGET_YUAN=50
SESSION_BUDGET_YUAN=10
REQUEST_BUDGET_YUAN=0.5

# 安全模式
SAFE_LLM_MODEL=qwen2.5:7b
OLLAMA_HOST=http://host.docker.internal:11434

# 嵌入与检索
EMBEDDING_MODEL=BAAI/bge-small-zh-v1.5
CHUNK_SIZE=512
CHUNK_OVERLAP=128
TOP_K=5
```

---

## 💻 开发指南

### 本地开发环境搭建

#### 1. 克隆与配置

```bash
git clone https://github.com/wenli/finrag.git
cd finrag
cp .env.example .env
# 编辑 .env 填入 DeepSeek API Key
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

> ⚠️ **注意**：手动开发的 Nginx 网关默认不启动。如需要，请参考 `gateway/` 目录下的配置自行启动。

### 常用命令

```bash
# 后端测试
cd backend
pytest tests/ -v

# 代码格式化
black backend/
ruff check backend/ --fix

# 前端构建
cd frontend
npm run build

# Docker 日志查看
docker compose logs -f backend

# 重启单个服务
docker compose restart backend
```

---

## 📚 API 文档

### 核心接口

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/api/chat` | 发送聊天消息（流式 SSE 响应） |
| `POST` | `/api/ingest` | 上传并索引文档 |
| `GET` | `/api/sources` | 获取已索引的文档列表 |
| `DELETE` | `/api/sources/{id}` | 删除指定文档 |
| `GET` | `/api/sources/{id}/pdf` | 获取原始 PDF 文件 |
| `GET` | `/api/health` | 健康检查 |
| `GET` | `/api/config` | 获取当前配置信息 |

### 调用示例

```bash
# 文档上传
curl -X POST http://localhost:8000/api/ingest \
  -F "file=@/path/to/annual_report.pdf"

# 聊天问答（流式）
curl -N -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{
    "query": "这家公司去年的营收是多少？",
    "mode": "pro",
    "session_id": "sess_001"
  }'
```

详细的 API 文档请参考启动后的 Swagger UI：http://localhost:8000/docs。

---

## 📁 项目结构

```
finrag/
├── backend/                  # FastAPI 后端服务
│   ├── main.py              # 应用入口 + 路由注册
│   ├── config.py            # 全局配置（环境变量加载）
│   ├── routers/             # API 路由
│   │   ├── chat.py          #   聊天接口
│   │   ├── ingest.py        #   文档上传与索引
│   │   ├── sources.py       #   文档管理
│   │   └── pdf.py           #   PDF 文件服务
│   └── services/            # 核心服务
│       ├── rag_engine.py    #   RAG 引擎
│       ├── cache.py         #   响应缓存
│       ├── budget_guard.py  #   预算控制系统
│       └── citation.py      #   引用标注管理
│
├── frontend/                # Next.js 14 前端
│   └── src/
│       ├── app/             #   页面路由
│       ├── components/      #   组件（聊天、上传、文档列表等）
│       └── lib/             #   API 客户端封装
│
├── scripts/                 # 核心 Python 逻辑
│   ├── agents/              # LangGraph Agent 节点
│   │   ├── rewrite.py       #   查询重写
│   │   ├── router.py        #   意图路由
│   │   ├── retrieve.py      #   混合检索
│   │   ├── execute.py       #   代码执行
│   │   └── generate.py      #   答案生成
│   ├── retriever.py         # 混合检索引擎（向量 + BM25 + RRF）
│   └── ingestion_v2.py      # 文档解析 + 分块 + 索引
│
├── gateway/                 # Nginx API 网关配置
│   └── nginx.conf
│
├── docker/                  # Docker 部署相关
│   └── Dockerfile.backend
│
├── data/                    # 运行时数据（.gitignore）
│   ├── qdrant_storage/      #   Qdrant 持久化数据
│   └── bm25_index/          #   BM25 索引文件
│
├── docker-compose.yml       # Docker Compose 编排
├── Dockerfile.backend       # 后端 Docker 镜像
├── requirements.txt         # Python 依赖
├── start.sh                 # 启动脚本
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

### 开发规范

- Python 代码遵循 [PEP 8](https://peps.python.org/pep-0008/) 规范
- commit 信息遵循 [Conventional Commits](https://www.conventionalcommits.org/) 规范
- 所有新功能需包含单元测试
- 提交前运行 `ruff check` 进行代码检查

---

## 📄 License

本项目基于 **MIT License** 开源。

```
MIT License

Copyright (c) 2024-present wenli

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
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

---

<p align="center">
  Made with ❤️ for the open-source community
  <br />
  <a href="https://github.com/wenli/finrag/issues">报告问题</a>
  ·
  <a href="https://github.com/wenli/finrag/discussions">讨论交流</a>
  ·
  <a href="https://github.com/wenli/finrag/releases">版本发布</a>
</p>
