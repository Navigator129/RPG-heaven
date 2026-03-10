# Source Code — Improvised TRPG Agent

即兴 TRPG 本地 RPG Agent 的全部源码。系统核心采用**三层分离**架构，外加编排/胶水层和终端界面。

---

## 使用 / 开局流程

### 1. 安装依赖

```bash
cd source_code
pip install -r requirements.txt
```

需要 Python 3.10+，并保证可以访问一个 OpenAI 兼容的 Chat Completion 接口。

### 2. 配置 LLM

```bash
export OPENAI_API_KEY="your-key-here"
# 如使用其他 OpenAI 兼容端点（如自建 vLLM、Ollama 等）：
# export OPENAI_BASE_URL="https://your-compatible-endpoint"
```

### 3. 启动游戏

**方式 A：终端 TUI**

```bash
cd source_code
python3 ui_tui.py
```

**方式 B：Web UI**

```bash
cd source_code
python3 app.py
```

默认在 **5001** 端口启动（避免与 macOS 上占用 5000 的 AirPlay Receiver 冲突）。在浏览器打开 http://127.0.0.1:5001，界面有 **开始** / **载入** / **退出** 三个按钮。选择开始后，上半部分为 AI 叙事，下半部分为玩家输入框。

如需改用其他端口，可设置环境变量 `FLASK_PORT`，例如：

```bash
FLASK_PORT=8080 python3 app.py
```

### 4. 存档槽

每次开始前需输入**存档名**，不同存档的故事、数据、世界观完全隔离，互不影响：

- 数据存放在 `data/<存档名>/rpg.db`
- TUI：可选择「新游戏」或「载入」，新游戏输入存档名，载入从列表选择
- Web：开始需填写存档名 + 题材/风格，载入从列表点击已有存档

### 5. TUI 流程与 Session Zero

启动 TUI 后会进入 **Session Zero**（世界设定）：

- 选择题材：奇幻 / 赛博 / 克苏鲁 / 武侠 / 都市怪谈 / ...
- 选择风格：轻松 / 严肃 / 黑色 / 喜剧 / 高危险 / ...
- 设置边界：不想出现的内容（可留空）

之后每回合在提示符下输入你的行动（自然语言），系统会：

- 基于当前世界真相和事件日志构建上下文
- 调用 LLM 生成叙事与结构化提案
- 通过规则层进行掷骰 / 检定与一致性校验
- 更新 Canon Store 与 Event Log，并输出叙事与系统消息

常用命令（在 `> 你的行动:` 下输入）：

- `/help`：查看命令列表
- `/debug`：打开/关闭调试面板
- `/state`：查看当前实体列表
- `/facts`：查看已确立的事实
- `/save`：手动保存快照
- `/quit`：退出

---

## 架构总览

```
┌────────────────────────────────────────────────────────┐
│                     UI / 交互层                         │
│                    ui_tui.py                            │
└──────────────────────┬─────────────────────────────────┘
                       │ 玩家输入 / 叙事输出
┌──────────────────────▼─────────────────────────────────┐
│                   编排层 (Orchestration)                 │
│                  orchestrator.py                        │
│        ┌─────────────┼──────────────┐                  │
│        │             │              │                   │
│  context_builder  validator  narrative_patcher          │
│        │             │              │                   │
└────────┼─────────────┼──────────────┼──────────────────┘
         │             │              │
┌────────▼─────┐ ┌─────▼──────┐ ┌────▼───────────┐
│   叙事层      │ │  规则层     │ │   状态层        │
│ (Narrative)  │ │  (Rules)   │ │  (Canon)       │
│              │ │            │ │                │
│ llm_adapter  │ │ rules_engine│ │ canon_store    │
│              │ │ validator  │ │ event_log      │
└──────────────┘ └────────────┘ └────────────────┘
         │             │              │
         └─────────────┼──────────────┘
                       │
              ┌────────▼────────┐
              │   数据模型层     │
              │   models.py     │
              └─────────────────┘
```

---

## 各层说明

### Layer 0 — 数据模型层 (`models.py`)

所有层共享的 Pydantic 数据定义，是整个系统的"通用语言"。

| 内容 | 说明 |
|------|------|
| 枚举类型 | `EpistemicStatus`, `Visibility`, `CausedBy`, `EntityType` |
| 核心领域模型 | `Entity`, `Fact`, `Secret`, `GameEvent`, `SessionContract`, `CheckResult` |
| 提案协议 | `CreateEntityProposal`, `UpdateEntityProposal`, `AddFactProposal`, `AddSecretProposal`, `RequestCheckProposal`, `RequestRollProposal`, `AdvanceClockProposal`, `RetconRequestProposal` |
| LLM 响应 | `LLMResponse` — 包含 `narrative` + `proposals[]` 的结构化输出 |

---

### Layer 1 — 状态层 (State Layer)

世界的**唯一真相来源 (Single Source of Truth)**。LLM 不能直接写入，必须经过校验。

| 文件 | 职责 |
|------|------|
| `canon_store.py` | 实体/事实/秘密的 CRUD，SQLite 后端。提供检索、冲突查询、快照导入导出 |
| `event_log.py` | 事件溯源 (Event Sourcing)：所有变更写入 append-only 日志，支持重放、快照、分支 |

**关键设计**：
- 真相等级 (`EpistemicStatus`)：`canon` / `rumor` / `hypothesis` / `unknown`
- 事实与实体分离：实体是"名词"，事实是"关于名词的陈述"
- 秘密 (`Secret`) 带揭示条件，只有系统可触发 `SecretRevealed`

---

### Layer 2 — 规则层 (Rules Layer)

负责所有**可复现**的机械判定，不依赖 LLM。

| 文件 | 职责 |
|------|------|
| `rules_engine.py` | d20 检定系统、骰子解析与投掷、确定性 RNG (`sha256(seed + event_id)`)、技能修正值计算 |
| `validator.py` | 提案校验：schema 合法性、引用合法性、一致性检查（同实体不能在两地）、权限检查（LLM 不可设置骰值/揭示秘密/修改数值） |

**关键设计**：
- 所有骰子由系统投掷，种子化保证结果可复现
- 支持明骰/暗骰（暗骰结果仅 GM 可见）
- 批量校验允许同一回合内的交叉引用（先 CreateEntity 后 AddFact）

---

### Layer 3 — 叙事层 (Narrative Layer)

LLM 扮演 GM，生成文本并提出世界变更提案。

| 文件 | 职责 |
|------|------|
| `llm_adapter.py` | OpenAI 兼容 API 调用、JSON 强制输出、重试逻辑、系统 prompt 管理 |
| `narrative_patcher.py` | 检测叙事中"偷渡"的新实体名称（无对应 `CreateEntity` 提案），替换为匿名描述 |

**关键设计**：
- LLM 每回合输出 `{ "narrative": "...", "proposals": [...] }`
- 叙事中提到的新实体必须在 proposals 中有对应的 CreateEntity
- Narrative Patcher 使用启发式规则检测引号名、"叫XX"等模式

---

### Layer 4 — 编排层 (Orchestration Layer)

协调所有子系统，实现**两阶段提交** (Propose → Validate → Commit) 回合循环。

| 文件 | 职责 |
|------|------|
| `orchestrator.py` | 主循环：玩家输入 → 上下文构建 → LLM 调用 → 校验 → 规则执行 → 提交 → 输出。还负责 Session Zero 开局、秘密揭示检查、分支/回滚 |
| `context_builder.py` | 为 LLM 组装上下文：Session Contract、当前场景、相关事实 (Top-K)、活跃线索、最近对话记录、GM 备忘 |

**关键设计**：
- 每 10 回合自动保存快照
- 支持从任意回合创建分支 (`fork_from_turn`)
- 上下文按关键词检索，限制注入量避免 token 爆炸

---

### Layer 5 — 交互层 (UI Layer)

面向玩家的终端界面。

| 文件 | 职责 |
|------|------|
| `ui_tui.py` | 基于 `rich` 的终端 UI：Session Zero 引导、叙事面板、系统消息、Debug 面板（可折叠）、斜杠命令 (`/debug`, `/state`, `/facts`, `/save`, `/quit`) |

---

## 文件清单

```
source_code/
├── app.py                # Web UI (Flask)
├── templates/
│   └── index.html        # Web 主页面
├── models.py              # Layer 0: 数据模型
├── canon_store.py         # Layer 1: 状态层 — 实体/事实/秘密 CRUD
├── event_log.py           # Layer 1: 状态层 — 事件溯源
├── rules_engine.py        # Layer 2: 规则层 — 检定/掷骰
├── validator.py           # Layer 2: 规则层 — 提案校验
├── llm_adapter.py         # Layer 3: 叙事层 — LLM 适配
├── narrative_patcher.py   # Layer 3: 叙事层 — 叙事修复
├── orchestrator.py        # Layer 4: 编排层 — 主循环
├── context_builder.py     # Layer 4: 编排层 — 上下文构建
├── save_utils.py          # 存档槽工具 — 按存档名隔离 data/<name>/
├── ui_tui.py              # Layer 5: 交互层 — 终端界面
├── requirements.txt       # Python 依赖
├── data/                  # SQLite 数据库 & 快照（运行时生成）
└── README.md              # 本文档
```

---

## 核心数据流（每回合）

```
玩家输入
    │
    ▼
ContextBuilder.build()  ──→  拼装 prompt 上下文
    │
    ▼
LLMAdapter.call()       ──→  { narrative, proposals[] }
    │
    ▼
Validator.validate()    ──→  schema + 一致性 + 权限
    │
    ▼
NarrativePatcher.patch()──→  检测叙事偷渡
    │
    ▼
RulesEngine.resolve()   ──→  掷骰 / 检定
    │
    ▼
CanonStore.commit()     ──→  写入实体/事实/秘密
EventLog.append()       ──→  记录事件
    │
    ▼
SecretRevealCheck       ──→  检查是否有秘密达成揭示条件
    │
    ▼
UI 输出：叙事 + 系统消息 + Debug 面板
```

---

## License

本项目源代码采用 **Apache License 2.0** 授权。  
详情见仓库根目录下的 `LICENSE` 文件。
