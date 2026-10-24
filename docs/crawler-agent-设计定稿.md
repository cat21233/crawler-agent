# crawler-agent 设计定稿

> 版本 v1.0 · 2026-10-20
> 项目定位：**基于 Function Calling 的智能商品数据采集 Agent**
> 交付目标：2027-01-15 前完成 MVP，可作为简历主项目

---

## 一、项目定位与范围

### 一句话定位

> 用户用自然语言下达采集指令，Agent 自主决策调用工具完成「获取 → 解析 → 落库」，
> 全程 SSE 流式推送思考链，所有步骤落库可回放。

### 范围边界（★ 这一节最重要，防止做不完）

| ✅ 做 | ❌ 不做 |
|---|---|
| 采集 **2-3 个已知结构**的商品列表页 | 抓**任意网站** |
| 静态页 + JS 渲染页两种模式 | 登录态 / 会员内容 |
| 分页翻页 | 破解滑块 / 点选验证码 |
| 429/403 降频重试 | 对抗专业风控（Cloudflare 等） |
| 选择器失效时读 HTML 片段重试 | 自动生成选择器（做不到） |
| 单机部署 + 单 Agent 串行 | 多 Agent 协同 / 分布式 |
| **检测到验证码 → 停止并告警** | 硬刚验证码 |

### 技术选型

| 层 | 选型 | 理由 |
|---|---|---|
| Web 框架 | FastAPI | 已有 |
| LLM 调用 | `llm_service`（从 day01 复用） | 多厂商 + 重试 + 并发 |
| 工具调用 | DeepSeek / 智谱 function calling | 原生支持 tool_calls |
| 数据库 | MySQL + SQLModel | day01 已验证 |
| 缓存 | 内存 dict（MVP）→ Redis（第二阶段） | 先跑通再加 |
| 爬虫 | DrissionPage（静态 SessionPage + 动态 ChromiumPage） | 已有 |
| 前端 | 原生 HTML + JS + EventSource | 够用，不上框架 |

---

## 二、目录结构

```
crawler-agent/
├── .github/workflows/ci.yml       ← 从 day01 复用
├── common/
│   ├── __init__.py
│   ├── sse.py                     ← 从 day01 复用（稍改）
│   ├── logger.py                  ← 从 day01 复用
│   ├── db.py                      ← 从 day01 复用（改 URL）
│   └── page_cache.py              ★ 新写（HTML 缓存，ref 模式核心）
├── llm_service/                   ← 从 day01 整体复用
│   ├── __init__.py
│   ├── client.py
│   └── factory.py
├── models/
│   ├── __init__.py
│   ├── thread.py                  ★ 会话
│   ├── task.py                    ★ 采集任务
│   ├── target.py                  ★ 目标配置
│   ├── record.py                  ★ 抓取记录
│   └── trace.py                   ★ 思考轨迹
├── agent/
│   ├── __init__.py
│   ├── loop.py                    ★ ReAct 循环（核心）
│   ├── prompts.py                 ★ system prompt
│   ├── schemas.py                 ★ 4 个工具的 JSON Schema
│   └── tools/
│       ├── __init__.py            ★ 工具注册表
│       ├── list_targets.py
│       ├── fetch_page.py
│       ├── parse_by_selector.py
│       └── save_records.py
├── crawler/
│   ├── __init__.py
│   └── fetcher.py                 ★ DrissionPage 封装
├── routers/
│   ├── __init__.py
│   ├── agent.py                   ★ POST /agent/run（SSE）
│   ├── traces.py                  ★ GET /traces/{thread_id}
│   └── targets.py                 ★ 目标配置 CRUD
├── static/
│   └── index.html                 ★ 前端单页
├── mock_site/
│   └── main.py                    ★ mock 商品站（演示保底）
├── tests/
│   ├── conftest.py                ← 从 day01 复用
│   ├── test_tools.py
│   └── test_agent_loop.py
├── docker-compose.yml             app + mysql + redis
├── Dockerfile                     ← 从 day01 复用（改）
├── config.py                      ← 从 day01 复用（加字段）
├── main.py
├── requirements.txt
└── README.md
```

**复用原则**：`common/` + `llm_service/` + `Dockerfile` + `config.py` + `tests/conftest.py` 直接搬；
**`agent/` + `models/` + `crawler/` 全自己写**（这是面试要讲的部分）。

---

## 三、★ 四个工具（完整定义）

### 3.1 设计原则

| 原则 | 说明 |
|---|---|
| **参数 ≤ 4 个** | 参数越多模型越容易传错 |
| **一工具一动作** | 失败可精确定位 |
| **description 写"何时用 + 何时不用"** | 模型靠这个决策 |
| **返回结构化 dict，不抛异常** | 让 Agent 能决策，而不是看到 traceback |

### 3.2 工具一：`list_targets`

```python
{
    "type": "function",
    "function": {
        "name": "list_targets",
        "description": (
            "查询某个任务下已配置的采集目标（URL + 选择器）。"
            "当用户说'抓任务里的目标''抓所有配置的网站'时必须先调用它获取列表。"
            "不要在没有 task_id 时调用。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "task_id": {"type": "integer", "description": "任务 ID"}
            },
            "required": ["task_id"],
        },
    },
}
```

**返回**：

```json
{
  "targets": [
    {"id": 1, "url": "http://localhost:9000/products?page=1", "selector": ".product-item", "fields": ["title", "price"], "last_crawled": "2026-10-20T10:00:00Z"},
    {"id": 2, "url": "http://localhost:9000/products?page=2", "selector": ".product-item", "fields": ["title", "price"], "last_crawled": null}
  ],
  "total": 2
}
```

### 3.3 工具二：`fetch_page`

```python
{
    "type": "function",
    "function": {
        "name": "fetch_page",
        "description": (
            "获取网页内容，返回一个 ref 引用（不返回完整 HTML）。"
            "需要查看页面内容时必须先调用它，再用 ref 调 parse_by_selector。"
            "如果返回的 has_likely_data 为 false，说明数据可能在 JS 渲染后才出现，"
            "应改用 need_render=true 重试一次。"
            "不要用它获取 JSON API。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "完整 URL，含 http:// 或 https://"},
                "need_render": {
                    "type": "boolean",
                    "description": "是否启用浏览器渲染（处理 JS 动态页面）。默认 false。首次失败后再试 true。",
                    "default": False,
                },
            },
            "required": ["url"],
        },
    },
}
```

**返回（成功）**：

```json
{
  "status": "ok",
  "ref": "page_a1b2c3d4",
  "length": 48213,
  "preview": "<!DOCTYPE html><html><head><title>商品列表...",
  "has_likely_data": true,
  "detected_items": 12
}
```

**返回（失败，三种情况都不抛异常）**：

```json
{"status": "blocked",     "reason": "检测到验证码特征: geetest", "suggest": "停止抓取，需人工介入"}
{"status": "rate_limited","retry_after": 60,  "suggest": "等待 60 秒后重试，或降低频率"}
{"status": "forbidden",   "status_code": 403,"suggest": "可能 IP 受限，建议换目标或稍后重试"}
{"status": "error",       "reason": "TimeoutError", "suggest": "可重试一次"}
```

### 3.4 工具三：`parse_by_selector`

```python
{
    "type": "function",
    "function": {
        "name": "parse_by_selector",
        "description": (
            "用 CSS 选择器从已获取的页面中提取元素。"
            "必须先调用 fetch_page 拿到 ref。"
            "如果返回 count 为 0，说明选择器不对，"
            "应读取返回的 html_snippet 判断真实结构，再换一个选择器重试。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "ref": {"type": "string", "description": "fetch_page 返回的 ref"},
                "selector": {"type": "string", "description": "CSS 选择器，如 '.product-item'"},
                "fields": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "要提取的字段名，可选 title/price/url/text。默认 ['text']",
                },
            },
            "required": ["ref", "selector"],
        },
    },
}
```

**返回（成功）**：

```json
{
  "status": "ok",
  "count": 12,
  "items": [
    {"title": "Python 实战课", "price": "199", "url": "/product/1"},
    {"title": "算法导论", "price": "89", "url": "/product/2"}
  ]
}
```

**返回（选择器没命中，这是 Agent 自我纠正的关键）**：

```json
{
  "status": "empty",
  "count": 0,
  "html_snippet": "<div class=\"goods-card\"><h3>...",
  "suggest": "选择器未命中。观察 html_snippet，可能是 .goods-card 而不是 .product-item"
}
```

> ★ **`html_snippet` 是整个容错机制的核心**——给模型 500 字符的片段，它就能推断出正确的选择器。这是"Agent 真能自我纠正"的关键设计。

### 3.5 工具四：`save_records`

```python
{
    "type": "function",
    "function": {
        "name": "save_records",
        "description": (
            "把解析出的条目存入数据库，自动按 URL 去重。"
            "仅在 parse_by_selector 成功返回条目后调用。"
            "不要用它保存原始 HTML。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "target_id": {"type": "integer", "description": "目标 ID（来自 list_targets）"},
                "items": {
                    "type": "array",
                    "description": "条目数组，每项至少含 title",
                    "items": {"type": "object"},
                },
            },
            "required": ["target_id", "items"],
        },
    },
}
```

**返回**：

```json
{"status": "ok", "new": 7, "skipped": 5, "total": 12}
```

---

## 四、★ 五张表（完整定义）

### 4.1 关系图

```
threads (会话)
   │ 1
   │
   ├──── N  agent_traces (每个 step 一行)
   │
   └──── N  tasks (采集任务)
                │ 1
                │
                ├──── N  targets (目标配置)
                │            │ 1
                │            │
                │            └──── N  records (抓取记录)
```

### 4.2 完整字段

```python
# models/thread.py
class Thread(SQLModel, table=True):
    """会话。一次完整的用户对话 = 一个 thread。"""
    __tablename__ = "threads"

    id: int | None = Field(default=None, primary_key=True)
    thread_id: str = Field(index=True, unique=True, max_length=64)
    title: str = Field(default="", max_length=200)
    user_id: int | None = Field(default=None, index=True)
    status: str = Field(default="active", max_length=20)   # active / completed / failed
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


# models/task.py
class Task(SQLModel, table=True):
    """采集任务。一个用户指令 = 一个 task。"""
    __tablename__ = "tasks"

    id: int | None = Field(default=None, primary_key=True)
    thread_id: str = Field(index=True, max_length=64)
    instruction: str = Field(sa_column=Column(Text))        # 用户原始指令
    status: str = Field(default="pending", max_length=20)   # pending/running/done/failed
    total_records: int = Field(default=0)
    created_at: datetime = Field(default_factory=utcnow)
    finished_at: datetime | None = None


# models/target.py
class Target(SQLModel, table=True):
    """采集目标。一个 URL + 一套选择器规则。"""
    __tablename__ = "targets"

    id: int | None = Field(default=None, primary_key=True)
    task_id: int = Field(foreign_key="tasks.id", index=True)
    url: str = Field(max_length=500)
    selector: str = Field(default="", max_length=200)
    fields: str = Field(default="title,price,url", max_length=200)   # 逗号分隔
    need_render: bool = Field(default=False)
    last_crawled_at: datetime | None = None
    last_status: str = Field(default="", max_length=20)


# models/record.py
class Record(SQLModel, table=True):
    """抓取到的一条数据。"""
    __tablename__ = "records"
    __table_args__ = (
        UniqueConstraint("target_id", "url_hash", name="uq_target_url"),
    )

    id: int | None = Field(default=None, primary_key=True)
    target_id: int = Field(foreign_key="targets.id", index=True)
    title: str = Field(max_length=300)
    price: str | None = Field(default=None, max_length=50)
    url: str | None = Field(default=None, max_length=500)
    url_hash: str = Field(index=True, max_length=64)      # md5(规范化 url)
    extra: str | None = Field(default=None, sa_column=Column(Text))  # 其他字段 JSON
    crawled_at: datetime = Field(default_factory=utcnow)
```

### 4.3 ★ `agent_traces`（最重要的一张表）

```python
# models/trace.py
class AgentTrace(SQLModel, table=True):
    """Agent 执行轨迹。一个 step = 一行。"""
    __tablename__ = "agent_traces"

    id: int | None = Field(default=None, primary_key=True)

    # ── 归属 ──
    thread_id: str = Field(index=True, max_length=64)
    task_id: int | None = Field(default=None, foreign_key="tasks.id", index=True)
    step: int = Field(default=0)

    # ── 三元组 + 角色 ──
    role: str = Field(default="assistant", max_length=20)   # user / assistant / tool
    thought: str | None = Field(default=None, sa_column=Column(Text))
    action: str | None = Field(default=None, sa_column=Column(Text))       # 完整 JSON
    tool_name: str | None = Field(default=None, index=True, max_length=50) # ★ 冗余，可索引
    observation: str | None = Field(default=None, sa_column=Column(Text))  # ★ 只存摘要

    # ── 可观测性 ──
    status: str = Field(default="ok", max_length=20)       # ok / error / blocked
    error: str | None = Field(default=None, sa_column=Column(Text))
    latency_ms: int | None = Field(default=None)
    tokens: int | None = Field(default=None)

    created_at: datetime = Field(default_factory=utcnow, index=True)
```

**索引设计（全部有理由）**：

| 索引 | 支撑的查询 |
|---|---|
| `thread_id` | 按会话拉全部步骤 |
| `task_id` | 按任务拉 |
| `tool_name` | "所有 fetch_page 调用"（统计、排查） |
| `created_at` | 按时间范围统计 |
| **复合 `(thread_id, step)`** | **★ 最常用：按会话查并排序** |

**三个关键设计决策**：

| 决策 | 理由 |
|---|---|
| **一个 step 一行** | 可单独查询/统计；不用嵌套 JSON |
| **`tool_name` 与 `action` 冗余** | `action` 是 JSON 无法索引；`tool_name` 单独存可建索引 |
| **`observation` 只存摘要** | 完整 HTML 存 `page_cache`，trace 里只留"length=48213, items=12" |

---

## 五、system prompt 草稿

```python
# agent/prompts.py

SYSTEM_PROMPT = """你是一个商品数据采集 Agent。你的目标是根据用户指令，
通过调用工具完成数据采集任务。

## 工作流程
1. 如果用户提到"任务里的目标"，先调 list_targets 拿目标列表
2. 对每个目标：fetch_page → parse_by_selector → save_records
3. 比对用户要求，判断是否完成；完成则给出总结

## 重要规则
- **一次只处理一个目标**，不要并行
- **每个目标最多重试 2 次**。选择器返回 empty 时，读 html_snippet 换选择器重试
- **fetch_page 返回 has_likely_data=false 时**，用 need_render=true 重试一次
- **遇到 status=blocked（验证码）立即停止该目标**，不要重试，向用户报告需要人工介入
- **遇到 status=rate_limited**，报告等待时间，不要连续重试
- **不要猜测选择器**，必须基于 html_snippet 或已有配置
- **save_records 只在 parse 成功后调用**，不要保存空数组

## 任务完成时
用一句话总结：成功抓取了几个目标、新增多少条数据、哪些目标失败及原因。

## 禁止
- 不要编造 URL
- 不要输出解释性文字（直接调工具或给结论）
- 不要重复调用同一个失败的工具超过 2 次
"""
```

> **FC 版不需要在 prompt 里写 JSON 输出格式**——`tools` 参数已经约束了。这是相比手写版最大的简化。

---

## 六、Agent 循环（核心伪代码）

```python
# agent/loop.py

async def run_agent(
    thread_id: str,
    task_id: int,
    instruction: str,
    session_factory,          # 传工厂，不传 session（Session 不跨线程）
    max_steps: int = 12,
) -> AsyncGenerator[dict, None]:
    """返回异步生成器，产出 SSE 事件。"""

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": instruction},
    ]

    tool_history: list[str] = []          # 防死循环
    yield {"event": "start", "data": {"thread_id": thread_id}}

    for step in range(1, max_steps + 1):
        t0 = time.time()

        # ── 调模型（同步 SDK 丢线程池）─────────────────
        resp = await run_in_threadpool(
            lambda: llm.chat_with_tools(messages, TOOLS)
        )
        msg = resp.choices[0].message
        latency = int((time.time() - t0) * 1000)

        # ── 闸 1：模型说完了 → 结束 ────────────────────
        if resp.choices[0].finish_reason == "stop":
            yield {"event": "final", "data": {"content": msg.content}}
            save_trace(thread_id, task_id, step, "assistant",
                       thought=msg.content, status="ok", latency_ms=latency)
            return

        # ── 闸 2：要调工具 ────────────────────────────
        messages.append(msg)

        for tc in msg.tool_calls:
            name = tc.function.name
            args = json.loads(tc.function.arguments)

            # 防死循环：连续 3 步同样调用 → 打断
            sig = f"{name}:{tc.function.arguments}"
            tool_history.append(sig)
            if tool_history[-3:].count(sig) >= 3:
                obs = {"status": "error", "reason": "检测到重复调用，已中止"}
                yield {"event": "error", "data": obs}
                return

            yield {"event": "action", "data": {"tool": name, "args": args}}

            # ── 执行（工具内部用独立 Session，丢线程池）──
            try:
                result = await run_in_threadpool(
                    execute_tool, name, args, session_factory
                )
                status = result.get("status", "ok")
            except Exception as e:
                result = {"status": "error", "reason": f"{type(e).__name__}: {e}"}
                status = "error"

            yield {"event": "observation", "data": result}

            # ── 落 trace（observation 只存摘要）────────
            save_trace(thread_id, task_id, step, "tool",
                       action=tc.function.arguments,
                       tool_name=name,
                       observation=summarize(result),      # ★ 摘要，不存全文
                       status=status, latency_ms=latency)

            # ── 回填 ──────────────────────────────────
            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": json.dumps(result, ensure_ascii=False),
            })

    yield {"event": "error", "data": {"reason": f"达到最大步数 {max_steps}"}}
```

### 三处关键设计

| 位置 | 设计 | 理由 |
|---|---|---|
| `run_in_threadpool(...)` | 同步 SDK 丢线程池 | 不阻塞 event loop（你项目有 async SSE 路由） |
| `session_factory` 而非 `session` | 传工厂 | Session 不跨线程，由工具内部创建 |
| `summarize(result)` | trace 只存摘要 | 防 traces 表被 HTML 撑爆 |

---

## 七、mock 商品站（演示保底）

```python
# mock_site/main.py
"""Mock 商品站。用于演示与回归测试，完全可控。

启动：uvicorn mock_site.main:app --port 9000

演示开关：
  GET /toggle/break-selector   → 切换选择器失效（演示 Agent 自我纠正）
  GET /toggle/rate-limit       → 切换返回 429（演示限流处理）
"""

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse

app = FastAPI(title="Mock 商品站")

STATE = {"break_selector": False, "rate_limit": False}

PRODUCTS = [
    {"id": i, "title": f"商品 {i}：Python 实战教程第 {i} 版",
     "price": 99 + i * 10, "stock": 100 - i * 7}
    for i in range(1, 21)
]

@app.get("/toggle/break-selector")
def toggle_selector():
    STATE["break_selector"] = not STATE["break_selector"]
    return {"break_selector": STATE["break_selector"]}

@app.get("/toggle/rate-limit")
def toggle_rate():
    STATE["rate_limit"] = not STATE["rate_limit"]
    return {"rate_limit": STATE["rate_limit"]}

@app.get("/products", response_class=HTMLResponse)
def product_list(page: int = 1, size: int = 5):
    if STATE["rate_limit"]:
        raise HTTPException(429, "Too Many Requests", headers={"Retry-After": "30"})

    # ★ 演示用：切换后 class 从 product-item 变成 goods-card
    cls = "goods-card" if STATE["break_selector"] else "product-item"

    start = (page - 1) * size
    items = PRODUCTS[start:start + size]

    cards = "".join(
        f'<div class="{cls}" data-id="{p["id"]}">'
        f'<h3 class="title">{p["title"]}</h3>'
        f'<span class="price">¥{p["price"]}</span>'
        f'<a class="detail" href="/product/{p["id"]}">详情</a>'
        f'</div>'
        for p in items
    )

    has_next = start + size < len(PRODUCTS)
    next_link = (f'<a class="next" href="/products?page={page+1}">下一页</a>'
                 if has_next else '<span class="end">已到最后一页</span>')

    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>商品列表 第{page}页</title></head>
<body>
  <h1>商品列表</h1>
  <div class="product-list">{cards}</div>
  <div class="pager">{next_link}</div>
</body></html>"""

@app.get("/product/{pid}", response_class=HTMLResponse)
def product_detail(pid: int):
    p = next((x for x in PRODUCTS if x["id"] == pid), None)
    if not p:
        raise HTTPException(404, "商品不存在")
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>{p['title']}</title></head>
<body>
  <h1 class="title">{p['title']}</h1>
  <p class="price">¥{p['price']}</p>
  <p class="stock">库存 {p['stock']}</p>
</body></html>"""
```

### 演示脚本（三个桥段）

| # | 操作 | 演示什么 |
|---|---|---|
| **1** | 正常抓 `http://localhost:9000/products?page=1` | Agent 完整流程 |
| **2** | `GET /toggle/break-selector` 后再抓 | **Agent 读 html_snippet 换选择器自我纠正** |
| **3** | `GET /toggle/rate-limit` 后再抓 | **Agent 识别 429 并报告等待时间，不硬刚** |

> **桥段 2 和 3 是最亮眼的**——面试官会看到"这个 Agent 真的会自己想办法"。

---

## 八、SSE 事件协议（前后端契约）

```
POST /agent/run
Content-Type: application/json
{"instruction": "抓取任务1的所有目标", "task_id": 1, "thread_id": "可选"}

响应：text/event-stream

event: start
data: {"thread_id":"t_abc123","task_id":1}

event: action
data: {"tool":"list_targets","args":{"task_id":1}}

event: observation
data: {"status":"ok","targets":[{"id":1,...}],"total":2}

event: thought
data: {"content":"需要先获取第1个目标的页面"}

event: action
data: {"tool":"fetch_page","args":{"url":"http://..."}}

event: observation
data: {"status":"ok","ref":"page_a1b2","length":48213,"detected_items":5}

event: final
data: {"content":"成功抓取 2 个目标，新增 12 条数据"}
```

### 查询回放

```
GET /traces/{thread_id}
→ [
    {"step":1,"role":"tool","tool_name":"list_targets","status":"ok","latency_ms":12,"observation":"..."},
    {"step":2,"role":"tool","tool_name":"fetch_page","status":"ok","latency_ms":850,"observation":"..."},
    ...
  ]
```

---

## 九、前 4 周日任务

### W1（10/21 - 10/27）：骨架 + 表 + mock 站

| 日 | 任务 | 验收 |
|---|---|---|
| 10/21 | 建仓库 + 搬 `common/` `llm_service/` `config.py` `Dockerfile`；pip 装依赖 | `uvicorn main:app` 能起 |
| 10/22 | 写 5 张表 + `models/__init__.py`；建库 `crawler_agent` | phpMyAdmin 看到 5 张表 |
| 10/23 | **写 mock 站并跑起来** | 浏览器能打开 `:9000/products`，翻页正常，两个 toggle 生效 |
| 10/24 | `common/page_cache.py`（ref 模式） | 存入/取出/过期，单测通过 |
| 10/25 | `crawler/fetcher.py`（DrissionPage 双模） | 能抓 mock 站，返回 dict |
| 10/26 | 4 个工具实现 + `agent/schemas.py` + 工具注册表 | 单测：手动调 4 个工具，从 url 走到落库 |
| 10/27 | **`/targets` CRUD + 手动插一条 target** | curl 能查到 target |

**W1 里程碑**：**手动调工具能完成一次完整抓取**（不经过 LLM）。

### W2（10/28 - 11/3）：Agent 循环

| 日 | 任务 | 验收 |
|---|---|---|
| 10/28 | `agent/prompts.py` + `agent/loop.py` 骨架 | 能跑一次 LLM 调用，拿到 tool_calls |
| 10/29 | 循环 + 工具执行 + 回填 | **Agent 能自主完成一次抓取**，控制台打印思考链 |
| 10/30 | 三处防呆：max_steps / 重复调用打断 / 错误返回 | 故意让它死循环，能被打断 |
| 10/31 | `save_trace()` + summarize | traces 表有完整步骤 |
| 11/1 | `routers/agent.py`（SSE 端点） | curl -N 能看到流式事件 |
| 11/2 | 桥段 2 测试：切 break-selector | **Agent 自己换选择器成功** |
| 11/3 | 桥段 3 测试：切 rate-limit | Agent 识别 429 并报告 |

**W2 里程碑**：**Agent 端到端能抓通 mock 站，三个桥段全部通过。**

### W3（11/4 - 11/10）：可观测性

| 日 | 任务 | 验收 |
|---|---|---|
| 11/4 | `routers/traces.py`（回放端点） | 能拉出完整执行链路 |
| 11/5 | 补复合索引 `(thread_id, step)` + 迁移 | `SHOW INDEX` 看到 |
| 11/6 | 统计端点：按 tool_name 统计调用次数/耗时 | 返回聚合数据 |
| 11/7 | 失败分析：查所有 status=error 的 trace | 能定位失败步骤 |
| 11/8 | 错误注入测试（断网/错选择器/429） | 三种情况都有正确 trace |
| 11/9 | pytest 补齐（工具 8 个用例 + 循环 4 个） | 全绿 |
| 11/10 | GitHub Actions CI 跑通 | 绿勾 |

**W3 里程碑**：**执行链路可回放、可统计、有测试和 CI。**

### W4（11/11 - 11/19）：前端

| 日 | 任务 | 验收 |
|---|---|---|
| 11/11 | `static/index.html` 骨架：输入框 + 按钮 + 两个列表区 | 页面能打开 |
| 11/12 | **`fetch` + `ReadableStream` 解析 SSE**（不能用 EventSource，要 POST） | 控制台能收到事件 |
| 11/13 | 思考链渲染：每条事件 append 一个 DOM 节点 | **逐条出现，不是一次性** |
| 11/14 | 结果表格渲染 | 抓到数据能显示 |
| 11/15 | 样式（简洁即可，别花时间） | 能看 |
| 11/16 | 错误态展示（blocked / rate_limited） | 红色提示 |
| 11/17 | 回放页面：输入 thread_id 拉历史 | 能看到历史轨迹 |
| 11/18 | 联调 + 修 bug | 全流程通 |
| 11/19 | **★ 录屏演示（三个桥段）** | **能给别人看** |

**W4 里程碑（红线）：11/19 前前端能看到流式思考链。做不到就砍功能。**

---

## 十、MVP 验收标准（7 条全绿才算完成）

| # | 能力 | 验证方式 |
|---|---|---|
| 1 | 自然语言指令 | `POST /agent/run {"instruction":"抓任务1的所有目标","task_id":1}` |
| 2 | Agent 自主调 ≥2 工具 | traces 里有 `fetch_page` → `parse` → `save` |
| 3 | **SSE 实时推思考链** | 前端**逐条**出现，不是卡住后一次性 |
| 4 | 数据落 MySQL | `records` 表有数据，`url_hash` 去重生效 |
| 5 | **traces 可回放** | `GET /traces/{id}` 返回完整步骤 + 耗时 |
| 6 | 前端能演示 | 打开页面输入指令，看到流式思考 + 结果表格 |
| 7 | `docker compose up` 一条命令起全套 | app + mysql（+ redis） |

### 加分项（MVP 之后）

- Redis 任务队列 + 多 worker
- 多轮对话（thread_id + 上下文压缩）
- 真实站点适配（1-2 个反爬弱的）
- JS 逆向签名工具（酷狗经验落地点）

---

## 十一、关键设计决策汇总（面试必讲）

| # | 决策 | 理由 | 面试话术 |
|---|---|---|---|
| 1 | **4 个工具，不是 1 个也不是 8 个** | 参数少模型好决策；失败可定位；可复用 | "我把采集拆成获取/解析/落库三个可独立失败的动作，加一个 list_targets 让 Agent 知道要抓什么" |
| 2 | **HTML 用 ref 传递，绝不进 messages** | 48K 字符爆 context；模型没法传长字符串 | "fetch_page 返回的是服务端缓存的 ref，模型只看到 200 字符的摘要，用 ref 调用后续工具" |
| 3 | **一个 step 一行 trace** | 可单独查询统计；不嵌套 JSON | "我做了一个 step 一行，配合 (thread_id, step) 复合索引，回放时能顺序读" |
| 4 | **`tool_name` 与 `action` 冗余** | JSON 字段无法索引 | "为了能查'所有 fetch_page 调用'，我冗余存了 tool_name 单独建索引，这是为查询做反范式" |
| 5 | **空选择器返回 html_snippet** | 模型靠片段推断正确选择器 | "解析失败时我返回 500 字符 HTML 片段，Agent 能从里面看出真实的 class 名" |
| 6 | **验证码检测后停止，不破解** | 合规 + 硬刚会加重风控 | "检测到 geetest 特征就返回 blocked，Agent 停止并告警，我的策略是降频而不是破解" |
| 7 | **mock 站做回归测试** | 演示不依赖外部网站 | "我准备了 mock 站点做回归，可以模拟选择器失效和 429，真实站点做集成验证" |
| 8 | **同步 SDK 丢线程池** | async 路由不能阻塞 event loop | "SSE 路由必须 async，所以 DB 和 SDK 调用都用 run_in_threadpool，Session 在线程内创建" |

---

## 十二、风险与解药

| # | 风险 | 解药 |
|---|---|---|
| 1 | Agent 死循环 | max_steps=12 + 连续 3 次同调用打断 |
| 2 | 模型选错工具 | description 写"何时不用" + 桥段 2/3 验证 |
| 3 | 选择器失效 | 返回 html_snippet 让它重试（最多 2 次） |
| 4 | token 爆炸 | ref 模式 |
| 5 | traces 表爆表 | observation 只存摘要 |
| 6 | 演示时外部站挂 | mock 站保底 |
| 7 | 进度落后 | **11/19 红线，砍功能不延期** |

---

## 十三、明确不做的（写下来防止自己加）

- ❌ 通用爬虫（任意 URL）
- ❌ 滑块 / 点选验证码
- ❌ 登录态 / 会员内容
- ❌ 多 Agent 协同
- ❌ 分布式 / 多机部署
- ❌ 完整电商门户前端（列表/详情/购物车）
- ❌ Kafka / RabbitMQ（Redis List 够用）
- ❌ K8s

---

## 十四、下一步（今天/明天）

```
1. ☐ 建 GitHub 仓库 crawler-agent（private）
2. ☐ 本地 git init，搬 common/ llm_service/ config.py Dockerfile
3. ☐ pip install fastapi uvicorn sqlmodel pymysql openai tenacity pydantic-settings drissionpage
4. ☐ 建库：CREATE DATABASE crawler_agent DEFAULT CHARACTER SET utf8mb4;
5. ☐ 写 mock_site/main.py，跑起来看能不能翻页
```

**跑完第 5 步，你就"开工"了。**

---

*定稿 v1.0 · 2026-10-20 · 后续变更记入 CHANGELOG*
