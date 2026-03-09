# Agent 组成模版说明

> 基于 `structure.md` 中的即兴 TRPG Agent 设计，提炼出的通用 Agent 组成模版。  
> 适用于从未设计过 Agent 的开发者理解「一个 Agent 由什么构成」。

---

## 核心概念：Agent = 感知 → 思考 → 行动 → 记忆

一个 Agent 可以抽象为四个环节的循环，structure 中的每个模块都对应其中一环。

---

## 1. 感知层：Agent 如何「看见」世界？

Agent 需要知道：当前发生了什么、世界长什么样、玩家说了什么。

在 structure 中，这对应 **Context Builder**：

```
Context Builder 负责：
├── Session Contract（题材、风格、边界）
├── Current Scene Brief（当前地点、在场角色、最近事件）
├── Canon Facts（已确立的事实，Top-K 检索）
├── Active Threads（进行中的任务、悬念）
├── Recent Transcript（最近 N 轮对话）
└── GM Notes（可选，秘密信息，不直接给玩家）
```

**作用**：把「世界状态 + 玩家输入」整理成一段上下文，喂给 LLM。相当于 Agent 的「眼睛」。

---

## 2. 思考层：Agent 如何「做决策」？

Agent 要根据感知到的信息，决定接下来要做什么。

在 structure 中，这对应 **LLM Adapter + 输出协议**：

```
LLM 每回合输出：
├── narrative（给玩家看的叙事文本）
└── proposals[]（结构化「行动提案」）
    ├── CreateEntity（创建新地点/NPC/物品）
    ├── AddFact（添加事实）
    ├── AddSecret（添加秘密）
    ├── RequestCheck（发起检定）
    ├── RequestRoll（掷骰）
    └── ...
```

**作用**：LLM 是「大脑」，负责理解情境、生成叙事、提出要执行的动作。但它只输出「提案」，不直接改世界。

---

## 3. 行动层：Agent 如何「动手」？

Agent 要把「决策」变成对世界的实际修改。

在 structure 中，这对应 **Validator + Rules Engine + Commit**：

```
Propose → Validate → Commit 流程：

1. Validator（校验）
   ├── 引用合法吗？（提到的 entity 存在吗？）
   ├── 一致吗？（同一人不能同时在两个地方）
   └── 权限对吗？（LLM 不能直接改骰子结果）

2. Rules Engine（规则执行）
   ├── 掷骰（d20 + modifier vs dc）
   ├── 结算检定结果
   └── 资源变化（可复现的 RNG）

3. Commit（落盘）
   ├── 写入 Event Log
   └── 更新 Canon Store（entities、facts、secrets）
```

**作用**：Validator 是「把关」，Rules Engine 是「执行规则」，Commit 是「真正写入状态」。这样 LLM 不能乱改世界，只能通过提案 + 校验 + 执行来行动。

---

## 4. 记忆层：Agent 如何「记住」？

Agent 需要持久化存储，否则每轮都是「失忆」状态。

在 structure 中，这对应 **Canon Store + Event Log**：

```
Canon Store（世界真相）
├── Entity Registry（地点、NPC、物品、任务…）
├── Fact Store（事实，带真相等级：canon/rumor/hypothesis）
└── Secrets（秘密，带揭示条件）

Event Log（事件溯源）
├── 所有变更都写事件
├── 状态可由事件重放得到
└── 支持分支、回滚、调试
```

**作用**：Canon Store 是「长期记忆」，Event Log 是「完整历史」。两者一起保证世界状态可追溯、可恢复。

---

## 5. 调度层：Orchestrator 把各层串起来

**Orchestrator** 负责按顺序调用各模块，形成一回合的完整流程：

```
每回合循环：
1. 玩家输入 → player_input
2. Context Builder → 构建 context_bundle
3. LLM Adapter → 调用 LLM，得到 narrative + proposals
4. Validator → 校验 proposals
5. Rules Engine → 执行检定/掷骰
6. Commit → 写入 Event Log，更新 Canon Store
7. 输出 → narrative + 系统结果（检定成功/失败等）
8. 回到步骤 1，等待下一轮玩家输入
```

---

## 6. 辅助模块：修补与约束

| 模块 | 作用 |
|------|------|
| **Narrative Patcher** | 防止 LLM 在叙事里「偷渡」新实体（只写进 narrative 却没 CreateEntity），把叙事改得更模糊或要求补提案 |
| **Truth Model** | 区分 canon / rumor / hypothesis，让 LLM 可以「留白」，不必强行补全 |
| **Secrets + Reveal Rules** | 秘密只按条件揭示，避免 LLM 直接剧透 |

---

## 架构总览图

```
                    ┌─────────────────┐
                    │   玩家输入       │
                    └────────┬────────┘
                             ▼
┌──────────────────────────────────────────────────────────────┐
│                     ORCHESTRATOR（回合循环）                    │
│                                                              │
│  ┌─────────────┐    ┌─────────────┐    ┌─────────────────┐   │
│  │ 感知        │    │ 思考        │    │ 行动             │   │
│  │ Context     │───▶│ LLM         │───▶│ Validator       │   │
│  │ Builder     │    │ Adapter     │    │ Rules Engine    │   │
│  └──────┬──────┘    └─────────────┘    └────────┬────────┘   │
│         │                      ▲                │             │
│         │                      │                ▼             │
│         │               ┌──────┴──────┐  ┌─────────────┐     │
│         │               │ 记忆        │  │ Commit      │     │
│         └──────────────▶│ Canon Store │◀─│ Event Log   │     │
│                         │ Event Log   │  └─────────────┘     │
│                         └─────────────┘                      │
└──────────────────────────────────────────────────────────────┘
                             ▼
                    ┌─────────────────┐
                    │ 叙事 + 系统结果   │
                    └─────────────────┘
```

---

## 总结：Agent 的通用组成

| 组成 | 在 structure 中的对应 | 通用含义 |
|------|------------------------|----------|
| **感知** | Context Builder | 收集、整理输入和状态 |
| **思考** | LLM Adapter | 理解、推理、生成决策 |
| **行动** | Validator + Rules Engine + Commit | 校验、执行、更新状态 |
| **记忆** | Canon Store + Event Log | 持久化、可追溯 |
| **调度** | Orchestrator | 串联各模块的流程 |
| **约束** | Narrative Patcher、Truth Model、Secrets | 限制 LLM 行为、保证一致性 |

---

## 参考

- 详细设计文档：[structure.md](./structure.md)
