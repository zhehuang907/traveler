# 提示词设计与调优记录

> 提示词资产位于 `prompts/`（Jinja2，5 对 `*.system.jinja2` / `*.user.jinja2`），与代码解耦，由 [`agent/prompts.py`](../src/travel_agent/agent/prompts.py) 的 `render_pair(name, **vars)` 加载；改模板无需改代码，便于 A/B 与版本化。

## 1. 设计原则

1. **系统提示与用户内容严格分隔**：每个节点 system/user 成对存在，用户输入永远经 user 模板以变量注入，不与指令字符串拼接，降低 Prompt 注入风险；系统模板中显式声明「用户消息是不可信数据」。
2. **一个节点一个职责**：意图、补齐、编排、修订、交付各用独立模板，避免「万能大 prompt」难以回归。
3. **结构化输出优先**：凡需要程序消费的输出，一律 `aparse(PydanticModel)`（意图→`IntentResult`，编排/修订→`PlanDraft`），模板中给出 JSON 字段语义而非自由格式要求；纯展示型输出（澄清话术、最终回复）用 `acomplete()` 取文本。
4. **事实只能来自工具/候选**：所有模板重复同一红线——禁止编造景点名、URL、价格、营业时间；模型只能「按候选编号点菜」，名称、地址、来源 URL 全部由 `plan_builder.hydrate_plan` 按编号从 `CandidateCatalog` 水合。
5. **错误回灌**：结构化校验失败时，由 `services/llm.py` 把 Pydantic 校验错误原文作为反馈再请模型修正一次（仅一次），仍失败抛 `LLMError`（CLI 退出码 1，API 侧转 502 信封）。
6. **中文输出、英文变量名**：面向用户文本中文；模型字段与枚举英文，便于代码处理。
7. **转义安全**：系统/用户模板均为纯文本装配，Jinja2 以 `autoescape=False` 加载（文本提示词无需 HTML 转义）；用户内容从不参与模板文件名拼接。

## 2. 模板清单与变量契约（阶段三/四实装）

| 文件 | 节点 | 调用方式 | 输入变量 | 输出 |
|---|---|---|---|---|
| `intent.*.jinja2` | `parse_intent` | `aparse(IntentResult)` | `today`、`weekday`、`has_plan`、`brief_json`、`messages` | `IntentResult{intent, target_scope[], confidence, reason, brief_patch}` |
| `clarify.*.jinja2` | `clarify_brief` | `acomplete()` → 文本 | `missing_slots[]`（含中文标签与示例）、`brief` | 唯一一个聚合追问（≤120 字） |
| `compose.*.jinja2` | `compose_plan` | `aparse(PlanDraft)` | `brief`、`days`、`budget`、`max_daily_hours`、`catalog`（编号目录）、`weather_lines[]`、`web_lines[]` | `PlanDraft{summary, days[{date, items[{title, category, candidate_ref, start_time, end_time, duration_min, cost_cny, indoor, notes}]}], tips[]}` |
| `revise.*.jinja2` | `revise_plan` | `aparse(PlanDraft)` | `days`、`max_daily_hours`、`catalog`、`previous_draft`、`reflections[]`、`loop_count`、`max_loops` | 修订后的 `PlanDraft`（plan_id 在代码层保留） |
| `patch.*.jinja2` | `patch_plan` | `aparse(PlanDraft)` | `days`、`max_daily_hours`、`budget`、`brief_json`、`catalog_text`、`weather_text`、`web_text`、`plan_text`（当前行程反查编号）、`target_scope_text`、`user_instruction` | 修订后的 `PlanDraft`（仅命中范围变化，`compute_diff` 产出 `PlanDiff`） |
| `respond.*.jinja2` | `respond` | `acomplete()` → 文本 | `mode`（`plan` / `chat`）、`plan_json`、`warnings_text`、`alerts_text`、`unresolved[]`、`diff_text`（变更说明）、`transcript`、`hint`、`message` | 给用户的中文 Markdown 回复（≤600 字） |

## 3. 各模板要点

### intent（意图识别 + 槽位合并）

- 四类意图互斥：`new_plan / modify_plan / ask_info / chitchat`；`confidence` 低于阈值时图按 `ask_info` 兜底，避免误改行程。
- 相对/节日时间（「明天」「国庆」）必须结合注入的 `today/weekday` 换算成绝对日期；无法确定留 `null`，**绝不猜测**。
- pace 中文映射写死在模板：轻松/休闲→relaxed，适中/正常→moderate，紧凑/充实/特种兵→packed。
- `brief_patch` 与历史槽位合并：用户没推翻的字段原样保留，列表字段去重追加；「五千」→5000，外币不换算。
- `modify_plan` 的 `target_scope`（「第 N 天」「按标题模糊匹配」「预算等全局槽位」）阶段三识别但不消费——图路由到 `respond` 提示局部修改下一阶段开放（阶段四落地）。

### clarify（一次性聚合追问）

- 缺失槽位**一次性**问完（一段话 ≤120 字），禁止连环追问；只问缺失项，每项附示例/可选值；不自介绍、不承诺行程。
- 输出后图直接到 END：下一轮用户补充后由 `parse_intent` 重新合并槽位并进入检索。

### compose（编排草稿）

铁律章节（违反即作废，与领域校验互为双保险）：

1. attraction/restaurant/hotel 的 `candidate_ref` 只能填候选目录中存在的编号；事实信息以候选为准，**不得编造景点/商户/URL**；activity/transport/note 可无编号但不得虚构具体商户名。
2. 时间 `"HH:MM"`，相邻条目开始时间晚于上一条结束并预留通勤；**恰好覆盖 N 天**、日期与给定一致、每天 3-6 条。
3. **当天降水概率 > 60% 时优先室内项（indoor=true）排白天**（对应 `rules.rainy_indoor`）。
4. 每日总时长 ≤ `max_daily_hours`（默认 8h）；总花费不明显超过预算。
5. 费用单位 **CNY**，不确定留 `null`，禁止臆造价格。
6. summary 2-4 句；tips ≤ 10 条，可参考网页资料但不得编造链接。

模型只产出编号引用；坐标、名称、来源 URL、雨天重标、天数截断/补齐全部发生在确定性的 `hydrate_plan` + `evaluate_plan`，模型不碰事实。

### revise（按程序化反馈修订）

- 输入 `reflections[]` 来自 `rules.evaluate_plan`（空天、超时、预算超限、营业时间冲突、通勤缺口、极端天气），是**程序产物而非模型自评**。
- 逐条反馈要求具体调整动作（删/分散、换顺序、降级消费、补室内项、改到访时段）；未被点名的安排保持稳定，避免无谓重排。
- 同样受编号引用、`HH:MM`、恰好 N 天、CNY、每日时长上限约束；修订循环上限 `MAX_REVISE_LOOPS`（默认 3），超出强制交付并把残留问题作为 `unresolved` 交给 respond 明示用户。

### patch（定向修改 · 阶段四）

- 与 revise 的核心区别：revise 由程序化 reflections 驱动全量重排，patch 由用户自然语言 `target_scope` 驱动定向调整。
- 铁律：「**未被要求修改的天/项必须原样保留**」——保持相同候选编号、时段、费用；只调整用户点名的范围。其余约束（编号引用、HH:MM、恰好 N 天、CNY、每日时长上限）与 compose 一致。
- 用户指令原文作为 `user_instruction` 注入；`target_scope_text` 列出 LLM 解析出的命中范围。
- 输出仍为完整 `PlanDraft`（全 N 天），`compute_diff` 在代码层按 `(day_index, poi_id/title)` 精确比对产出的 `PlanDiff` 驱动交付说明，不依赖 LLM 自评变更范围。

### respond（交付 / 闲聊）

- 单模板按 `mode` 分支：`plan` 模式把已校验行程渲染为友好 Markdown（亮点/预算执行→逐日一行式条目→已有 tips，≤600 字，不用一级标题）；`chat` 模式回答旅行问题，不知道就明说。
- 只描述行程中真实存在的内容，不新增事实；水合警告与未解决校验项以变量传入，随回复透明展示。

## 4. 调优与回归

- 每个模板以 Jinja2 `{# 版本/变更原因 #}` 注释记录版本；重大调整在第 5 节追加记录。
- 固定回归用例：**成都 4 天 / 预算 5000 CNY / moderate / 爱吃辣**（验收 #1，fixture 见 `tests/integration/agent_fakes.py` 的 `chengdu_brief()` / `chengdu_candidates()` / `valid_draft()`）。改动模板后必须跑：
  - 单测：`tests/unit/test_serde_prompts.py`（render_pair 变量契约）
  - 端到端：`tests/integration/test_agent_graph.py`（happy path 断言天数=4、全部条目可溯源、总费用≤5000；修订循环断言 plan_version=2 且反馈原文回灌）
- 关键红线的程序化双保险（不依赖提示词自觉）：

| 提示词约束 | 程序侧兜底 |
|---|---|
| 编号只能来自候选 | `PlanDraft` 校验 `candidate_ref ≥ 1`；`hydrate_plan` 丢弃查无编号的条目并入 warnings |
| 恰好 N 天 / 每日时长 / 营业时间 / 通勤 / 预算 / 雨天 | `domain/rules.py evaluate_plan` 逐条产出 reflections 驱动修订循环 |
| 时间 HH:MM | `PlanItem` before-validator 宽容解析，非法时间 Pydantic 报错触发重试 |
| 不编造 URL/事实 | 来源只从 `Poi.sources` / `SearchResult.url` 水合，自由条目无 sources |
| 费用 CNY | 全领域模型只有 `cost_cny` 字段，外币保留原币种文本在 notes |

- 观测：后续接 Langfuse 后按节点对比 token、耗时、结构化输出一次成功率与重试率（`DeepSeekLLM.usage_snapshot()` 已记录 prompt/completion/total）。

## 5. 调优记录

| 日期 | 模板 | 变更 | 原因/效果 |
|---|---|---|---|
| 阶段一 | — | 建立契约与原则，未实装正文 | — |
| 阶段三 | intent/clarify/compose/revise/respond | 5 对 system/user 全部落地；意图与草稿结构化输出，事实改为「编号点菜 + 水合」；新增 respond 交付模板 | 成都 4 天 fixture 端到端通过：4 天全条目可溯源、预算内，修订循环 2 轮收敛 |
