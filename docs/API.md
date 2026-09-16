# 接口文档

健康探针、错误契约、SSE 流式对话、认证、行程 CRUD、分享快照均已实装。本文档为最终契约，前后端共同遵守。

在线交互式文档：服务启动后访问 `/docs`（Swagger）与 `/redoc`。

## 1. 通用约定

- Base URL：`http://127.0.0.1:8000`
- 所有请求/响应均为 `application/json; charset=utf-8`（SSE 除外，为 `text/event-stream`）。
- 请求建议携带 `X-Request-ID`；未携带时服务端生成，并在响应头原样返回，全链路日志据此串联。
- **鉴权**：除 `/api/auth/register`、`/api/auth/login`、`/healthz`、`/readyz`、`/docs`、`/share/{token}` 外，**所有业务端点均需登录**（HttpOnly Session Cookie `session`，登录后自动携带）。未登录返回 `401 UNAUTHORIZED`。
- **数据隔离**：`/api/plan` 系端点按登录用户隔离，非本人行程一律 `404 PLAN_NOT_FOUND`。
- 错误统一信封：

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "请求参数校验失败",
    "request_id": "9f1c...",
    "details": { "fields": [ { "loc": ["body", "date"], "msg": "..." } ] }
  }
}
```

错误码：`BAD_REQUEST / VALIDATION_ERROR / UNAUTHORIZED / RATE_LIMITED / NOT_FOUND / PLAN_NOT_FOUND / SESSION_NOT_FOUND / LLM_NOT_CONFIGURED / UPSTREAM_ERROR / SERVICE_UNAVAILABLE / INTERNAL_ERROR`。

## 2. 健康与页面端点

### GET /healthz

存活探针。

```json
{ "status": "ok", "version": "0.1.0" }
```

### GET /readyz

就绪探针，检查运行期目录可写性。

```json
{
  "status": "ready",
  "version": "0.1.0",
  "checks": { "data_dir": "ok", "cache_dir": "ok" }
}
```

### GET /

对话页（Jinja2 SSR）。未登录时前端展示登录/注册卡片；登录后台展示对话与"我的行程"抽屉。

### GET /plan/{plan_id} · GET /share/{token}

行程详情页（需登录）/ 只读分享页（无需登录）。

## 3. 认证端点 /api/auth

### POST /api/auth/register · 201

```json
{ "username": "traveler", "password": "Tour2026abc" }
```

用户名 3-32 位（字母/数字/下划线/中文），密码 ≥8 位且需含字母和数字。重复用户名返回 `409`。

### POST /api/auth/login

```json
{ "username": "traveler", "password": "Tour2026abc" }
```

成功在响应头 `Set-Cookie` 下发 HttpOnly `session` Cookie（默认 7 天）。密码错误 `401`。

### POST /api/auth/logout · 204

登出并删除服务端会话行与 Cookie。需登录。

### GET /api/auth/me

```json
{ "id": 3, "username": "traveler" }
```

返回当前登录用户；未登录 `401`。前端据此决定展示登录卡片或主界面。

## 4. 规划中的业务端点（均需登录）

### POST /api/chat

发起/继续对话，返回 **SSE 流**（单向推送，浏览器原生 EventSource 自带断线重连）。

> **鉴权与会话**：请求携带登录 Cookie 即绑定到当前用户；对话进度/结果只在流结束后的响应链路落库。**仅当 Agent 本轮实际生成出行程时才写入 `plans` 表**，chitchat / 开放问答不改会话记忆、不落 messages、不产生行程。
> **会话内记忆**：按 `(user_id, thread_id)` 存累计 brief——时间/地点/人数可分多轮分批补充，每轮结束后回灌给模型。

请求：

```json
{
  "thread_id": "uuid（可选，续聊必传）",
  "message": "下个月去成都玩4天，预算5000，爱吃辣，不想太累",
  "client_ts": "2026-10-01T10:00:00Z"
}
```

SSE 事件格式：`event: <类型>\ndata: <JSON>\n\n`。事件类型与负载：

| event | data 负载 | 说明 |
|---|---|---|
| `token` | `{"text": "正在为你…"}` | 模型增量文本，前端追加渲染 |
| `tool_start` | `{"tool": "search_poi", "args": {"city": "成都", "keywords": "火锅"}, "run_id": "r1"}` | 工具开始，驱动进度条/步骤文案 |
| `tool_end` | `{"run_id": "r1", "ok": true, "cached": false, "count": 8, "elapsed_ms": 412}` | 工具结束；`cached=true` 表示命中 TTL 缓存 |
| `plan_patch` | `PlanDiff`（见下） | 局部修改结果，未命中范围保持不变 |
| `clarify` | `{"question": "聚合追问（最多一个问题）", "slots": ["departure_city"]}` | 需求缺失时的一次性追问 |
| `plan` | `TripPlan`（完整行程卡片数据） | 首版/全量重算后的行程 |
| `warning` | `{"type": "weather|budget|loop_cap", "message": "..."}` | 极端天气、预算取舍、修订次数触顶等 |
| `done` | `{"thread_id": "...", "plan_version": 3}` | 流正常结束 |
| `error` | `{"code": "UPSTREAM_ERROR", "message": "...", "retryable": true}` | 致命错误；可重试错误前端自动重连 |

`PlanDiff`：

```json
{
  "added":   [{"day": 2, "item_id": "i22", "title": "四川博物院"}],
  "removed": [{"day": 2, "item_id": "i21", "title": "宽窄巷子"}],
  "changed": [{"day": 2, "item_id": "i23", "before": {"start": "18:00"}, "after": {"start": "19:30"}}],
  "reason": "第2天降雨概率72%，用室内博物馆替换户外街区，晚餐顺延"
}
```

### GET /api/chat/{thread_id}/history

恢复会话消息（刷新/重启后续聊）。限定当前用户。

### GET /api/plan · 我的行程列表

返回当前用户的行程列表（按更新时间倒序，`limit` 参数默认 50）：

```json
[
  {
    "id": "a1b2...",
    "title": "成都4天3晚…",
    "destination": "成都",
    "start_date": "2026-10-01",
    "end_date": "2026-10-04",
    "budget_cny": 5000.0,
    "days": 4,
    "updated_at": "2026-09-15T09:00:00",
    "created_at": "2026-09-15T08:00:00"
  }
]
```

### POST /api/plan · 新建行程（空骨架）

```json
{ "destination": "北京", "start_date": "2026-11-01", "end_date": "2026-11-03", "travelers": 3, "budget_cny": 3000 }
```

占位实现：仅创建空骨架（`days=[]`），后续通过在对话页与之对话补全内容。

### GET /api/plan/{plan_id}

返回当前版本行程（概览、逐日 items、费用、天气、来源链接）。非本人行程 `404`。

### GET /api/plan/{plan_id}/versions

版本列表：`[{version, trigger_snippet, created_at}]`。

### POST /api/plan/{plan_id}/rollback

```json
{ "version": 2 }
```

回滚到指定版本（以新版本写入，不删除历史）。

### DELETE /api/plan/{plan_id} · 204

删除本人行程及其全部版本；非本人 `404`。

### POST /api/plan/{plan_id}/pdf

PDF 导出暂为占位（返回 503 提示用浏览器打印）。

### POST /api/share

为当前版本生成只读快照与不可猜测 token；`GET /api/share/{token}` 只读访问（无需登录），禁止写入。

## 5. 领域模型

已在阶段二/三固化（frozen Pydantic，`extra=forbid`），实际字段以代码为准：

- [`TravelBrief`](../src/travel_agent/domain/brief.py)：`destination/start_date/end_date/travelers(1..50)/budget_cny/pace` 必填，`preferences/dietary/must_visit/avoid` 可选；`missing_slots()`、`is_ready`、`duration_days`（含首尾）。
- [`Poi`](../src/travel_agent/domain/models.py)：`poi_id/name/location(GeoPoint)` 必填，`sources: list[HttpUrl]`、营业时间等可选；另有 `SearchResult / DailyWeather / WeatherForecast / RouteInfo`。
- [`TripPlan / PlanDay / PlanItem`](../src/travel_agent/domain/plan.py)：item 含 `item_id/title/category/duration_min/start_time/end_time/cost_cny/indoor/weather_adjusted/poi_id/location/sources/travel_mode_to_next/notes`；计算字段 `total_cost_cny`（明细加总，CNY，round 2）与 `per_person_cost_cny`（= 总花费 / 出行人数，人均参考值，round 2）随 plan 序列化输出。
- 条目类别 `ItemCategory`：attraction / restaurant / hotel / activity / transport / note。
- 与 LLM 的边界模型 [`PlanDraft / DraftDay / DraftItem`](../src/travel_agent/domain/draft.py)：模型只产编号引用，经 `plan_builder.hydrate_plan` 水合成 `TripPlan`。

## 6. 兼容性策略

- 错误码字符串与事件类型一旦发布只增不删，字段废弃保留两个版本周期。
- Schema 演进通过 OpenAPI 版本号体现；破坏性变更升级 `/api/v2`。
