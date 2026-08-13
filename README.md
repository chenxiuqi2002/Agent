# Obsidian Note Management Agent

基于 **Claude Agent SDK** 的智能 Obsidian 笔记管理 Agent，运行在本地沙箱环境中，通过自然语言交互管理你的 Obsidian Vault。

集成了 [kepano/obsidian-skills](https://github.com/kepano/obsidian-skills) 的全部 5 个 Obsidian 专业技能，Agent 在创建和编辑内容时会自动遵循 Obsidian 的最佳实践。

---

## 项目架构

```
Agent/
├── .env                          # 环境变量配置（API Key、模型、路径等）
├── .env.example                  # 环境变量模板
├── .gitignore
├── requirements.txt              # Python 依赖
├── obsidian_agent/               # 核心 Agent 代码包
│   ├── __init__.py               # 包初始化（空文件）
│   ├── __main__.py               # 入口点，调用 agent.main()
│   ├── agent.py                  # Agent 核心：SDK 客户端创建、交互循环、沙箱 Hook
│   ├── config.py                 # 全局配置：路径、环境变量、沙箱规则
│   ├── tools.py                  # 基础笔记 CRUD 工具（11 个工具）+ 共享辅助函数
│   ├── search_tools.py           # 联网搜索与内容抓取工具（3 个工具）
│   ├── link_tools.py             # 双链分析与知识图谱工具（3 个工具）
│   ├── import_tools.py           # 多格式文件导入工具（3 个工具）
│   ├── summary_tools.py          # 笔记总结与知识图谱生成工具（3 个工具）
│   ├── skills_loader.py          # Obsidian Skills 加载器，注入系统提示词
│   └── audit.py                  # 审计日志系统（单例模式）
├── skills/                       # Obsidian Skills 知识库（来自 kepano/obsidian-skills）
│   ├── obsidian-markdown/        # Obsidian Markdown 语法技能
│   │   ├── SKILL.md
│   │   └── references/
│   │       ├── CALLOUTS.md
│   │       ├── EMBEDS.md
│   │       └── PROPERTIES.md
│   ├── obsidian-bases/           # Obsidian Bases 数据库技能
│   │   ├── SKILL.md
│   │   └── references/
│   │       └── FUNCTIONS_REFERENCE.md
│   ├── json-canvas/              # JSON Canvas 画布技能
│   │   ├── SKILL.md
│   │   └── references/
│   │       └── EXAMPLES.md
│   ├── obsidian-cli/             # Obsidian CLI 交互技能
│   │   └── SKILL.md
│   └── defuddle/                 # 网页内容提取技能
│       └── SKILL.md
└── vault/                        # Obsidian Vault 目录
    ├── 欢迎.md                   # 欢迎页
    ├── 模板/
    │   └── 每日模板.md           # 每日日记模板
    └── 项目/
        └── 项目索引.md           # 项目汇总页
```

运行时自动生成的目录：

```
vault/
├── attachments/                  # 附件（从 PDF/DOCX 提取的图片）
└── .knowledge_graph/             # 知识图谱输出（Mermaid/JSON）

logs/
├── audit/                        # 操作审计日志（JSONL 格式）
└── model_outputs/                # 模型输入输出记录（JSONL 格式）
```

---

## 模块详解

### `obsidian_agent/config.py` — 全局配置中心

从 `.env` 文件和环境变量中加载所有配置项，是整个项目的配置枢纽。`VAULT_PATH` 会在加载时自动 `.resolve()` 为绝对路径，避免 Claude CLI 沙箱路径检查失败。

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `VAULT_PATH` | Obsidian Vault 根目录（自动解析为绝对路径） | `./vault` |
| `ATTACHMENTS_PATH` | 附件存储目录 | `{VAULT_PATH}/attachments` |
| `LOG_DIR` | 日志根目录 | `./logs` |
| `AUDIT_DIR` | 审计日志目录 | `./logs/audit` |
| `MODEL_OUTPUT_DIR` | 模型输出日志目录 | `./logs/model_outputs` |
| `KNOWLEDGE_GRAPH_DIR` | 知识图谱输出目录 | `{VAULT_PATH}/.knowledge_graph` |
| `ALLOWED_EXTENSIONS` | 允许操作的笔记扩展名 | `.md`, `.markdown`, `.txt`, `.canvas` |
| `IMPORT_EXTENSIONS` | 允许导入的文件扩展名 | `.pdf`, `.docx`, `.doc`, `.txt`, `.rtf` |
| `SANDBOX_ENABLED` | 是否启用沙箱保护 | `true` |
| `SANDBOX_BLOCKED_PATTERNS` | 沙箱屏蔽的危险命令模式 | `rm -rf`, `sudo`, `chmod` 等 |
| `WEB_SEARCH_ENABLED` | 是否启用联网搜索 | `true` |
| `ANTHROPIC_BASE_URL` | API 基础 URL（支持本地模型） | 空 |
| `ANTHROPIC_AUTH_TOKEN` | API 认证 Token | 空 |
| `MODEL` | 使用的模型名称 | 空（使用 SDK 默认） |

---

### `obsidian_agent/agent.py` — Agent 核心

项目的核心模块，负责：

1. **工具注册**：将所有 5 个工具模块的 23 个工具汇总为 `ALL_REGISTERED_TOOLS`
2. **系统提示词**：定义 Agent 的角色、能力和操作规范（`SYSTEM_PROMPT`），包含 Skills 知识库
3. **Skills 注入**：通过 `skills_loader.build_skills_prompt()` 将 5 个 Obsidian Skills 知识注入系统提示词
4. **沙箱 Hook**（`sandbox_hook`）：在工具执行前拦截危险操作
   - 屏蔽 Bash 中的危险命令（`rm -rf`, `sudo`, `chmod` 等）
   - 屏蔽对敏感环境变量的访问（`ANTHROPIC_API_KEY`, `SECRET`, `PASSWORD` 等）
   - 监控 Write/Edit 操作的文件路径
5. **Agent 创建**（`create_agent`）：
   - 通过 `create_sdk_mcp_server` 创建 MCP 服务器，注册所有工具
   - 配置 `ClaudeAgentOptions`：系统提示词、MCP 服务器、允许/禁止的工具、权限模式
   - 设置 `PreToolUse` Hook 拦截链
   - 注入环境变量（`ANTHROPIC_BASE_URL`, `ANTHROPIC_AUTH_TOKEN`）
6. **交互式运行**（`run_interactive`）：REPL 交互循环
   - 支持自然语言输入
   - `quit`/`exit` 退出
   - `audit` 查看当前会话审计日志
   - 实时流式输出 Agent 响应和工具调用
   - 异常处理：query 和 response 阶段均有 try/except，不会因单次错误卡死
7. **单次运行**（`run_single`）：命令行单次查询模式
8. **入口函数**（`main`）：Windows UTF-8 重配置 + 根据命令行参数选择模式

---

### `obsidian_agent/tools.py` — 基础笔记 CRUD 工具

提供 11 个核心笔记管理工具，以及所有工具模块共享的辅助函数。

**共享辅助函数**（被 search_tools, link_tools, import_tools, summary_tools 导入使用）：
- `_resolve_path`：将相对路径解析为绝对路径，并检测路径遍历攻击
- `_parse_frontmatter`：解析 YAML frontmatter 为字典
- `_build_frontmatter`：从字典构建 YAML frontmatter 字符串
- `_read_note_file`：读取笔记文件，返回元数据和内容
- `_normalize_tags`：统一处理 `str` 和 `list` 两种标签输入格式
- `_strip_frontmatter_from_content`：剥离 LLM 在 content 中自带的 frontmatter，避免双重 frontmatter

**工具列表**：

| 工具名 | 功能 | 必填参数 | 可选参数 |
|--------|------|----------|----------|
| `list_notes` | 列出 Vault 中的笔记 | — | `folder`, `recursive` |
| `read_note` | 读取指定笔记内容和元数据 | `path` | — |
| `create_note` | 创建新笔记（含 frontmatter） | `path` | `content`, `tags`, `title` |
| `update_note` | 更新笔记内容/元数据 | `path` | `content`, `append`, `tags`, `title` |
| `delete_note` | 删除笔记 | `path` | — |
| `search_notes` | 全文搜索（支持正则） | `query` | `case_sensitive`, `search_in_metadata` |
| `move_note` | 移动/重命名笔记 | `source`, `destination` | — |
| `get_tags` | 获取所有标签及使用笔记 | — | — |
| `get_backlinks` | 获取反向链接 | `path` | — |
| `create_folder` | 创建文件夹 | `path` | — |
| `get_note_structure` | 获取 Vault 目录树 | — | `folder`, `depth` |

---

### `obsidian_agent/search_tools.py` — 联网搜索工具

提供 3 个联网搜索与内容抓取工具：

| 工具名 | 功能 | 必填参数 | 可选参数 |
|--------|------|----------|----------|
| `web_search` | 通过 DuckDuckGo API 搜索网页 | `query` | `max_results` |
| `web_fetch_content` | 抓取 URL 内容并提取纯文本 | `url` | `max_length` |
| `create_note_with_search` | 搜索网页并自动创建结构化笔记 | `path` | `topic`, `tags`, `title`, `max_search_results` |

---

### `obsidian_agent/link_tools.py` — 双链分析工具

提供 3 个 Obsidian 双向链接分析工具：

| 工具名 | 功能 | 必填参数 | 可选参数 |
|--------|------|----------|----------|
| `analyze_links` | 分析笔记的双向链接关系 | — | `path`, `include_unresolved` |
| `generate_link_graph` | 生成双链关系图谱（Mermaid/JSON） | — | `format`, `folder`, `link_type`, `max_nodes` |
| `find_orphan_notes` | 查找无入链无出链的孤立笔记 | — | `folder` |

---

### `obsidian_agent/import_tools.py` — 多格式导入工具

提供 3 个文件导入工具，支持 PDF、DOCX、TXT、RTF 格式：

| 工具名 | 功能 | 必填参数 | 可选参数 |
|--------|------|----------|----------|
| `import_file` | 导入单个文件为 Markdown 笔记 | `file_path` | `output_folder`, `extract_images` |
| `import_folder` | 批量导入文件夹中的文件 | `source_folder` | `output_folder`, `extract_images`, `recursive` |
| `extract_images` | 从 PDF/DOCX 提取图片到附件 | `file_path` | `output_folder` |

---

### `obsidian_agent/summary_tools.py` — 笔记总结与知识图谱工具

提供 3 个笔记总结和知识图谱生成工具：

| 工具名 | 功能 | 必填参数 | 可选参数 |
|--------|------|----------|----------|
| `summarize_notes` | 批量总结笔记，生成汇总文档 | — | `paths`, `folder`, `output_path`, `max_notes` |
| `generate_knowledge_graph` | 基于标签和链接生成知识图谱 | — | `format`, `folder`, `min_connections` |
| `find_related_notes` | 查找与指定笔记相关的笔记 | `path` | `max_results` |

---

### `obsidian_agent/skills_loader.py` — Obsidian Skills 加载器

从 `skills/` 目录加载 [kepano/obsidian-skills](https://github.com/kepano/obsidian-skills) 的全部技能，注入到 Agent 的系统提示词中。

| 函数 | 说明 |
|------|------|
| `load_skill(name)` | 加载单个 skill 的 SKILL.md 及其 references/ 目录下的参考文件 |
| `load_all_skills()` | 扫描 skills/ 目录，加载所有 skill，返回 `dict[name, content]` |
| `build_skills_prompt()` | 将所有 skills 组装为系统提示词片段，供 agent.py 注入 |

**加载流程**：
1. 扫描 `skills/` 下所有包含 `SKILL.md` 的子目录
2. 读取 `SKILL.md` 主文件
3. 如果存在 `references/` 目录，追加所有 `.md` 参考文件
4. 组装为 `## Obsidian Skills Knowledge Base` 段落注入系统提示词

---

### `obsidian_agent/audit.py` — 审计日志系统

基于单例模式的审计日志记录器，记录所有操作和模型交互。

| 方法 | 说明 |
|------|------|
| `log_operation` | 记录工具操作（工具名、输入、输出、耗时、状态） |
| `log_model_output` | 记录模型输入输出（prompt、response、token 用量） |
| `log_query` | 记录用户查询和响应摘要 |
| `get_session_log` | 获取当前会话的所有审计记录 |

**日志格式**：JSONL（每行一条 JSON 记录），按会话 ID 分文件存储。

---

## Obsidian Skills 详解

项目集成了 [kepano/obsidian-skills](https://github.com/kepano/obsidian-skills) 的全部 5 个专业技能。这些 Skills 以 Markdown 知识库的形式存在，在 Agent 启动时自动加载到系统提示词中，使 Agent 在创建和编辑 Obsidian 内容时遵循最佳实践。

| Skill | 说明 | 参考文件 |
|-------|------|----------|
| **obsidian-markdown** | Obsidian 风味 Markdown 语法：wikilinks、嵌入、callouts、properties 等 Obsidian 专有语法 | CALLOUTS.md, EMBEDS.md, PROPERTIES.md |
| **obsidian-bases** | Obsidian Bases 数据库：创建 `.base` 文件，支持视图、过滤器、公式和汇总 | FUNCTIONS_REFERENCE.md |
| **json-canvas** | JSON Canvas 画布：创建 `.canvas` 文件，支持节点、边、分组和连接 | EXAMPLES.md |
| **obsidian-cli** | Obsidian CLI 交互：通过命令行与 Obsidian 交互，包括插件和主题开发 | — |
| **defuddle** | 网页内容提取：使用 Defuddle 从网页中提取干净的 Markdown，去除杂乱内容节省 token | — |

**Skills 工作原理**：

```
skills/                          # 知识库目录
├── obsidian-markdown/
│   ├── SKILL.md                 # 主技能描述（自动加载）
│   └── references/              # 参考文档（自动追加）
│       ├── CALLOUTS.md
│       ├── EMBEDS.md
│       └── PROPERTIES.md
├── ...

skills_loader.py                 # 加载器
  │
  ├── load_all_skills()          # 扫描所有 SKILL.md
  ├── load_skill(name)           # 读取主文件 + references/
  └── build_skills_prompt()      # 组装为提示词片段
        │
        ▼
agent.py                         # 注入到 SYSTEM_PROMPT
  SYSTEM_PROMPT = """
  ...
  {skills_prompt}                # ← Skills 知识库在此注入
  """
```

**添加新 Skill**：只需在 `skills/` 目录下创建新文件夹，放入 `SKILL.md`，可选添加 `references/` 目录，Agent 下次启动时自动加载。

---

## 工具总览

项目共注册 **23 个 MCP 工具**，分为 5 个模块：

| 模块 | 工具数 | 工具列表 |
|------|--------|----------|
| **tools.py** (基础 CRUD) | 11 | list_notes, read_note, create_note, update_note, delete_note, search_notes, move_note, get_tags, get_backlinks, create_folder, get_note_structure |
| **search_tools.py** (联网搜索) | 3 | web_search, web_fetch_content, create_note_with_search |
| **link_tools.py** (双链分析) | 3 | analyze_links, generate_link_graph, find_orphan_notes |
| **import_tools.py** (文件导入) | 3 | import_file, import_folder, extract_images |
| **summary_tools.py** (笔记总结) | 3 | summarize_notes, generate_knowledge_graph, find_related_notes |

此外，Agent 还被允许使用 SDK 内置的 `Read` 和 `Glob` 工具，但禁止使用 `Bash` 工具。

**工具 Schema 设计**：所有工具使用 `TypedDict` + `NotRequired` 定义参数，SDK 会正确区分必填和可选参数，避免 LLM 传参时因缺少可选参数而被验证拒绝。

---

## 安全机制

### 沙箱保护

- **路径遍历防护**：所有文件操作通过 `_resolve_path` 解析，确保操作路径不超出 Vault 范围
- **危险命令屏蔽**：沙箱 Hook 拦截 `rm -rf`, `sudo`, `chmod`, `shutdown` 等危险命令
- **敏感信息保护**：屏蔽对 `ANTHROPIC_API_KEY`, `SECRET`, `PASSWORD`, `TOKEN` 等环境变量的访问
- **Bash 工具禁用**：在 Agent 配置中直接禁止使用 Bash 工具
- **权限模式**：使用 `acceptEdits` 模式，允许编辑操作但受 Hook 约束

### 审计追踪

所有操作记录到 `logs/audit/` 目录，包含：
- 操作时间、会话 ID
- 工具名称、输入参数、输出结果
- 操作耗时、执行状态
- 用户查询和模型响应

### 编码安全

- **Windows UTF-8 重配置**：启动时将 stdout/stderr 重配置为 UTF-8 + `errors="replace"`
- **`_safe_print()`**：捕获 `UnicodeEncodeError`，将无法编码的字符替换而非崩溃
- **双重 frontmatter 防护**：自动剥离 LLM 在 content 中自带的 frontmatter

---

## 启动方式

### 1. 环境准备

```bash
# 克隆项目
cd Agent

# 创建虚拟环境（推荐）
python -m venv .venv

# 激活虚拟环境
# Windows:
.venv\Scripts\activate
# Linux/macOS:
source .venv/bin/activate

# 安装依赖
pip install -r requirements.txt
```

### 2. 配置环境变量

复制 `.env.example` 为 `.env` 并填写配置：

```bash
cp .env.example .env
```

编辑 `.env` 文件：

```ini
# 必填：Anthropic API Key（使用官方 API 时）
ANTHROPIC_API_KEY=sk-ant-xxxxx

# 可选：使用本地模型（如 LM Studio、Ollama 等）
ANTHROPIC_BASE_URL=https://localhost:1234
ANTHROPIC_AUTH_TOKEN=lmstudio
MODEL=qwen3.6-35b-a3b

# Vault 路径（默认 ./vault，自动解析为绝对路径）
VAULT_PATH=./vault

# 沙箱开关（默认 true）
SANDBOX_ENABLED=true

# 联网搜索开关（默认 true）
WEB_SEARCH_ENABLED=true
```

**配置说明**：

| 场景 | ANTHROPIC_BASE_URL | ANTHROPIC_AUTH_TOKEN | MODEL |
|------|-------------------|---------------------|-------|
| 官方 Claude API | 留空 | 留空 | 留空（默认 Claude） |
| LM Studio 本地模型 | `https://localhost:1234` | `lmstudio` | 模型名称 |
| 其他 OpenAI 兼容 API | API 地址 | API Key | 模型名称 |

### 3. 启动 Agent

```bash
# 交互式模式（推荐）
python -m obsidian_agent

# 单次查询模式
python -m obsidian_agent "帮我创建一篇关于机器学习的笔记"
```

### 4. 交互式使用

启动后进入 REPL 交互界面：

```
============================================================
  Obsidian Note Management Agent
  Powered by Claude Agent SDK
  Vault: D:\za\Note\Agent\vault
  Sandbox: ON
  Web Search: Enabled
  Tools: 23
  Skills: 5 (obsidian-markdown, obsidian-bases, json-canvas, obsidian-cli, defuddle)
  Session: 20260427_120000_abc12345
============================================================

Commands:
  Type your request in natural language
  'quit' or 'exit' to stop
  'audit' to view session audit log

[You]: 
```

**示例交互**：

```
[You]: 帮我创建一篇关于深度学习的笔记，标签加上 AI 和技术

[You]: 搜索所有包含 Python 的笔记

[You]: 查看 vault 的目录结构

[You]: 分析所有笔记的双链关系

[You]: 导入 D:/documents/report.pdf 到 vault

[You]: 批量总结项目文件夹下的所有笔记

[You]: 创建一个 JSON Canvas 画布，展示项目笔记之间的关系

[You]: audit
```

---

## 依赖说明

| 依赖包 | 版本要求 | 用途 |
|--------|----------|------|
| `claude-agent-sdk` | >=0.1.0 | Claude Agent SDK 核心，提供 Agent 运行时和 MCP 工具注册 |
| `python-dotenv` | >=1.0.0 | 从 `.env` 文件加载环境变量 |
| `anyio` | >=4.0.0 | 异步 I/O 支持 |
| `PyMuPDF` | >=1.24.0 | PDF 解析和图片提取（fitz） |
| `python-docx` | >=1.1.0 | DOCX 文件解析和图片提取 |
| `striprtf` | >=0.0.26 | RTF 格式转纯文本 |

> **注意**：PyMuPDF 和 python-docx 是可选依赖。如果未安装，对应的导入功能会返回提示信息而不会崩溃。

---

## 数据流

```
用户输入
  │
  ▼
agent.py (run_interactive / run_single)
  │
  ▼
ClaudeSDKClient.query()
  │
  ▼
Claude 模型 ──→ 选择工具调用
  │                    │
  │              PreToolUse Hook (sandbox_hook)
  │                    │
  │              ┌─────┴─────┐
  │              │ 允许/拒绝  │
  │              └─────┬─────┘
  │                    │
  ▼                    ▼
MCP Server (obsidian) ──→ 执行工具函数
  │
  ├── tools.py        → 笔记 CRUD
  ├── search_tools.py → 联网搜索
  ├── link_tools.py   → 双链分析
  ├── import_tools.py → 文件导入
  └── summary_tools.py→ 笔记总结
        │
        ▼
  audit.py (记录操作日志)
        │
        ▼
  返回结果 → Claude 继续推理 → 输出响应

系统提示词构成：
  ┌─────────────────────────────┐
  │ Agent 角色与能力描述         │
  │ 操作指南与规范              │
  │ Vault 路径信息              │
  ├─────────────────────────────┤
  │ Obsidian Skills Knowledge   │  ← skills_loader.py 自动注入
  │ ├── obsidian-markdown       │
  │ ├── obsidian-bases          │
  │ ├── json-canvas             │
  │ ├── obsidian-cli            │
  │ └── defuddle                │
  └─────────────────────────────┘
```
