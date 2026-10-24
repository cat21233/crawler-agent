# crawler-agent 设计定稿 v1.0 审查报告

> 审查时间：2026-10-20 23:15
> 审查对象：`crawler-agent-设计定稿.md`（836 行）
> 审查方式：对照 day01 真实代码逐条核对（`db.py` / `db_models.py` / `llm_service/client.py` / `llm_service/tool_calling.py` / `requirements.txt` / venv site-packages）
> 结论：**设计思路可用，不要重写。但有 10 处会让 W1/W2 直接卡住的实现级错误，改完再开工。**

---

## 总评

**写得好的**（面试真能讲）：
- ref 模式（HTML 不进 messages）——理由说到了"模型物理上传不回去"这一层，是对的
- 4 工具 = 4 个决策点，不是"分步"
- `observation` 只存摘要、`tool_name` 与 `action` 冗余（为可索引做反范式）
- 三层防线 + 每步 SSE 事件

**问题集中在一处**：伪代码 → 可运行代码之间的那层。类型、索引、字段提取规则、异步边界。**这类错误在设计评审时看不出来，会在写代码第 2 天集中爆炸。**

---

## P0 —— 不改就卡在 W1（5 条）

### P0-1 ★ SQLModel ≠ day01 的 SQLAlchemy 2.0（最致命）

| 项 | 定稿写的 | 实际情况 |
|---|---|---|
| ORM 选型 | `class Thread(SQLModel, table=True)` | day01 用的是 **SQLAlchemy 2.0 声明式**：`class User(Base)` + `Mapped` / `mapped_column`（`db_models.py:17-48`） |
| 依赖 | 「MySQL + SQLModel \| day01 已验证」 | ❌ **day01 验证的不是 SQLModel**。`sqlmodel` **未安装**：`requirements.txt` 只有 `SQLAlchemy==2.0.55`，venv `site-packages` 里没有 |
| 建表 | 没提 | 🔴 **`db.py` 的 `init_db()` 走 `Base.metadata.create_all()`**。SQLModel 的表注册在 `SQLModel.metadata`，**不会进 `Base.metadata`** |

**后果**：照搬 `db.py` + 写 SQLModel 表 → `init_db()` 返回成功、**一张表都不建** → 第一个请求报
`(1146, "Table 'crawler_agent.threads' doesn't exist")`。而且它不报错，是**静默失败**，你会以为连库有问题去查配置，浪费半天。

**建议：方案 A（跟 day01 一致，用 SQLAlchemy 2.0）**

理由：① `db.py` 零改动直接复用；② 你刚吃透 `Mapped`，不用再学一套 API；③ SQLModel 的复合索引写法更绕；④ 面试讲一套技术栈比讲两套清楚。

改前 / 改后：

```python
# ❌ 改前（定稿写法，需要装 sqlmodel + 改 db.py 的 metadata）
class Thread(SQLModel, table=True):
    __tablename__ = "threads"
    id: int | None = Field(default=None, primary_key=True)
    thread_id: str = Field(index=True, unique=True, max_length=64)
```

```python
# ✅ 改后（SQLAlchemy 2.0，与 db_models.py 同一套）
from datetime import datetime
from sqlalchemy import DateTime, Index, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column
from db import Base


class Thread(Base):
    __tablename__ = "threads"

    thread_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    title: Mapped[str] = mapped_column(String(200), default="")
    user_id: Mapped[int | None] = mapped_column(default=None, index=True)
    status: Mapped[str] = mapped_column(String(20), default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"}
```

**顺带解决 P2-4**：`thread_id` 直接当主键，去掉没人引用的 `id`。

---

### P0-2 ★ `__table_args__` 是 dict，塞不进复合索引

定稿 4.3 节的索引表格写着「**复合 `(thread_id, step)`** ★ 最常用」，但 `AgentTrace` 类里**没有任何复合索引**——`step` 字段连单列索引都没有。文档自己跟自己对不上。

而且 day01 的 `__table_args__` 是 **dict 形式**（`db_models.py:45`）：

```python
__table_args__ = {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"}
```

**dict 里不能混 `Index(...)`**，会直接报错或静默丢掉索引。加复合索引必须改成 **tuple，且 dict 放最后一个元素**：

```python
# ✅ 正确写法
from sqlalchemy import Index

class AgentTrace(Base):
    __tablename__ = "agent_traces"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    thread_id: Mapped[str] = mapped_column(String(64), index=True)
    step: Mapped[int] = mapped_column(default=0)
    # ... 其他字段

    __table_args__ = (
        Index("ix_trace_thread_step", "thread_id", "step"),   # ★ 复合索引
        {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"},  # dict 必须在最后
    )
```

**另外**：一个 step 可能产出**多条** tool trace（模型一次返回多个 `tool_calls`）。所以回放查询排序必须带 `id` 兜底：

```sql
ORDER BY step, id
```

只用 `(thread_id, step)` 排序，同一步的多行顺序不确定，回放会串。

---

### P0-3 ★ `parse_by_selector` 的字段提取规则，文档一个字没写

`fields: ["title", "price", "url"]` 看起来通用，实际是**假通用**——代码拿到 `.product-item` 这个元素后，**怎么知道 title 在哪个子节点？**

mock 站（定稿第 7 节）的实际结构：

```html
<div class="product-item" data-id="1">
  <h3 class="title">商品 1：Python 实战教程第 1 版</h3>   <!-- title = 文本 -->
  <span class="price">¥109</span>                        <!-- price = 文本 -->
  <a class="detail" href="/product/1">详情</a>            <!-- url = href 属性！ -->
</div>
```

**四个字段四种取法**：`title` 取 `.title` 的文本、`price` 取 `.price` 的文本、`url` 取 `a` 的 **`href` 属性**（不是文本）。这个映射表现在只存在于你脑子里，写代码时才发现"`fields` 参数没用上"。

**两个选项，必须选一个**：

**选项 1（省事，MVP 够用）**：在文档里写死约定，代码硬编码

```python
# crawler/parser.py
FIELD_RULES = {
    "title": (".title", "text"),
    "price": (".price", "text"),
    "url":   ("a", "href"),
    "text":  (None, "text"),      # None = 取元素自身文本
}
```

然后 `fields` 参数只做**过滤**（传了哪几个就返哪几个），不做"怎么取"。**必须在设计文档里明说这一点**——否则面试官问「换个站怎么办」，你答"改 FIELD_RULES"，那就退化成手写爬虫了。

**选项 2（通用，多花 2 小时）**：把字段级选择器存进 `targets` 表

```python
# targets 表把 fields 从 "title,price,url" 改成 JSON
field_map: Mapped[str] = mapped_column(
    Text, default='{"title": ".title", "price": ".price", "url": "a@href"}'
)
```

工具签名改成 `parse_by_selector(ref, selector, field_map)` → 真的可以换站不改代码。

**建议选 2**，因为它顺带解决 P2-3（表里存 str、工具返 list 的类型不一致），而且「目标配置里带字段选择器」是面试可以讲的抽象。**但如果你只有 2.5h/天，选 1，然后在文档里老实写清楚边界。**

---

### P0-4 ★ `save_records.items` 是裸 object，参数校验那层是空的

```python
"items": {
    "type": "array",
    "description": "条目数组，每项至少含 title",
    "items": {"type": "object"},      # ← 没定义 properties
},
```

`{"type": "object"}` 等于**告诉模型"随便传"**。真实风险：

- 模型传 `{"name": "商品1", "价格": "109"}`（换字段名 / 中文 key）
- 工具里 `Record(title=item["title"])` → **KeyError**，整个 Agent 崩
- 或者传入 `[{"title": ""}]` 空标题 → 脏数据落库

定稿 3.1 原则写了「返回结构化 dict，不抛异常」，但**入参没做校验**——三层防线的第二层（代码约束）在最重要的写库工具上是缺的。

```python
# ✅ 改后：Schema 定义清楚 + 工具内部兜一层
"items": {
    "type": "array",
    "minItems": 1,
    "items": {
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "商品标题，必填"},
            "price": {"type": "string", "description": "价格，可含货币符号"},
            "url":   {"type": "string", "description": "详情页链接"},
        },
        "required": ["title"],          # ★ 关键
    },
},
```

```python
# 工具内部（第二层防线，不能只靠 Schema）
items = [i for i in raw_items
         if isinstance(i, dict) and str(i.get("title", "")).strip()]
if not items:
    return {"status": "error", "reason": "没有一条带 title 的有效数据",
            "suggest": "确认 parse 结果的字段名是 title"}
```

**注意**：不要把 `items` 原样塞回 observation（12 条 × 3 字段就已经几千字符）。返回 `{"status":"ok","new":7,"skipped":5,"total":12}` 就够——**这跟 trace 里只存摘要的原则是一致的**。

---

### P0-5 ★ `utcnow` 没定义，第一次 import 就 NameError

```python
created_at: datetime = Field(default_factory=utcnow)   # ← utcnow 从哪来？
```

全文 5 张表都用了 `utcnow`，但**没有任何一节定义或 import 它**。

而且 day01 的做法不一样（`db_models.py:39`）：

```python
created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
```

**两种做法差别不是风格问题**：
- `server_default=func.now()` → **数据库填**。多进程/多机器部署时时间统一，且 INSERT 少一个字段
- `default_factory=utcnow` → **Python 填**。机器时钟不一致就会乱；而且 aware datetime 存进 MySQL `DATETIME` 会丢时区（`TIMESTAMP` 才保留）

**建议统一用 `server_default=func.now()`**，跟 day01 一致，少一类时区坑。如果确实要 Python 侧控制，就显式写好：

```python
from datetime import datetime, timezone

def utcnow() -> datetime:
    """返回 naive UTC 时间（MySQL DATETIME 不存时区，统一成 naive 更安全）。"""
    return datetime.now(timezone.utc).replace(tzinfo=None)
```

---

## P1 —— W2 会撞（5 条）

### P1-1 `run_in_threadpool` 用错对象，真正的阻塞点反而没包

定稿「三处关键设计」第一条：`run_in_threadpool(lambda: llm.chat_with_tools(...))` —— 理由是"同步 SDK 丢线程池"。

**但 day01 的 `llm_service/client.py` 是 async 的**：

```python
async def one(self, prompt, system=None):
    resp = await self._client.chat.completions.create(...)   # client.py:53
```

`tool_calling.py:45` 也是直接 `await client.chat.completions.create(...)`，**没有 threadpool**。

所以复用 `llm_service` 之后：
- LLM 调用 → 直接 `await`，**不需要** `run_in_threadpool`
- 真正的阻塞点是 **`save_trace()` 同步写 DB**（每步一次 INSERT + commit），以及 **DrissionPage 的同步抓取** —— 这两个**反而没被丢进线程池**

```python
# ✅ 改后
resp = await llm.chat_with_tools(messages, TOOLS)                    # async client，直接 await
result = await run_in_threadpool(execute_tool, name, args, factory)   # 工具里有同步 IO，要包
await run_in_threadpool(save_trace_sync, ...)                        # ★ trace 写库也要包
```

**顺带**：`fetch_page` 用 DrissionPage 是**阻塞**的，渲染模式下可能 3-10 秒。这一个工具就能把整个事件循环卡住 —— 所有并发的 SSE 连接一起停住。**必须 `run_in_threadpool`，且 `asyncio.wait_for` 加超时**（定稿的三层防线表里提了 30s 超时，但伪代码里没有）。

---

### P1-2 `latency_ms` 存的是模型的耗时，不是工具的

伪代码里 `latency` 在**模型调用后**计算（`t0` 到 `msg` 之间），然后 tool 的 trace 里写成 `latency_ms=latency`。

但定稿第 8 节的回放示例是：

```
{"step":2,"role":"tool","tool_name":"fetch_page","status":"ok","latency_ms":850}
```

850ms 明显是抓页面的耗时。**实现跟示例矛盾。**

**后果**：traces 是你简历的核心卖点（"全程可观测"）。面试官问「你 fetch_page 平均多久」，你打开表一查，全是模型的 800ms，**数据是假的**。

```python
# ✅ 改后：tool 的 trace 用自己的计时
t_tool = time.time()
result = await run_in_threadpool(execute_tool, name, args, factory)
tool_latency = int((time.time() - t_tool) * 1000)

save_trace(..., tool_name=name, latency_ms=tool_latency)      # 工具耗时
# 模型那条 trace 单独记 model_latency
```

**这一步做完，你就能在面试里报真实分位数**（fetch 的 P50/P95、模型调用的 P50）——这是"可观测性"唯一有说服力的形态。

---

### P1-3 闸 1 用 `finish_reason == "stop"`，不如 `if not msg.tool_calls`

```python
if resp.choices[0].finish_reason == "stop":      # ❌ 依赖厂商实现细节
    ...
```

- 有些厂商（含部分网关）返回 `finish_reason=None` 或 `"tool_calls"`
- 更糟：`finish_reason` 异常但 `tool_calls` 为空列表 → `for tc in []` 什么都不做 → **空转直到 max_steps**
- 若 `finish_reason=="tool_calls"` 但 content 有内容 → 内容被丢弃

day01 的写法是对的（`tool_calling.py:50`）：

```python
if not msg.tool_calls:                 # 情况1：模型没要工具 → 停
    return False
```

**判据：判断"要不要继续"看工具调用，不看停止原因。** `finish_reason` 是给日志看的，不是给控制流用的。

---

### P1-4 `max_steps = 12` 会误杀正常流程

prompt 写的规则：每个目标最多重试 2 次。算一下最坏情况：

```
2 个目标 × (fetch + 1次渲染重试 + parse + 1次换选择器重试 + save) = 2 × 5 = 10 步
+ list_targets 1 步 + 收尾 1 步 = 12 步
```

**恰好卡在 12。** 只要多一个目标、或某次重试多走一步，就被 `max_steps` 掐死，而它其实在正常工作。

```python
# ✅ 改后：按目标数动态算，且留余量
max_steps = min(30, 8 * max(1, len(targets)) + 4)
```

同时把 `max_steps` 的触发**区分成两种状态**：正常用完（`status="max_steps"`）≠ 死循环（`status="loop"`）。两者在 traces 里要分开统计，否则面试讲"我怎么防死循环"会把正常结束也算进去。

---

### P1-5 `event: thought` 在契约里，循环里没有

第 8 节 SSE 协议列了：

```
event: thought
data: {"content":"需要先获取第1个目标的页面"}
```

但第 6 节伪代码只 yield `start` / `action` / `observation` / `final` / `error`，**从来没有 `thought`**。

**后果直接打在 W4 的红线上**（「11/19 必须能看到流式思考链」）——前端等着 thought 事件，后端不产。

FC 模式下模型**可以在返回 tool_calls 的同时输出 content**（DeepSeek、智谱都支持）。所以：

```python
# ✅ 在闸 2 之前加
if msg.content:                                  # 模型说了话（哪怕同时要调工具）
    yield {"event": "thought", "data": {"content": msg.content}}
    save_trace(..., role="assistant", thought=msg.content, status="ok",
               latency_ms=model_latency)
```

**但要有预期**：模型不一定每步都说话。如果它连着两步只调工具不出声，前端就是空白 —— **前端要做"骨架占位"**，不能干等。

---

## P2 —— 契约/排期，早改便宜（4 条）

### P2-1 ★ EventSource 发不了 POST

| 定稿写的 | 冲突 |
|---|---|
| 技术选型：「前端 原生 HTML + JS + **EventSource**」 | `EventSource` **只支持 GET**，且不能自定义 header |
| 第 8 节：「**POST** /agent/run，body 带 instruction」 | 前端根本发不出去 |

三个解法：

| 方案 | 代价 | 建议 |
|---|---|---|
| **`fetch()` + `ReadableStream` 手写 SSE 解析** | 30 行 JS，要自己按 `\n\n` 切事件 | ✅ **推荐**，零依赖 |
| 端点改成 `GET /agent/run?instruction=...` | 指令长会超 URL 长度，中文要 encode | 可用但不优雅 |
| `@microsoft/fetch-event-source` | 引 npm，跟"不上框架"冲突 | 不推荐 |

**这条现在改零成本，W4 才发现要重写前端。**

### P2-2 mock 站的 next_url 是相对路径，`fetch_page` 要求绝对 URL

```python
next_link = f'<a class="next" href="/products?page={page+1}">下一页</a>'   # 相对路径
```

但 `fetch_page` 的 Schema 写着「完整 URL，含 http:// 或 https://」。Agent 拿到 `/products?page=2` 直接喂进去 → 抓取失败。

**必须在 `fetch_page` / 翻页检测里 `urljoin`**：

```python
from urllib.parse import urljoin
next_url = urljoin(current_url, raw_next_href)     # → http://localhost:9000/products?page=2
```

顺带：定稿 3.3 的 `fetch_page` 返回里**没有 `next_url` / `has_next` 字段**，但第 8 节 SSE 示例里也没有。**翻页能力目前是悬空的** —— 归档 Q3 讨论出的「必须给完整 next_url」没落进 Schema。补两个字段。

### P2-3 `list_targets` 返回 `fields` 是 list，表里存的是逗号字符串

| 位置 | 类型 |
|---|---|
| `Target.fields`（表） | `str`，`"title,price,url"` |
| `list_targets` 返回示例 | `["title","price"]`（list） |
| `parse_by_selector` 入参 | `array of string` |

三处三种形态，中间要转。**跟着 P0-3 选方案 2（field_map 用 JSON）就一起解决了**，否则至少写清楚转换点在哪。

### P2-4 排期：把「最小前端」从 W4 提前到 W2 末

定稿 W4（11/11-11/19）才做前端，红线是 11/19 能看到流式思考链。

**建议 W2 最后一天做 20 行最小前端**：一个 HTML + 50 行 JS（fetch + ReadableStream），**能在控制台打印出事件流就行**，不做样式。

理由：SSE 是**前后端契约**。W2 写完 loop，你手上只有 curl 的输出，看不出"事件顺序对不对、前端能不能边收边渲染"。等 W4 才发现协议要改，loop 得返工——那是你最贵的一周。

---

## 明天开工顺序（修订版）

原计划的五步（建仓库 → 搬 common/llm_service → 建库 → mock 站）**顺序不变**，但插入三件事：

```
① 先改掉定稿的 P0-1（SQLModel → SQLAlchemy 2.0）  ← 15 分钟，改文档 + 建表代码
② 建仓库、搬 common/ + llm_service/（db.py 不动）
③ 建库（CREATE DATABASE crawler_agent utf8mb4）
④ 写 5 张表（SQLAlchemy 2.0 写法，带复合索引）
   ★ 验收不是"建成了"，是 `inspect(engine).get_table_names()` 返回 5 张表
   ★ 且 `SHOW CREATE TABLE agent_traces` 里能看到 ix_trace_thread_step
⑤ 写 mock 站（原样可用，只要补 urljoin 那处）
```

**W1 的验收标准建议加一条**（原定稿只有「手动调工具能完成一次抓取」）：

> **`SELECT tool_name, latency_ms FROM agent_traces` 能查出真实耗时，且 fetch_page 的 latency 明显大于 list_targets 的。**
> 这条过不了，说明 P1-2 的计时问题没修，traces 卖点是假的。

---

## 一句话

**定稿的"想清楚"部分已经够了，缺的是"写下去会不会崩"那层。把 P0 五条改完再动手，W1 不会卡；不改，第一天就在建表上浪费半天。**
