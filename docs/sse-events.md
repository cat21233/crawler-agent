# SSE 事件契约（POST /agent/run）

> 定稿：2026-11-11（c-071）。前端（11-14 W4-A）按此消费，后端改动必须同步本文档。

## 端点

```
POST /agent/run
Content-Type: application/json
→ 200, Content-Type: text/event-stream
```

请求体：

| 字段 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `input` | string | ✅ | — | 自然语言指令 |
| `max_turns` | integer | ❌ | 8 | 最多几轮模型往返；到顶会返回「已达到最大轮数」 |

响应头里带 `Cache-Control: no-cache` 与 `X-Accel-Buffering: no`（后者给 nginx 用，别让它缓冲）。

## 报文格式

```
event: <类型>
data: <一行 JSON>

```

每条消息以**空行**结束。`data` 里始终有 `type` 字段，值与 `event:` 行相同（冗余是刻意的：`EventSource.onmessage` 拿不到 event 名时靠它兜底）。

## 事件类型

| type | 何时发 | 关键字段 | 前端怎么用 |
|---|---|---|---|
| `start` | 请求一进来 | `thread_id` | 存下来，用于事后查 `GET /traces/{thread_id}` |
| `tool_calls` | 模型这一轮决定调工具 | `step`, `names[]` | 渲染「第 N 轮，要调 X/Y」 |
| `tool_start` | 每个工具执行前 | `step`, `name`, `args` | 该工具显示 loading |
| `tool_end` | 每个工具执行完 | `step`, `name`, `ok`, `latency_ms`, `preview` | 结束 loading，显示耗时与成败 |
| `tool_error` | 模型吐了非法 JSON | `step`, `name`, `error` | 与 `tool_end(ok=false)` 分开显示：这是**模型**的错，不是工具的错 |
| `answer` | 模型给出最终回答 | `content` | 渲染最终答案（markdown） |
| `done` | 正常结束 | （无） | 关闭连接、停 spinner |
| `error` | 服务端异常 | `error` | 显示错误，停 spinner |

## 三条设计约定

1. **`answer` 带内容，`done` 不带**。一个事件只承担一个职责；否则前端要处理重复文本。
2. **`step` 是模型往返次数**，不是工具序号。一轮里模型可能并列调多个工具，它们的 `step` 相同（与 `agent_traces` 表的 `step` 语义一致，回放时靠 `id` 定序）。
3. **`preview` 截断到 200 字符；`args` 里的长值折叠成「规模」**。
   字符串 >60 字符显示 `<N 字符的字符串>`，列表显示 `<N 项列表>`，超长字典显示 `<N 键字典>`。
   要完整参数或结果请用 `thread_id` 查 `/traces/{thread_id}` 的 `action` / `observation`。
   事件流只负责「让人看见在动」，不承担「完整数据搬运」。
   （11-16 补：原文档只规定了 `preview`，漏了 `args` —— `save_records` 的 5 条商品列表直接把前端事件流撑成 10 行。）

## 已知限制（前端要接受）

- **单进程**：`page_cache` 的 ref 只活在一个进程的内存里。多 worker 部署时 ref 会失效，届时需换 Redis。
- **无重连语义**：`Last-Event-ID` 没实现。断线后重连等于重新跑一遍任务，不是续传。
- **同步 DB 写会阻塞事件循环**：`save_trace` 是同步调用，写库那几毫秒里其他请求（含正在推流的）会被卡住。单用户场景可接受，并发场景要改 `asyncio.to_thread`。

## 客户端示例

```js
const resp = await fetch("/agent/run", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ input: "抓取 target 1 的商品并保存" }),
});
const reader = resp.body.getReader();
const dec = new TextDecoder();
let buf = "";
while (true) {
  const { value, done } = await reader.read();
  if (done) break;
  buf += dec.decode(value, { stream: true });
  const parts = buf.split("\n\n");
  buf = parts.pop();                      // 最后一段可能不完整，留到下次
  for (const block of parts) {
    const line = block.split("\n").find((l) => l.startsWith("data: "));
    if (!line) continue;
    const ev = JSON.parse(line.slice(6));
    handle(ev);                           // 按 ev.type 分发
  }
}
```

⚠️ 用 `fetch` + `ReadableStream` 而不是 `EventSource`：**`EventSource` 只支持 GET，发不了 POST 的 JSON body**。这是前端最容易踩的一脚，`c-081` 费曼会考。
