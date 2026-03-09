# Improvised TRPG 本地 RPG Agent 设计文档（Empty Framework / v0）

> 核心理念：不预设剧本、不预设地图/NPC/主线；由玩家与 LLM 共同即兴创作。  
> 系统职责：**管理“世界真相（canon）”**、**保证一致性**、**把 LLM 的即兴内容结构化落盘**、**数值与检定可复现**。

---

## 0. 设计目标（与传统“预设剧本跑团”不同点）

### 0.1 目标
- “开局一片空白”：世界、人物、冲突、秘密都可以在互动中生成。
- LLM 扮演 GM / 叙事者：负责描述、扮演 NPC、提出可能的世界元素与推进方向。
- 系统作为“规则 + 史官（Canon Keeper）”：  
  - 统一管理世界真相（哪些信息已经成为事实）
  - 防止自相矛盾
  - 负责掷骰/数值变化/事件日志
  - 负责“秘密信息”的可见性控制（避免剧透）

### 0.2 非目标（v0 不做）
- 不做大型图形界面（先 TUI / 简单 Web）
- 不做多人联机
- 不做超复杂战斗体系（先轻量检定 + 叙事战斗）

---

## 1. 核心架构：把“即兴创作”变成“可控写入”

### 1.1 三层分离（必须）
- **叙事层（Narrative / LLM）**：生成文本、扮演、提出“世界新增/修改提案”
- **规则层（Rules Engine）**：掷骰、检定、战斗结算、资源变化（可复现）
- **状态层（Canon Store）**：世界与角色的唯一真相（single source of truth）

> LLM **不能直接改状态**。LLM 只能“提案（proposal）”，系统校验后“提交（commit）”。

### 1.2 两阶段提交：Propose -> Validate -> Commit
每回合：
1) 玩家输入 `player_input`
2) Context Builder 构建上下文（只喂必要信息）
3) LLM 输出：
   - `narrative`：给玩家看的叙事
   - `proposals[]`：结构化提案（新增地点/NPC/事实/秘密/检定请求等）
4) 系统校验（Validator）：
   - schema 合法性
   - 一致性（时间/地点/归属/角色存在性）
   - 权限（LLM 是否越权）
5) 规则引擎执行（骰子/结算）
6) Commit：写入 Event Log，并更新 Canon Store（或重放得到）
7) 输出：叙事 + 系统结果（检定成功/失败、获得物品等）

---

## 2. “空白世界”的关键：真相模型（Truth Model）

即兴创作时，不是所有陈述都应立即成为“事实”。  
因此引入 **真相等级**（epistemic status）：

- **CanonFact（已确立事实）**：系统承认、可约束后续一致性
- **Rumor（传闻/他人说法）**：可用于叙事，但不强约束
- **Hypothesis（推测/猜测）**：可被证实或证伪
- **Unknown（未知）**：显式保留空白，不强行补全

> 作用：让 LLM 可以“留白”，避免为了连贯而胡编导致世界后续无法自洽。

---

## 3. 数据与存储（空框架也要能落盘）

### 3.1 存储建议
- SQLite：`events`, `snapshots`, `entities`, `facts`, `secrets`
- world schema：`schemas/`（JSON Schema 或 pydantic 模型）

### 3.2 事件溯源（Event Sourcing）
- **所有变更写事件日志**，世界状态可由事件重放得到
- 定期 snapshot 加速恢复

通用事件字段：
- `event_id` UUID
- `turn_id`
- `ts`
- `type`
- `payload`
- `caused_by`: `player | system | llm`
- `visibility`: `public | gm_only`（用于秘密/暗骰）

---

## 4. 实体模型：从“空”到“有”的最小集合

### 4.1 Entity Registry（实体注册表）
任何新出现的“东西”（地点/NPC/物品/组织/任务）必须有：
- `id`（系统生成，稳定引用）
- `type`
- `display_name`
- `lore`（相对稳定的设定）
- `state`（会变化的状态）
- `tags`（检索用）

v0 实体类型建议：
- `player`
- `npc`
- `location`
- `item`
- `faction`（可选）
- `quest`（可选）

### 4.2 Fact Store（事实库）
事实以三元组或结构化形式存储，并带真相等级与来源：

字段建议：
- `fact_id`
- `subject_id` / `predicate` / `object`（或结构化 payload）
- `status`: `canon | rumor | hypothesis`
- `source`: `player | llm | system`
- `visibility`: `public | gm_only`
- `evidence_event_ids[]`（哪些事件支持该事实）
- `conflicts_with[]`（冲突事实引用）

> Fact 与 Entity 分离：实体是“名词”，事实是“关于名词的陈述”。

---

## 5. Secrets（GM 私密剧情）在即兴模式下怎么存在？

### 5.1 秘密不是预设“剧情”，而是“私密事实集合”
LLM 在即兴过程中可以提出秘密（例如：某 NPC 的真实动机、某地点隐藏入口），但秘密默认：
- `visibility = gm_only`
- `status = canon` 或 `hypothesis`（取决于是否“确立”）

### 5.2 Reveal Rules（揭示规则）
每个 secret 必须带“揭示条件”，只有条件满足时系统才允许向玩家输出 `SecretRevealed` 事件。

揭示条件参考的对象只能是：
- 已存在的 facts
- quest state
- 已发生的事件类型
- 检定结果（成功/失败）

> 这样秘密可以“动态生成”，但揭示仍由系统控制，避免 LLM 直接剧透。

---

## 6. LLM 输出协议（空框架的核心约束）

LLM 每回合必须输出 JSON：
- `narrative`: string
- `proposals`: list

### 6.1 Proposal 类型（v0 必备）
1) `CreateEntity`
2) `UpdateEntity`
3) `AddFact`
4) `AddSecret`
5) `RequestCheck`（发起检定）
6) `RequestRoll`（暗骰/明骰）
7) `AdvanceClock`（可选：推进威胁/悬念进度条）
8) `RetconRequest`（可选：请求回滚/修正，需要玩家确认）

#### 示例（仅结构示意，不带剧本内容）
```json
{
  "narrative": "（面向玩家的叙事文本）",
  "proposals": [
    {
      "type": "CreateEntity",
      "entity_type": "location",
      "temp_name": "老旧酒馆",
      "fields": {
        "display_name": "老旧酒馆",
        "lore": { "vibe": "昏黄、潮湿、低语不断" },
        "state": { "interactables": ["吧台", "壁炉"] }
      }
    },
    {
      "type": "AddFact",
      "status": "rumor",
      "visibility": "public",
      "subject": "location:老旧酒馆",
      "predicate": "locals_say",
      "object": "这里曾发生过失踪事件"
    },
    {
      "type": "RequestCheck",
      "actor_id": "player",
      "skill": "Perception",
      "dc": 12,
      "reason": "察觉酒馆里是否有人在监视你"
    }
  ]
}
```

### 6.2 重要规则：叙事不得“偷渡新增”

如果 LLM narrative 里提到“新 NPC/新地点/新物品”，必须同时在 proposals 中出现对应 CreateEntity 或 AddFact。

否则系统应触发 Narrative Patcher：把叙事改写成更模糊、不引入新实体的版本（例如“一个陌生人”而非“叫X的猎人”）。

---

## 7. 校验与一致性（即兴模式最容易崩的地方）

### 7.1 Validator 校验项（v0 必做）

- 引用合法：actor/location/item id 必须存在（或被本轮 CreateEntity 创建）
- schema 合法：字段类型/必填项
- 一致性：
  - 同一实体不能同时在两个地点
  - 物品归属唯一
  - 玩家状态变化必须有事件依据
- 权限：
  - LLM 不能直接设置数值结果（如“你掷骰=20”）
  - LLM 不能直接把 secret 改为 public（必须由系统触发 SecretRevealed）

### 7.2 冲突处理（Truth Maintenance）

当新增 CanonFact 与已有 CanonFact 冲突：

- 默认：拒绝 commit，要求 LLM 给出修正提案（或降级为 rumor/hypothesis）
- 或：显式产生 ConflictDetected 事件，并由 GM（LLM）在叙事中解释“认知差异/谣言/视角问题”

---

## 8. 规则引擎（轻规则但要稳）

### 8.1 v0 轻规则建议

- d20 检定：d20 + modifier >= dc
- modifier 来自 player sheet（属性/技能）
- 失败也要有后果（fail forward）：推进 clock、暴露线索、资源消耗等

### 8.2 RNG 可复现

- 所有骰子由系统掷：rng(seed, event_id)
- 记录：roll、mods、dc、outcome
- 支持明骰/暗骰（暗骰结果不直接给玩家，仅影响后果）

---

## 9. Context Builder（空世界更需要“记忆与真相检索”）

### 9.1 上下文分块（强烈建议）

- Session Contract：题材/边界/风格（可在开局由玩家选择）
- Current Scene Brief：当前地点、在场实体、最近发生的关键事件
- Canon Facts（Top-K）：与当前话题相关的已确立事实
- Active Threads：进行中的 clocks / quests / unresolved mysteries（不要求预设，只要“已出现的线索”）
- Recent Transcript：最近 N 轮
- GM Notes（可选、gm_only）：secrets 的最小必要信息（避免剧透倾向）

### 9.2 检索策略

- 以“当前回合关键词 + 在场实体 id”为 query，检索 facts/entities/secrets（gm_only）
- 限制注入量，避免上下文爆炸

---

## 10. 开局（Empty World Bootstrapping）

不预设剧本，但需要一个“开局协议”让玩家与 LLM对齐创作方向。

### 10.1 Session Zero（建议，但不强制）

玩家选择/输入：

- 题材：奇幻/赛博/克苏鲁/武侠/都市怪谈...
- 风格：轻松/严肃/黑色/喜剧/高危险...
- 边界：不想出现的内容（可选）

系统将其写入 SessionContract（canon）

### 10.2 第一幕触发

- 玩家一句“开始”
- LLM 提出 1~3 个“开场画面提案”（不是剧情，而是舞台与张力）
- 系统提交最小必要实体：start_location、player、若干可互动对象（可匿名/模糊）

仍然不等于预设剧本：只是创建“舞台”，剧情由互动生成。

---

## 11. Debug 与可视化（必备，不然你很快不知道哪里坏了）

### 11.1 必备调试输出

- 本轮注入给 LLM 的 context_bundle（可折叠）
- LLM 原始 JSON 输出
- proposals 校验通过/失败原因
- 本轮事件列表
- 状态 diff（entities/facts/secrets 变化）

### 11.2 分支与回滚

- 允许从任意 turn_id 创建新分支（fork）
- 允许玩家显式 Retcon（需确认，生成 RetconCommitted 事件）

---

## 12. 工程结构（Python 推荐）

```text
app/
  orchestrator.py        # 回合循环
  llm_adapter.py         # provider 适配、重试、JSON 强制
  context_builder.py     # 检索与拼装
  validator.py           # schema + 一致性 + 权限
  rules_engine.py        # 检定/掷骰/资源结算
  canon_store.py         # entities/facts/secrets CRUD
  event_log.py           # append + replay + snapshot
  narrative_patcher.py   # 修复“叙事偷渡新增”
  ui_tui.py              # (or web/)
schemas/
data/                    # sqlite + snapshots
tests/
  test_replay.py
  test_validator.py
  test_secrets_gate.py
```

---

## 13. v0 验收标准（面向“即兴创作”）

- 允许从空白启动并顺畅推进 ≥ 30 回合
- 世界新增元素（地点/NPC/物品/事实）全部通过 proposals 提交并可追溯
- 事件重放可恢复一致状态
- 至少一次秘密创建 + 至少一次按条件揭示（无剧透）
- 任意回合出现矛盾时，系统能检测并阻止 commit 或降级为 rumor/hypothesis
- Debug 面板能定位“哪一回合引入了哪个事实/实体”
